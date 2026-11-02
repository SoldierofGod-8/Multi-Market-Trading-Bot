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
