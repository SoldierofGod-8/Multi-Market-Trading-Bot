"""Market data feeds. Every feed returns CLOSED bars only (newest last)."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

log = logging.getLogger("bot.feeds")
COLUMNS = ["open", "high", "low", "close", "volume"]


class Feed(ABC):
    @abstractmethod
    def candles(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        """Return the last n CLOSED bars, indexed by UTC timestamp."""

    @abstractmethod
    def last_price(self, symbol: str) -> float:
        """Current mid/last price."""


class CcxtFeed(Feed):
    """Crypto candles and tickers via ccxt (Binance, Bybit, OKX, Kraken, ...)."""

    def __init__(self, exchange):
        self.exchange = exchange

    def candles(self, symbol, timeframe, n):
        raw = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=n + 1)
        df = pd.DataFrame(raw, columns=["ts", *COLUMNS]).set_index("ts")
        df.index = pd.to_datetime(df.index, unit="ms", utc=True)
        return df.iloc[:-1].tail(n)  # last row is the still-forming bar

    def last_price(self, symbol):
        t = self.exchange.fetch_ticker(symbol)
        return float(t.get("last") or t["close"])


class Mt5Feed(Feed):
    """Forex and metals candles via the MetaTrader 5 terminal (Windows)."""

    def candles(self, symbol, timeframe, n):
        from .brokers import mt5_connect, mt5_timeframe
        mt5 = mt5_connect()
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe(timeframe), 0, n + 1)
        if rates is None or len(rates) < 2:
            raise RuntimeError(f"no MT5 data for {symbol}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")[COLUMNS]
        return df.iloc[:-1].tail(n)  # last row is the still-forming bar

    def last_price(self, symbol):
        from .brokers import mt5_connect
        mt5 = mt5_connect()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"no MT5 tick for {symbol}: {mt5.last_error()}")
        return (tick.bid + tick.ask) / 2


class SyntheticFeed(Feed):
    """SIMULATED random-walk prices. For offline testing/demos only, never real data.

    Each candles() call advances the simulation by one bar.
    """

    START_PRICES = {"BTC": 60000.0, "ETH": 3000.0, "SOL": 150.0, "XAU": 2400.0,
                    "XAG": 30.0, "EUR": 1.08, "GBP": 1.27, "JPY": 150.0}

    def __init__(self, seed: int = 0, vol: float = 0.003):
        self.rng = np.random.default_rng(seed)
        self.vol = vol
        self.series: dict[str, pd.Series] = {}
        self.step_td = None

    def _start(self, symbol: str) -> float:
        for key, px in self.START_PRICES.items():
            if symbol.upper().startswith(key):
                return px
        return 100.0

    def _ensure(self, symbol, timeframe, n):
        if symbol not in self.series:
            base = self._start(symbol)
            rets = self.rng.normal(0, self.vol, n)
            idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("h"),
                                periods=n, freq=pd.Timedelta(timeframe))
            self.series[symbol] = pd.Series(base * np.exp(np.cumsum(rets)), index=idx)

    def candles(self, symbol, timeframe, n):
        self._ensure(symbol, timeframe, max(n, 400))
        s = self.series[symbol]
        step = pd.Timedelta(timeframe)
        nxt = s.iloc[-1] * np.exp(self.rng.normal(0, self.vol))
        s = pd.concat([s, pd.Series([nxt], index=[s.index[-1] + step])])
        self.series[symbol] = s
        close = s.values
        open_ = np.concatenate([[close[0]], close[:-1]])
        spread = np.abs(self.rng.normal(0, self.vol, len(close))) * close
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                           "volume": np.ones(len(close))}, index=s.index)
        return df.iloc[:-1].tail(n)  # consistent with live feeds: drop forming bar

    def last_price(self, symbol):
        if symbol not in self.series:
            self._ensure(symbol, "1h", 400)
        return float(self.series[symbol].iloc[-1])
