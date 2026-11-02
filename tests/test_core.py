from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from bot.backtest import run_backtest
from bot.brokers import PaperBroker, round_down
from bot.config import Config, Instrument, RiskConfig, StrategyConfig
from bot.engine import Engine
from bot.feeds import Feed, SyntheticFeed
from bot.indicators import atr, ema, rsi
from bot.risk import RiskGuard, drawdown, daily_loss, position_size
from bot.state import BotState, Position
from bot.strategy import evaluate


def make_cfg(tmp_path, **over):
    cfg = Config(
        mode="paper", starting_equity=10000, state_file=str(tmp_path / "state.json"),
        journal_file=str(tmp_path / "trades.csv"), log_file=str(tmp_path / "bot.log"),
        stop_file=str(tmp_path / "STOP"), paper_fee_rate=0.001, paper_slippage_bps=0,
        feeds={"crypto": "synthetic", "forex": "synthetic", "metals": "synthetic"},
        risk=RiskConfig(), strategy=StrategyConfig(),
        instruments=[Instrument("BTC/USDT", "crypto", False), Instrument("EURUSD", "forex", True)],
    )
    for k, v in over.items():
        setattr(cfg, k, v)
    cfg.validate()
    return cfg


def ohlc_from_close(close):
    c = np.asarray(close, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(c), freq="h", tz="UTC")
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": c + 0.5, "low": c - 0.5, "close": c,
                         "volume": 1.0}, index=idx)


# ---------- indicators ----------
def test_ema_matches_manual():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    e = ema(s, 3)
    alpha = 2 / 4
    expected = [1.0]
    for x in [2.0, 3.0, 4.0]:
        expected.append(alpha * x + (1 - alpha) * expected[-1])
    assert np.allclose(e.values, expected)


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(100, dtype=float))
    assert rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(100, 0, -1, dtype=float))
    assert rsi(down, 14).iloc[-1] < 1.0
    rng = np.random.default_rng(1)
    r = rsi(pd.Series(100 + rng.normal(size=500).cumsum()), 14)
    assert ((r >= 0) & (r <= 100)).all()


def test_atr_positive():
    df = ohlc_from_close(np.linspace(100, 110, 60))
    assert (atr(df, 14).dropna() > 0).all()


# ---------- strategy ----------
def test_no_signal_during_warmup():
    df = ohlc_from_close(np.linspace(1, 2, 50))
    assert evaluate(df, StrategyConfig()).side == 0


def test_long_signal_on_uptrend_cross():
    # long flat-ish then strong rise: fast EMA crosses slow above trend with RSI in band
    p = StrategyConfig(ema_trend=30, ema_fast=5, ema_slow=10, rsi_long_min=0, rsi_long_max=100)
    close = np.concatenate([np.full(60, 100.0), np.linspace(100, 80, 40)])  # decline first
    close = np.concatenate([close, np.linspace(80, 130, 25)])               # then rally
    found = []
    df_all = ohlc_from_close(close)
    for i in range(p.ema_trend + 2, len(df_all) + 1):
        sig = evaluate(df_all.iloc[:i], p)
        if sig.side != 0 or sig.cross != 0:
            found.append((i, sig))
    assert any(s.side == 1 for _, s in found), found


def test_signal_fields_are_consistent():
    rng = np.random.default_rng(7)
    df = ohlc_from_close(100 + rng.normal(size=400).cumsum())
    p = StrategyConfig()
    for i in range(p.ema_trend + 2, len(df) + 1, 7):
        s = evaluate(df.iloc[:i], p)
        assert s.side in (-1, 0, 1) and s.cross in (-1, 0, 1)
        if s.side != 0:
            assert s.side == s.cross  # entries only come from a fresh cross


# ---------- risk ----------
def test_position_size_risks_expected_amount():
    qty = position_size(10000, 100, 2.0, 0.01, 10.0)  # risk 100 over stop distance 2
    assert qty == pytest.approx(50)


def test_position_size_caps_notional():
    qty = position_size(10000, 100, 0.01, 0.01, 0.25)  # tiny stop -> cap at 25% notional
    assert qty * 100 == pytest.approx(2500)


def test_drawdown_and_breach():
    assert drawdown(850, 1000) == pytest.approx(0.15)
    assert daily_loss(970, 1000) == pytest.approx(0.03)
    g = RiskGuard(RiskConfig(risk_per_trade=0.01, daily_loss_limit=0.03, max_drawdown=0.15))
    assert g.breach(1000, 1000, 1000) is None
    assert g.breach(970, 1000, 1000) == "daily_loss"
    assert g.breach(840, 1000, 1000) == "max_drawdown"


def test_round_down():
    assert round_down(1.237, 0.01) == pytest.approx(1.23)


