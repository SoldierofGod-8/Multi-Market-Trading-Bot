import numpy as np
import pandas as pd

from bot.backtest import run_backtest
from bot.config import Config, Instrument, RiskConfig, StrategyConfig
from bot.stats import compute_stats, periods_per_year
from bot.strategy import evaluate, signal_frame
from tests.test_core import ohlc_from_close


def test_signal_frame_matches_evaluate_row_by_row():
    rng = np.random.default_rng(11)
    df = ohlc_from_close(100 + rng.normal(size=600).cumsum())
    p = StrategyConfig(ema_fast=5, ema_slow=12, ema_trend=40, rsi_long_min=40,
                       rsi_long_max=65, rsi_short_min=35, rsi_short_max=60)
    frame = signal_frame(df, p)
    for i in range(0, len(df), 3):
        ev = evaluate(df.iloc[: i + 1], p)
        row = frame.iloc[i]
        assert int(row["side"]) == ev.side, i
        assert int(row["cross"]) == ev.cross, i
        if ev.side != 0:
            assert abs(row["atr"] - ev.atr) < 1e-9


def test_sharpe_zero_for_flat_equity_and_known_value():
    eq = np.full(100, 1000.0)
    st = compute_stats(eq, np.array([]), np.array([]), np.zeros(100), 1000.0, 0.0, 252)
    assert st["sharpe"] == 0.0 and st["max_drawdown_pct"] == 0.0
    # alternating +1%/-1% from 1000 -> mean ~0 but sd>0
    eq2 = 1000 * np.cumprod(np.r_[1.0, np.tile([1.01, 0.99], 50)])
    st2 = compute_stats(eq2, np.array([]), np.array([]), np.ones(len(eq2)), 1000.0, 0.0, 252)
    assert st2["max_drawdown_pct"] > 0 and abs(st2["sharpe"]) < 0.1


def test_periods_per_year():
    assert periods_per_year("1d", "crypto") == 365
    assert periods_per_year("1h", "crypto") == 365 * 24
    assert periods_per_year("4h", "forex") == 252 * 6


def test_backtest_no_lookahead_on_truncation():
    """Results for bars <= k must not change when later bars are removed."""
    rng = np.random.default_rng(5)
    df = ohlc_from_close(100 + rng.normal(size=500).cumsum())
    p = StrategyConfig(ema_fast=5, ema_slow=12, ema_trend=40, rsi_long_min=0,
                       rsi_long_max=100, rsi_short_min=0, rsi_short_max=100)
    cfg = Config(instruments=[Instrument("X", "forex")])
    cfg.risk = RiskConfig()
    full = signal_frame(df, p)
    part = signal_frame(df.iloc[:300], p)
    assert (full.iloc[:300]["side"].values == part["side"].values).all()
    assert np.allclose(full.iloc[:300]["atr"].values, part["atr"].values)
    r = run_backtest(df, p, cfg, allow_short=True, market="forex")
    assert len(r.equity_curve) == len(df)
