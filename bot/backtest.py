"""Historical replay of the same strategy and risk rules (paper fills, bar-level stops)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config, StrategyConfig
from .risk import position_size
from .strategy import evaluate


@dataclass
class Trade:
    entry_time: object
    exit_time: object
    side: int
    entry: float
    exit: float
    pnl: float
    reason: str


@dataclass
class Report:
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    start_equity: float = 0.0

    def summary(self) -> dict:
        pnls = np.array([t.pnl for t in self.trades]) if self.trades else np.array([])
        eq = np.array(self.equity_curve) if self.equity_curve else np.array([self.start_equity])
        peak = np.maximum.accumulate(eq)
        mdd = float(((peak - eq) / peak).max()) if len(eq) else 0.0
        wins, losses = pnls[pnls > 0], pnls[pnls < 0]
        gross_loss = -losses.sum()
        end = float(eq[-1])
        return {
            "trades": len(pnls),
            "win_rate": float(len(wins) / len(pnls)) if len(pnls) else 0.0,
            "net_profit": end - self.start_equity,
            "return_pct": (end / self.start_equity - 1) * 100 if self.start_equity else 0.0,
            "profit_factor": _profit_factor(wins.sum(), gross_loss),
            "max_drawdown_pct": mdd * 100,
            "avg_trade": float(pnls.mean()) if len(pnls) else 0.0,
        }


def _profit_factor(gross_win: float, gross_loss: float) -> float:
    if gross_loss > 0:
        return float(gross_win / gross_loss)
    return float("inf") if gross_win > 0 else 0.0


def run_backtest(df: pd.DataFrame, s: StrategyConfig, cfg: Config,
                 allow_short: bool = True) -> Report:
    fee, slip = cfg.paper_fee_rate, cfg.paper_slippage_bps / 10000
    equity = cfg.starting_equity
    rep = Report(start_equity=equity)
    pos = None  # dict with side, qty, entry, sl, tp, time, fee
    warm = s.ema_trend + 2
    for i in range(warm, len(df)):
        bar = df.iloc[i]
        t = df.index[i]
        if pos is not None:
            exit_px, reason = None, None
            if pos["side"] == 1:
                if bar.low <= pos["sl"]:
                    exit_px, reason = pos["sl"], "stop"      # conservative: stop wins ties
                elif bar.high >= pos["tp"]:
                    exit_px, reason = pos["tp"], "target"
            else:
                if bar.high >= pos["sl"]:
                    exit_px, reason = pos["sl"], "stop"
                elif bar.low <= pos["tp"]:
                    exit_px, reason = pos["tp"], "target"
            sig = evaluate(df.iloc[: i + 1], s)
            if exit_px is None and sig.cross == -pos["side"]:
                exit_px, reason = bar.close, "signal_exit"
            if exit_px is not None:
                px = exit_px * (1 - pos["side"] * slip)
                exit_fee = px * pos["qty"] * fee
                pnl = (px - pos["entry"]) * pos["side"] * pos["qty"] - exit_fee - pos["fee"]
                equity += pnl
                rep.trades.append(Trade(pos["time"], t, pos["side"], pos["entry"], px, pnl, reason))
                pos = None
        else:
            sig = evaluate(df.iloc[: i + 1], s)
            if sig.side != 0 and (sig.side == 1 or allow_short):
                side, price = sig.side, bar.close
                stop_dist = s.stop_atr * sig.atr
                qty = position_size(equity, price, stop_dist, cfg.risk.risk_per_trade,
                                    cfg.risk.max_position_fraction)
                if qty > 0:
                    entry = price * (1 + side * slip)
                    fee_in = entry * qty * fee
                    pos = {"side": side, "qty": qty, "entry": entry, "time": t,
                           "sl": price - side * stop_dist,
                           "tp": price + side * s.tp_atr * sig.atr, "fee": fee_in}
        rep.equity_curve.append(equity)
    return rep