def test_config_validation(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.mode = "live"
    cfg.feeds = {"crypto": "synthetic"}
    with pytest.raises(ValueError):
        cfg.validate()


# ---------- paper engine ----------
class ScriptedFeed(Feed):
    """Feed with controllable last price, returning a fixed candle frame."""

    def __init__(self, df, price):
        self.df, self.price = df, price

    def candles(self, symbol, timeframe, n):
        return self.df.tail(n)

    def last_price(self, symbol):
        return self.price


def run_engine_once(tmp_path, df, price, clock, cfg=None):
    cfg = cfg or make_cfg(tmp_path)
    feeds = {"crypto": ScriptedFeed(df, price), "forex": ScriptedFeed(df, price)}
    brokers = {"crypto": PaperBroker(0, 0), "forex": PaperBroker(0, 0)}
    eng = Engine(cfg, feeds, brokers, clock=lambda: clock)
    eng.tick()
    return eng


def test_paper_engine_stop_and_target_cycle(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.instruments = [Instrument("BTC/USDT", "crypto", False)]
    now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    st = BotState(cash=10000, peak_equity=10000)
    st.save(cfg.state_file)
    df = ohlc_from_close(np.linspace(100, 101, 300))
    eng = Engine(cfg, {"crypto": ScriptedFeed(df, 101.0)}, {"crypto": PaperBroker(0, 0)},
                 clock=lambda: now)
    # Force an open long position and check the stop closes it at a loss.
    eng.marks["BTC/USDT"] = 100.0
    eng.state.day = now.date().isoformat()
    eng.state.day_start_equity = 10000
    eng.state.positions["BTC/USDT"] = Position("BTC/USDT", "crypto", 1, 10, 100.0, 98.0, 103.0,
                                               now.isoformat())
    eng.state.last_bar["BTC/USDT"] = str(df.index[-1])  # no new bar -> no new signal
    eng.feeds["crypto"].price = 97.5
    eng.tick()
    assert "BTC/USDT" not in eng.state.positions
    assert eng.state.cash == pytest.approx(10000 - 25)  # 10 units * -2.5
    assert (tmp_path / "trades.csv").read_text().count("stop") == 1

    # target path
    eng.state.positions["BTC/USDT"] = Position("BTC/USDT", "crypto", 1, 10, 100.0, 98.0, 103.0,
                                               now.isoformat())
    eng.feeds["crypto"].price = 104.0
    eng.tick()
    assert "BTC/USDT" not in eng.state.positions
    assert eng.state.cash == pytest.approx(10000 - 25 + 40)


def test_daily_loss_flattens_and_halts(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.instruments = [Instrument("BTC/USDT", "crypto", False)]
    now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    df = ohlc_from_close(np.linspace(100, 101, 300))
    feed = ScriptedFeed(df, 100.0)
    eng = Engine(cfg, {"crypto": feed}, {"crypto": PaperBroker(0, 0)}, clock=lambda: now)
    eng.state.day = now.date().isoformat()
    eng.state.day_start_equity = 10000
    eng.state.cash = 10000
    eng.state.peak_equity = 10000
    eng.state.positions["BTC/USDT"] = Position("BTC/USDT", "crypto", 1, 100, 100.0, 1.0, 999.0,
                                               now.isoformat())
    feed.price = 96.0  # equity = 10000 - 400 = 9600 -> -4% > 3% daily limit
    eng.tick()
    assert eng.state.positions == {}
    assert eng.state.halted_day == now.date().isoformat()
    assert not eng.state.halted_permanent


def test_max_drawdown_halts_permanently(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.instruments = [Instrument("BTC/USDT", "crypto", False)]
    now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    df = ohlc_from_close(np.linspace(100, 101, 300))
    eng = Engine(cfg, {"crypto": ScriptedFeed(df, 100.0)}, {"crypto": PaperBroker(0, 0)},
                 clock=lambda: now)
    eng.state.day = now.date().isoformat()
    eng.state.day_start_equity = 10000
    eng.state.cash = 8400
    eng.state.peak_equity = 10000
    eng.tick()
    assert eng.state.halted_permanent and eng.state.halt_reason == "max_drawdown"


def test_full_paper_run_no_network(tmp_path):
    """Smoke test: synthetic feeds, several ticks, state and journal persist."""
    cfg = make_cfg(tmp_path)
    cfg.strategy = StrategyConfig(ema_fast=5, ema_slow=10, ema_trend=20, bars=120,
                                  rsi_long_min=0, rsi_long_max=100, rsi_short_min=0,
                                  rsi_short_max=100)
    cfg.feeds = {"crypto": "synthetic", "forex": "synthetic"}
    cfg.paper_slippage_bps = 2
    clock = {"t": datetime(2026, 11, 2, 10, tzinfo=timezone.utc)}
    feeds = {"crypto": SyntheticFeed(seed=1, vol=0.01), "forex": SyntheticFeed(seed=2, vol=0.003)}
    brokers = {"crypto": PaperBroker(0.001, 2), "forex": PaperBroker(0.001, 2)}
    eng = Engine(cfg, feeds, brokers, clock=lambda: clock["t"])
    for _ in range(300):
        clock["t"] = clock["t"] + pd.Timedelta("1h").to_pytimedelta()
        eng.tick()
    st = BotState.load(cfg.state_file, cfg.starting_equity)
    assert st.cash > 0
    assert set(st.last_bar) == {"BTC/USDT", "EURUSD"}
    eq = eng.equity()
    assert eq > 0


def test_backtest_runs_and_reports(tmp_path):
    cfg = make_cfg(tmp_path)
    s = StrategyConfig(ema_fast=5, ema_slow=10, ema_trend=20, rsi_long_min=0, rsi_long_max=100,
                       rsi_short_min=0, rsi_short_max=100)
    rng = np.random.default_rng(3)
    df = ohlc_from_close(100 + rng.normal(size=800).cumsum())
    rep = run_backtest(df, s, cfg, allow_short=True)
    summ = rep.summary()
    assert summ["trades"] == len(rep.trades)
    assert 0 <= summ["max_drawdown_pct"] <= 100
    assert len(rep.equity_curve) == len(df)  # mark-to-market for every bar
