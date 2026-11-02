"""Position sizing and account-level circuit breakers."""
from __future__ import annotations

from typing import Optional

from .config import RiskConfig


def position_size(equity: float, price: float, stop_distance: float,
                  risk_per_trade: float, max_fraction: float) -> float:
    """Units to buy/sell so that hitting the stop loses ~risk_per_trade of equity.

    Capped so notional value never exceeds max_fraction * equity (leverage guard).
    """
    if equity <= 0 or price <= 0 or stop_distance <= 0:
        return 0.0
    qty = equity * risk_per_trade / stop_distance
    return min(qty, equity * max_fraction / price)


def drawdown(equity: float, peak: float) -> float:
    return (peak - equity) / peak if peak > 0 else 0.0


def daily_loss(equity: float, day_start: float) -> float:
    return (day_start - equity) / day_start if day_start > 0 else 0.0


class RiskGuard:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def breach(self, equity: float, peak: float, day_start: float) -> Optional[str]:
        if drawdown(equity, peak) >= self.cfg.max_drawdown:
            return "max_drawdown"
        if daily_loss(equity, day_start) >= self.cfg.daily_loss_limit:
            return "daily_loss"
        return None

    def can_open(self, n_open: int) -> bool:
        return n_open < self.cfg.max_open_positions
