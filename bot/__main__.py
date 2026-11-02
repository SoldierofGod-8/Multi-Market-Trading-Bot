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


def cmd_backtest(args, cfg: Config):
    from .backtest import run_backtest
    from .engine import make_exchange
    from .feeds import CcxtFeed, Mt5Feed
    kind = cfg.feed_for(args.market)
    if kind == "ccxt":
        feed = CcxtFeed(make_exchange(cfg))
    elif kind == "mt5":
        feed = Mt5Feed()
    else:
        sys.exit("backtest needs real data: set feeds.%s to ccxt or mt5" % args.market)
    tf = cfg.strategy.timeframe
    df = feed.candles(args.symbol, tf, args.bars)
    allow_short = args.market != "crypto"
    rep = run_backtest(df, cfg.strategy, cfg, allow_short=allow_short)
    print(f"Backtest {args.symbol} {tf} bars={len(df)} ({df.index[0]} -> {df.index[-1]})")
    for k, v in rep.summary().items():
        print(f"  {k:>16}: {v:.4f}" if isinstance(v, float) else f"  {k:>16}: {v}")
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
