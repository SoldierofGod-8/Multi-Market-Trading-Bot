"""Main trading loop: monitors every instrument, manages risk, places and closes trades."""
from __future__ import annotations

import csv
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .brokers import Broker, CcxtBroker, Fill, Mt5Broker, PaperBroker
from .config import Config, Instrument
from .feeds import CcxtFeed, Feed, Mt5Feed, SyntheticFeed
from .risk import RiskGuard, position_size
from .state import BotState, Position
from .strategy import evaluate

log = logging.getLogger("bot")
JOURNAL_FIELDS = ["time", "symbol", "market", "side", "qty", "entry", "exit",
                  "pnl", "reason", "mode"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Engine:
    def __init__(self, cfg: Config, feeds: dict[str, Feed], brokers: dict[str, Broker],
                 clock: Callable[[], datetime] = utcnow):
        self.cfg = cfg
        self.feeds = feeds
        self.brokers = brokers
        self.clock = clock
        self.paper = cfg.mode == "paper"
        self.guard = RiskGuard(cfg.risk)
        self.state = BotState.load(cfg.state_file, cfg.starting_equity)
        self.marks: dict[str, float] = {}

    # ---- accounting -------------------------------------------------------
    def equity(self) -> float:
        if self.paper:
            unrealised = sum(p.pnl(self.marks.get(s, p.entry))
                             for s, p in self.state.positions.items())
            return self.state.cash + unrealised
        total = 0.0
        for market, broker in self._unique_brokers():
            mine = [p for p in self.state.positions.values() if p.market == market]
            total += broker.equity(mine, self.marks)
        return total

    def _unique_brokers(self):
        seen = {}
        for market, broker in self.brokers.items():
            seen.setdefault(id(broker), (market, broker))
        return list(seen.values())

    # ---- trade helpers ----------------------------------------------------
    def _open(self, inst: Instrument, side: int, price: float, atr: float, equity: float):
        s = self.cfg.strategy
        broker = self.brokers[inst.market]
        stop_dist = s.stop_atr * atr
        qty = position_size(equity, price, stop_dist, self.cfg.risk.risk_per_trade,
                            self.cfg.risk.max_position_fraction)
        qty = broker.normalize_qty(inst.symbol, qty)
        if qty <= 0:
            log.info("%s: size too small to trade, skipping", inst.symbol)
            return
        sl = price - side * stop_dist
        tp = price + side * s.tp_atr * atr
        fill: Fill = broker.open(inst.symbol, side, qty, price, sl, tp)
        if self.paper:
            self.state.cash -= fill.fee
        pos = Position(symbol=inst.symbol, market=inst.market, side=side, qty=qty,
                       entry=fill.price, sl=sl, tp=tp, opened_at=self.clock().isoformat(),
                       entry_fee=fill.fee)
        self.state.positions[inst.symbol] = pos
        log.info("OPEN %s %s qty=%.6f entry=%.5f sl=%.5f tp=%.5f",
                 "LONG" if side == 1 else "SHORT", inst.symbol, qty, fill.price, sl, tp)

    def _close(self, pos: Position, price: float, reason: str):
        broker = self.brokers[pos.market]
        fill = broker.close(pos.symbol, pos.side, pos.qty, price)
        gross = pos.pnl(fill.price)
        net = gross - fill.fee - pos.entry_fee
        if self.paper:
            self.state.cash += gross - fill.fee
        del self.state.positions[pos.symbol]
        self._journal(pos, fill.price, net, reason)
        log.info("CLOSE %s %s exit=%.5f pnl=%.2f reason=%s",
                 pos.symbol, "LONG" if pos.side == 1 else "SHORT", fill.price, net, reason)

    def _journal(self, pos: Position, exit_price: float, pnl: float, reason: str):
        path = Path(self.cfg.journal_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=JOURNAL_FIELDS)
            if new:
                w.writeheader()
            w.writerow({"time": self.clock().isoformat(), "symbol": pos.symbol,
                        "market": pos.market, "side": "long" if pos.side == 1 else "short",
                        "qty": pos.qty, "entry": pos.entry, "exit": exit_price,
                        "pnl": round(pnl, 4), "reason": reason, "mode": self.cfg.mode})

    def close_all(self, reason: str):
        for sym, pos in list(self.state.positions.items()):
            try:
                price = self.feeds[pos.market].last_price(sym)
                self._close(pos, price, reason)
            except Exception:
                log.exception("failed to close %s during %s", sym, reason)

    # ---- main steps -------------------------------------------------------
    def tick(self):
        now = self.clock()
        today = now.date().isoformat()
        st = self.state
        for inst in self.cfg.instruments:  # refresh marks first
            try:
                self.marks[inst.symbol] = self.feeds[inst.market].last_price(inst.symbol)
            except Exception as exc:
                log.warning("price unavailable for %s: %s", inst.symbol, exc)

        eq = self.equity()
        if st.day != today:
            st.day, st.day_start_equity, st.halted_day = today, eq, None
            log.info("new trading day %s, start equity %.2f", today, eq)
        st.peak_equity = max(st.peak_equity, eq)

        if not st.halted_permanent:
            breach = self.guard.breach(eq, st.peak_equity, st.day_start_equity)
            if breach == "max_drawdown":
                log.error("MAX DRAWDOWN breached (equity %.2f, peak %.2f): flattening and halting",
                          eq, st.peak_equity)
                self.close_all("max_drawdown")
                st.halted_permanent, st.halt_reason = True, "max_drawdown"
            elif breach == "daily_loss" and st.halted_day != today:
                log.error("DAILY LOSS limit hit (equity %.2f): flattening, no new trades today", eq)
                self.close_all("daily_loss")
                st.halted_day = today
                st.halt_reason = "daily_loss"

        if not st.halted_permanent:
            for inst in self.cfg.instruments:
                try:
                    self.process(inst, eq)
                except Exception:
                    log.exception("error processing %s", inst.symbol)
        st.save(self.cfg.state_file)

    def process(self, inst: Instrument, equity: float):
        s = self.cfg.strategy
        feed = self.feeds[inst.market]
        df = feed.candles(inst.symbol, s.timeframe, s.bars)
        price = self.marks.get(inst.symbol, float(df["close"].iloc[-1]))
        bar_time = str(df.index[-1])
        new_bar = self.state.last_bar.get(inst.symbol) != bar_time
        sig = None
        if new_bar:
            self.state.last_bar[inst.symbol] = bar_time
            sig = evaluate(df, s)

        pos = self.state.positions.get(inst.symbol)
        if pos is not None:
            hit = None
            if pos.side == 1:
                hit = "stop" if price <= pos.sl else ("target" if price >= pos.tp else None)
            else:
                hit = "stop" if price >= pos.sl else ("target" if price <= pos.tp else None)
            if hit:
                self._close(pos, price, hit)
            elif sig is not None and sig.cross == -pos.side:
                self._close(pos, price, "signal_exit")
            return

        if sig is None or sig.side == 0:
            return
        if sig.side == -1 and not inst.allow_short:
            return
        if self.state.halted_day == self.clock().date().isoformat():
            return
        if not self.guard.can_open(len(self.state.positions)):
            return
        self._open(inst, sig.side, price, sig.atr, equity)

    def stop_requested(self) -> bool:
        return Path(self.cfg.stop_file).exists()

    def run(self, once: bool = False):
        log.info("bot starting in %s mode with %d instruments", self.cfg.mode,
                 len(self.cfg.instruments))
        try:
            while True:
                self.tick()
                if once or self.stop_requested():
                    break
                time.sleep(self.cfg.poll_seconds)
        except KeyboardInterrupt:
            log.info("interrupted by user")
        finally:
            self.state.save(self.cfg.state_file)
            log.info("state saved; open positions: %s", list(self.state.positions))


def setup_logging(log_file: str):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def build_runtime(cfg: Config) -> tuple[dict[str, Feed], dict[str, Broker]]:
    """Create feeds and brokers per market. Paper mode uses one simulated broker."""
    feeds: dict[str, Feed] = {}
    brokers: dict[str, Broker] = {}
    markets = sorted({i.market for i in cfg.instruments})
    paper_broker = PaperBroker(cfg.paper_fee_rate, cfg.paper_slippage_bps)
    exchange = None
    for m in markets:
        kind = cfg.feed_for(m)
        if kind == "ccxt":
            exchange = exchange or make_exchange(cfg)
            feeds[m] = CcxtFeed(exchange)
        elif kind == "mt5":
            feeds[m] = Mt5Feed()
        else:
            feeds[m] = SyntheticFeed(seed=markets.index(m))
        if cfg.mode == "paper":
            brokers[m] = paper_broker
        elif m == "crypto":
            exchange = exchange or make_exchange(cfg)
            brokers[m] = CcxtBroker(exchange, cfg.ccxt_quote)
        else:
            brokers[m] = Mt5Broker()
    return feeds, brokers


def make_exchange(cfg: Config):
    import ccxt
    if not hasattr(ccxt, cfg.ccxt_exchange):
        raise ValueError(f"unknown ccxt exchange {cfg.ccxt_exchange}")
    params = {"enableRateLimit": True}
    if cfg.mode == "live":
        key, secret = os.getenv("CCXT_API_KEY"), os.getenv("CCXT_SECRET")
        if not key or not secret:
            raise RuntimeError("live crypto trading needs CCXT_API_KEY and CCXT_SECRET in .env")
        params.update({"apiKey": key, "secret": secret})
    return getattr(ccxt, cfg.ccxt_exchange)(params)
