"""Configuration loading (config.yaml + .env secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MARKETS = ("crypto", "forex", "metals")
FEEDS = ("ccxt", "mt5", "synthetic")


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01          # fraction of equity risked per trade
    max_open_positions: int = 4
    max_position_fraction: float = 0.25   # max notional per trade as fraction of equity
    daily_loss_limit: float = 0.03        # halt new trades + flatten if down this much today
    max_drawdown: float = 0.15            # halt permanently + flatten past this drawdown from peak


@dataclass
class StrategyConfig:
    timeframe: str = "1h"
    bars: int = 300
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    rsi_period: int = 14
    rsi_long_min: float = 50.0
    rsi_long_max: float = 70.0
    rsi_short_min: float = 30.0
    rsi_short_max: float = 50.0
    atr_period: int = 14
    stop_atr: float = 2.0
    tp_atr: float = 3.0


@dataclass
class Instrument:
    symbol: str
    market: str
    allow_short: bool = True


@dataclass
class Config:
    mode: str = "paper"                   # paper | live
    starting_equity: float = 10000.0
    poll_seconds: int = 60
    state_file: str = "data/state.json"
    journal_file: str = "logs/trades.csv"
    log_file: str = "logs/bot.log"
    stop_file: str = "STOP_BOT"
    paper_fee_rate: float = 0.001
    paper_slippage_bps: float = 2.0
    ccxt_exchange: str = "binance"
    ccxt_quote: str = "USDT"
    feeds: dict = field(default_factory=dict)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    instruments: list = field(default_factory=list)

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'")
        if not self.instruments:
            raise ValueError("no instruments configured")
        for inst in self.instruments:
            if inst.market not in MARKETS:
                raise ValueError(f"unknown market {inst.market!r} for {inst.symbol}")
        for market, kind in self.feeds.items():
            if market not in MARKETS or kind not in FEEDS:
                raise ValueError(f"bad feed entry {market}: {kind}")
            if self.mode == "live" and kind == "synthetic":
                raise ValueError("synthetic feed is not allowed in live mode")
        r = self.risk
        if not (0 < r.risk_per_trade <= 0.05):
            raise ValueError("risk_per_trade must be within (0, 0.05]")
        if not (0 < r.daily_loss_limit < r.max_drawdown < 1):
            raise ValueError("need 0 < daily_loss_limit < max_drawdown < 1")
        if self.strategy.ema_fast >= self.strategy.ema_slow:
            raise ValueError("ema_fast must be smaller than ema_slow")

    def feed_for(self, market: str) -> str:
        default = "ccxt" if market == "crypto" else "mt5"
        return self.feeds.get(market, default)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        cfg = cls(
            mode=raw.get("mode", "paper"),
            starting_equity=float(raw.get("starting_equity", 10000)),
            poll_seconds=int(raw.get("poll_seconds", 60)),
            state_file=raw.get("state_file", "data/state.json"),
            journal_file=raw.get("journal_file", "logs/trades.csv"),
            log_file=raw.get("log_file", "logs/bot.log"),
            stop_file=raw.get("stop_file", "STOP_BOT"),
            paper_fee_rate=float(raw.get("paper_fee_rate", 0.001)),
            paper_slippage_bps=float(raw.get("paper_slippage_bps", 2.0)),
            ccxt_exchange=raw.get("ccxt_exchange", "binance"),
            ccxt_quote=raw.get("ccxt_quote", "USDT"),
            feeds=raw.get("feeds", {}) or {},
            risk=RiskConfig(**(raw.get("risk") or {})),
            strategy=StrategyConfig(**(raw.get("strategy") or {})),
            instruments=[Instrument(**i) for i in (raw.get("instruments") or [])],
        )
        cfg.validate()
        return cfg


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
