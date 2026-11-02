"""Performance statistics for backtests."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def periods_per_year(timeframe: str, market: str) -> float:
    """Bars per year. Crypto trades 24/7; forex and metals trade ~24/5."""
    unit_hours = {"m": 1 / 60, "h": 1.0, "d": 24.0, "w": 168.0}
    hours = int(timeframe[:-1]) * unit_hours[timeframe[-1].lower()]
    days = 365 if market == "crypto" else 252
    return days * 24 / hours


def compute_stats(equity: np.ndarray, trade_pnls: np.ndarray, trade_bars: np.ndarray,
                  in_market: np.ndarray, start_equity: float, buy_hold_return: float,
                  ppy: float) -> dict:
    eq = np.asarray(equity, dtype=float)
    rets = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0.0])
    n = len(eq)
    years = max(n / ppy, 1e-9)
    total = eq[-1] / start_equity - 1 if n else 0.0
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    sd = rets.std(ddof=1) if len(rets) > 1 else 0.0
    mean = rets.mean() if len(rets) else 0.0
    sharpe = mean / sd * math.sqrt(ppy) if sd > 0 else 0.0
    downside = rets[rets < 0]
    dd_sd = math.sqrt((downside ** 2).mean()) if len(downside) else 0.0
    sortino = mean / dd_sd * math.sqrt(ppy) if dd_sd > 0 else 0.0
    peak = np.maximum.accumulate(eq) if n else np.array([start_equity])
    dd = (peak - eq) / peak if n else np.array([0.0])
    mdd = float(dd.max()) if n else 0.0
    # longest drawdown in bars (time under water)
    under = dd > 0
    longest, cur = 0, 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    calmar = cagr / mdd if mdd > 0 else 0.0

    wins = trade_pnls[trade_pnls > 0]
    losses = trade_pnls[trade_pnls < 0]
    nt = len(trade_pnls)
    gross_loss = -losses.sum()
    pf = float(wins.sum() / gross_loss) if gross_loss > 0 else (float("inf") if len(wins) else 0.0)
    t_stat = (trade_pnls.mean() / (trade_pnls.std(ddof=1) / math.sqrt(nt))
              if nt > 1 and trade_pnls.std(ddof=1) > 0 else 0.0)
    return {
        "total_return_pct": total * 100,
        "cagr_pct": cagr * 100,
        "buy_hold_return_pct": buy_hold_return * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "volatility_ann_pct": sd * math.sqrt(ppy) * 100,
        "max_drawdown_pct": mdd * 100,
        "longest_drawdown_bars": longest,
        "time_in_market_pct": float(np.mean(in_market) * 100) if len(in_market) else 0.0,
        "trades": nt,
        "win_rate_pct": len(wins) / nt * 100 if nt else 0.0,
        "profit_factor": pf,
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "expectancy_per_trade": float(trade_pnls.mean()) if nt else 0.0,
        "trade_t_stat": float(t_stat),
        "avg_bars_held": float(np.mean(trade_bars)) if nt else 0.0,
        "best_trade": float(trade_pnls.max()) if nt else 0.0,
        "worst_trade": float(trade_pnls.min()) if nt else 0.0,
        "net_profit": float(eq[-1] - start_equity) if n else 0.0,
        "end_equity": float(eq[-1]) if n else start_equity,
    }
