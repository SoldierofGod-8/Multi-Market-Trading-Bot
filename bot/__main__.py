"""Command line entry point.

  python -m bot run [--config config.yaml] [--mode paper|live] [--once]
  python -m bot backtest --market crypto --symbol BTC/USDT [--bars 2000]
  python -m bot status
  python -m bot clear-halt
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config, load_dotenv


def cmd_run(args, cfg: Config):
    from .engine import Engine, build_runtime, setup_logging
    if cfg.mode == "live" and __import__("os").getenv("LIVE_CONFIRM") != "YES":
        sys.exit("Live mode refused: set LIVE_CONFIRM=YES (start_bot.bat does this after confirmation).")
    setup_logging(cfg.log_file)
    feeds, brokers = build_runtime(cfg)
    Engine(cfg, feeds, brokers).run(once=args.once)


def load_csv(path: str) -> "pd.DataFrame":
    """Load OHLC(V) candles from a CSV with a datetime/date column (UTC assumed)."""
    import pandas as pd
    raw = pd.read_csv(path)
    raw.columns = [c.strip().lower() for c in raw.columns]
    tcol = "datetime" if "datetime" in raw.columns else "date"
    df = raw.set_index(pd.to_datetime(raw[tcol], utc=True))[["open", "high", "low", "close"]]
    df = df.astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def cmd_backtest(args, cfg: Config):
    from .backtest import run_backtest
    from .engine import make_exchange
    from .feeds import CcxtFeed, Mt5Feed
    tf = args.timeframe or cfg.strategy.timeframe
    if args.csv:
        df = load_csv(args.csv)
    else:
        kind = cfg.feed_for(args.market)
        if kind == "ccxt":
            feed = CcxtFeed(make_exchange(cfg))
        elif kind == "mt5":
            feed = Mt5Feed()
        else:
            sys.exit("backtest needs real data: set feeds.%s to ccxt or mt5" % args.market)
        df = feed.candles(args.symbol, tf, args.bars)
    if args.start:
        df = df[df.index >= args.start]
    if args.end:
        df = df[df.index <= args.end]
    cfg.strategy.timeframe = tf
    allow_short = args.market != "crypto"
    rep = run_backtest(df, cfg.strategy, cfg, allow_short=allow_short, market=args.market)
    print(f"Backtest {args.symbol} {tf} bars={len(df)} ({df.index[0]} -> {df.index[-1]})")
    print(f"Costs: fee {cfg.paper_fee_rate*100:.3f}%/side, slippage {cfg.paper_slippage_bps} bps, "
          f"risk {cfg.risk.risk_per_trade*100:.1f}%/trade, start equity {cfg.starting_equity:,.0f}")
    for k, v in rep.summary().items():
        if isinstance(v, float):
            print(f"  {k:>22}: {v:,.4f}")
        else:
            print(f"  {k:>22}: {v}")
    if args.trades_out:
        rep.trades_frame().to_csv(args.trades_out, index=False)
        print(f"Trades written to {args.trades_out}")
    print("NOTE: past results do not guarantee future results.")


def cmd_status(args, cfg: Config):
    from .state import BotState
    st = BotState.load(cfg.state_file, cfg.starting_equity)
    print(json.dumps({
        "cash": round(st.cash, 2), "peak_equity": round(st.peak_equity, 2),
        "day": st.day, "halted_day": st.halted_day,
        "halted_permanent": st.halted_permanent, "halt_reason": st.halt_reason,
        "open_positions": {k: vars(v) for k, v in st.positions.items()},
    }, indent=2))


def cmd_clear_halt(args, cfg: Config):
    from .state import BotState
    st = BotState.load(cfg.state_file, cfg.starting_equity)
    st.halted_permanent, st.halted_day, st.halt_reason = False, None, ""
    st.peak_equity = 0.0  # re-anchored to real equity on the next tick
    st.save(cfg.state_file)
    print("Halts cleared. Peak equity will re-anchor to current equity on the next run.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="bot", description="Multi-market trading bot")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["paper", "live"])
    r.add_argument("--once", action="store_true", help="run a single cycle and exit")
    b = sub.add_parser("backtest")
    b.add_argument("--market", required=True, choices=["crypto", "forex", "metals"])
    b.add_argument("--symbol", required=True)
    b.add_argument("--bars", type=int, default=2000)
    b.add_argument("--csv", help="backtest a local CSV instead of downloading (columns: datetime/date, open, high, low, close)")
    b.add_argument("--timeframe", help="override strategy timeframe, e.g. 1h, 4h, 1d")
    b.add_argument("--start", help="ISO date/time to start from, e.g. 2020-01-01")
    b.add_argument("--end", help="ISO date/time to end at")
    b.add_argument("--trades-out", help="write every trade to this CSV")
    sub.add_parser("status")
    sub.add_parser("clear-halt")
    args = p.parse_args(argv)

    load_dotenv(".env")
    cfg = Config.from_yaml(args.config)
    if getattr(args, "mode", None):
        cfg.mode = args.mode
        cfg.validate()
    {"run": cmd_run, "backtest": cmd_backtest, "status": cmd_status,
     "clear-halt": cmd_clear_halt}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
