"""Execution. PaperBroker simulates fills; CcxtBroker and Mt5Broker place real orders."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from .state import Position

log = logging.getLogger("bot.broker")


@dataclass
class Fill:
    price: float
    fee: float = 0.0


def round_down(x: float, step: float) -> float:
    if step <= 0:
        return x
    return int(x / step + 1e-9) * step


class Broker:
    """side arguments are +1 (buy/long) or -1 (sell/short). close() passes the position side."""

    def open(self, pos_symbol: str, side: int, qty: float, ref_price: float,
             sl: float, tp: float) -> Fill:
        raise NotImplementedError

    def close(self, symbol: str, side: int, qty: float, ref_price: float) -> Fill:
        raise NotImplementedError

    def normalize_qty(self, symbol: str, qty: float) -> float:
        return round(qty, 6)

    def equity(self, positions: list[Position], marks: dict[str, float]) -> float:
        raise NotImplementedError


class PaperBroker(Broker):
    """Simulated fills at ref price with slippage and fees. Cash is tracked by the engine."""

    def __init__(self, fee_rate: float, slippage_bps: float):
        self.fee_rate = fee_rate
        self.slip = slippage_bps / 10000.0

    def open(self, symbol, side, qty, ref_price, sl, tp):
        price = ref_price * (1 + side * self.slip)
        return Fill(price, price * qty * self.fee_rate)

    def close(self, symbol, side, qty, ref_price):
        price = ref_price * (1 - side * self.slip)
        return Fill(price, price * qty * self.fee_rate)

    def normalize_qty(self, symbol, qty):
        return round(qty, 4)

    def equity(self, positions, marks):  # engine uses its own cash-based equity in paper mode
        raise RuntimeError("paper equity is computed by the engine")


class CcxtBroker(Broker):
    """Spot crypto orders via ccxt. Long-only: sells only close what the bot bought."""

    def __init__(self, exchange, quote: str):
        self.ex = exchange
        self.quote = quote
        self._markets_loaded = False

    def _load(self):
        if not self._markets_loaded:
            self.ex.load_markets()
            self._markets_loaded = True

    def _order(self, symbol, side_str, qty, ref_price):
        self._load()
        order = self.ex.create_order(symbol, "market", side_str, qty)
        price = float(order.get("average") or order.get("price") or ref_price)
        fee = float((order.get("fee") or {}).get("cost") or 0.0)
        return Fill(price, fee)

    def open(self, symbol, side, qty, ref_price, sl, tp):
        if side != 1:
            raise ValueError("CcxtBroker is long-only (spot)")
        return self._order(symbol, "buy", qty, ref_price)

    def close(self, symbol, side, qty, ref_price):
        return self._order(symbol, "sell", qty, ref_price)

    def normalize_qty(self, symbol, qty):
        self._load()
        return float(self.ex.amount_to_precision(symbol, qty))

    def equity(self, positions, marks):
        self._load()
        bal = self.ex.fetch_balance()
        cash = float((bal.get("total") or {}).get(self.quote) or 0.0)
        return cash + sum(p.qty * marks.get(p.symbol, p.entry) for p in positions)


_MT5 = None


def mt5_connect():
    """Initialise the MetaTrader 5 terminal once. Credentials are optional (uses logged-in terminal)."""
    global _MT5
    if _MT5 is not None:
        return _MT5
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package missing: pip install MetaTrader5 (Windows only)") from exc
    kwargs = {}
    if os.getenv("MT5_LOGIN"):
        kwargs = {"login": int(os.environ["MT5_LOGIN"]),
                  "password": os.environ.get("MT5_PASSWORD", ""),
                  "server": os.environ.get("MT5_SERVER", "")}
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    _MT5 = mt5
    return mt5


def mt5_timeframe(tf: str):
    mt5 = mt5_connect()
    table = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
             "1h": "H1", "4h": "H4", "1d": "D1"}
    if tf not in table:
        raise ValueError(f"unsupported timeframe {tf}")
    return getattr(mt5, "TIMEFRAME_" + table[tf])


class Mt5Broker(Broker):
    """Forex/metals orders via MetaTrader 5. Stops/targets are also sent to the server."""

    MAGIC = 260001
    DEVIATION = 20  # points

    def _info(self, symbol):
        mt5 = mt5_connect()
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"unknown MT5 symbol {symbol}")
        return info

    def normalize_qty(self, symbol, qty):
        info = self._info(symbol)
        lots = qty / info.trade_contract_size
        lots = round_down(lots, info.volume_step)
        if lots < info.volume_min:
            return 0.0  # too small to trade with this stop distance
        lots = min(lots, info.volume_max)
        return lots * info.trade_contract_size

    def _filling(self, symbol):
        mt5 = mt5_connect()
        mode = self._info(symbol).filling_mode
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _send(self, request):
        mt5 = mt5_connect()
        res = mt5.order_send(request)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 order failed: {getattr(res, 'retcode', None)} "
                               f"{getattr(res, 'comment', mt5.last_error())}")
        return res

    def open(self, symbol, side, qty, ref_price, sl, tp):
        mt5 = mt5_connect()
        info = self._info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if side == 1 else tick.bid
        lots = round(qty / info.trade_contract_size, 2)
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lots,
            "type": mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL,
            "price": price, "sl": round(sl, info.digits), "tp": round(tp, info.digits),
            "deviation": self.DEVIATION, "magic": self.MAGIC,
            "comment": "mmtb", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(symbol),
        }
        res = self._send(req)
        return Fill(res.price or price, 0.0)

    def close(self, symbol, side, qty, ref_price):
        mt5 = mt5_connect()
        info = self._info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        positions = mt5.positions_get(symbol=symbol) or []
        want = mt5.POSITION_TYPE_BUY if side == 1 else mt5.POSITION_TYPE_SELL
        matches = [p for p in positions if p.type == want and p.magic == self.MAGIC]
        if not matches:
            raise RuntimeError(f"no open MT5 position for {symbol} to close")
        p = matches[0]
        close_type = mt5.ORDER_TYPE_SELL if side == 1 else mt5.ORDER_TYPE_BUY
        price = tick.bid if side == 1 else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "position": p.ticket,
            "volume": p.volume, "type": close_type, "price": price,
            "deviation": self.DEVIATION, "magic": self.MAGIC, "comment": "mmtb-close",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(symbol),
        }
        res = self._send(req)
        return Fill(res.price or price, 0.0)

    def equity(self, positions, marks):
        mt5 = mt5_connect()
        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError(f"MT5 account_info failed: {mt5.last_error()}")
        return float(acc.equity)
