"""Historical replay of the same strategy and sizing rules.

Assumptions (see README "Backtest assumptions"):
- Decisions use the close of a closed bar; entries fill at that close plus slippage.
- Stops/targets are checked against each later bar's high/low; if both are touched in
  one bar the stop is assumed to fill first (conservative).
- Fees and slippage as in paper mode. No spread model beyond slippage.
- The daily-loss and max-drawdown breakers are NOT simulated here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config, StrategyConfig
from .risk import position_size
from .stats import compute_stats, periods_per_year
from .strategy import signal_frame


@dataclass
class Trade:
    entry_time: object
    exit_time: object
    side: int
    entry: float
    exit: float
    qty: float
    pnl: float
    reason: str
    bars: int


@dataclass
class Report:
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    in_market: list = field(default_factory=list)
    start_equity: float = 0.0
    index: object = None
    buy_hold_return: float = 0.0
    ppy: float = 252.0

    def summary(self) -> dict:
        pnls = np.array([t.pnl for t in self.trades], dtype=float)
        bars = np.array([t.bars for t in self.trades], dtype=float)
        return compute_stats(np.array(self.equity_curve), pnls, bars,
                             np.array(self.in_market, dtype=float), self.start_equity,
                             self.buy_hold_return, self.ppy)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


def run_backtest(df: pd.DataFrame, s: StrategyConfig, cfg: Config,
                 allow_short: bool = True, market: str = "forex") -> Report:
    fee, slip = cfg.paper_fee_rate, cfg.paper_slippage_bps / 10000
    sig = signal_frame(df, s)
    side_arr, cross_arr, atr_arr = sig["side"].values, sig["cross"].values, sig["atr"].values
    close, high, low = df["close"].values, df["high"].values, df["low"].values
    idx = df.index
    cash = cfg.starting_equity
    rep = Report(start_equity=cash, index=idx,
                 buy_hold_return=float(close[-1] / close[0] - 1) if len(close) else 0.0,
                 ppy=periods_per_year(s.timeframe, market))
    pos = None
    warm = s.ema_trend + 2
    for i in range(len(df)):
        if i < warm:
            rep.equity_curve.append(cash)
            rep.in_market.append(0)
            continue
        exited = False
        if pos is not None:
            exit_px, reason = None, None
            if pos["side"] == 1:
                if low[i] <= pos["sl"]:
                    exit_px, reason = pos["sl"], "stop"
                elif high[i] >= pos["tp"]:
                    exit_px, reason = pos["tp"], "target"
            else:
                if high[i] >= pos["sl"]:
                    exit_px, reason = pos["sl"], "stop"
                elif low[i] <= pos["tp"]:
                    exit_px, reason = pos["tp"], "target"
            if exit_px is None and cross_arr[i] == -pos["side"]:
                exit_px, reason = close[i], "signal_exit"
            if exit_px is not None:
                px = exit_px * (1 - pos["side"] * slip)
                exit_fee = px * pos["qty"] * fee
                pnl = (px - pos["entry"]) * pos["side"] * pos["qty"] - exit_fee - pos["fee"]
                cash += pnl
                rep.trades.append(Trade(pos["time"], idx[i], pos["side"], pos["entry"], px,
                                        pos["qty"], pnl, reason, i - pos["i"]))
                pos = None
                exited = True  # like the live engine: no re-entry on the exit bar
        if pos is None and not exited:
            sd = side_arr[i]
            if sd != 0 and (sd == 1 or allow_short):
                price = close[i]
                stop_dist = s.stop_atr * atr_arr[i]
                qty = position_size(cash, price, stop_dist, cfg.risk.risk_per_trade,
                                    cfg.risk.max_position_fraction)
                if qty > 0:
                    entry = price * (1 + sd * slip)
                    pos = {"side": sd, "qty": qty, "entry": entry, "time": idx[i], "i": i,
                           "sl": price - sd * stop_dist,
                           "tp": price + sd * s.tp_atr * atr_arr[i],
                           "fee": entry * qty * fee}
        # mark-to-market equity at this bar's close
        if pos is not None:
            unreal = (close[i] - pos["entry"]) * pos["side"] * pos["qty"]
            rep.equity_curve.append(cash + unreal)
            rep.in_market.append(1)
        else:
            rep.equity_curve.append(cash)
            rep.in_market.append(0)
    return rep
