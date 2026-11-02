"""Trend-following strategy: EMA crossover filtered by trend EMA and RSI.

Entry (long):  fast EMA crosses above slow EMA, close above trend EMA, RSI in long band.
Entry (short): mirror image.
Exit:          opposite raw EMA crossover (handled by the engine).
Stops/targets: ATR multiples set by the engine at entry.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .indicators import atr, ema, rsi


@dataclass(frozen=True)
class Signal:
    side: int      # +1 long entry, -1 short entry, 0 none (after filters)
    cross: int     # raw EMA crossover direction, used for exits
    atr: float
    reason: str


NO_SIGNAL = Signal(0, 0, 0.0, "none")


def evaluate(df: pd.DataFrame, p: StrategyConfig) -> Signal:
    """Evaluate the LAST row of df, which must be a closed bar."""
    if len(df) < p.ema_trend + 2:
        return Signal(0, 0, 0.0, "warmup")
    c = df["close"]
    f, s, t = ema(c, p.ema_fast), ema(c, p.ema_slow), ema(c, p.ema_trend)
    r, a = rsi(c, p.rsi_period), atr(df, p.atr_period)
    now = [x.iloc[-1] for x in (f, s, t, r, a, c)]
    prev_f, prev_s = f.iloc[-2], s.iloc[-2]
    if not np.all(np.isfinite(now)) or not np.isfinite(prev_f) or not np.isfinite(prev_s):
        return Signal(0, 0, 0.0, "nan")
    f_now, s_now, t_now, r_now, a_now, c_now = now

    cross_up = prev_f <= prev_s and f_now > s_now
    cross_dn = prev_f >= prev_s and f_now < s_now
    cross = 1 if cross_up else (-1 if cross_dn else 0)
    if a_now <= 0:
        return Signal(0, cross, 0.0, "zero_atr")

    side, reason = 0, "no_cross"
    if cross_up and c_now > t_now and p.rsi_long_min <= r_now <= p.rsi_long_max:
        side, reason = 1, "ema_cross_up"
    elif cross_dn and c_now < t_now and p.rsi_short_min <= r_now <= p.rsi_short_max:
        side, reason = -1, "ema_cross_down"
    elif cross != 0:
        reason = "cross_filtered"
    return Signal(side, cross, float(a_now), reason)


def signal_frame(df: pd.DataFrame, p: StrategyConfig) -> pd.DataFrame:
    """Vectorised version of evaluate() for every bar at once (used by the backtester).

    Every indicator is causal (uses only current and past bars), so row i here equals
    evaluate(df.iloc[:i+1], p). tests/test_core.py checks that parity.
    """
    c = df["close"]
    f, s, t = ema(c, p.ema_fast), ema(c, p.ema_slow), ema(c, p.ema_trend)
    r, a = rsi(c, p.rsi_period), atr(df, p.atr_period)
    fp, sp = f.shift(1), s.shift(1)
    finite = np.isfinite(f) & np.isfinite(s) & np.isfinite(t) & np.isfinite(r) \
        & np.isfinite(a) & np.isfinite(fp) & np.isfinite(sp) & np.isfinite(c)
    warm = pd.Series(np.arange(len(df)) + 1 >= p.ema_trend + 2, index=df.index)
    valid = finite & warm
    cross_up = (fp <= sp) & (f > s) & valid
    cross_dn = (fp >= sp) & (f < s) & valid
    cross = np.where(cross_up, 1, np.where(cross_dn, -1, 0))
    long_ok = cross_up & (c > t) & (r >= p.rsi_long_min) & (r <= p.rsi_long_max) & (a > 0)
    short_ok = cross_dn & (c < t) & (r >= p.rsi_short_min) & (r <= p.rsi_short_max) & (a > 0)
    side = np.where(long_ok, 1, np.where(short_ok, -1, 0))
    return pd.DataFrame({"side": side, "cross": cross,
                         "atr": np.where(valid, a, 0.0)}, index=df.index)
