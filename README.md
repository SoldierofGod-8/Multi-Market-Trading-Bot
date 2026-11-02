# Multi-Market-Trading-Bot

An automated trading bot that monitors **crypto, forex and precious metals** and
trades them with a trend-following strategy, strict position sizing and automatic
loss limits.

> **Read this first.** No trading bot can guarantee profit. Trading leveraged
> products and crypto can lose money quickly, and most automated strategies lose
> money over time. This software is provided as-is with no warranty. Start with
> **paper mode**, study the results for several weeks, and only then consider
> live trading with money you can afford to lose.

---

## Quick start (Windows)

1. Install **Python 3.9+** from <https://www.python.org/downloads/> and tick
   *"Add python.exe to PATH"*.
2. (Forex and metals only) Install the **MetaTrader 5** terminal from your broker,
   log in to a demo account, and enable *Tools > Options > Expert Advisors > Allow
   algorithmic trading*.
3. Double-click **`start_bot.bat`**. The first run installs everything automatically.
4. Choose **1. PAPER TRADE**. The bot now runs on real market prices with simulated money.

To stop: press `Ctrl+C`, or create an empty file named `STOP_BOT` in the folder.

### Menu
| Option | What it does |
|---|---|
| 1 Paper trade | Simulated money on real prices. Safe. Use this first. |
| 2 Live trade | Real orders. Requires typing `I UNDERSTAND`. |
| 3 Backtest | Replays the strategy on historical data for one symbol. |
| 4 Status | Shows cash, open positions, and any halts. |
| 5 Clear halts | Re-enables trading after a drawdown or daily-loss stop. |
| 6 Edit config | Opens `config.yaml` and `.env`. |

## How it works

Every `poll_seconds` the bot:

1. Gets the latest closed candles for each instrument (default 1h).
2. **Entry (long):** fast EMA(20) crosses above slow EMA(50), price is above EMA(200),
   and RSI(14) is between 50 and 70. **Entry (short):** the mirror image, for forex and
   metals only (crypto is spot, long-only).
3. **Position size:** risks `risk_per_trade` (1%) of equity between entry and a
   stop-loss of `2 x ATR`. Notional is capped at 25% of equity.
4. **Exits:** stop-loss at 2 x ATR, take-profit at 3 x ATR, or an opposite EMA cross.
5. **Circuit breakers:** a 3% daily loss flattens everything and stops trading for
   the day. A 15% drawdown from peak flattens everything and halts until you run
   *Clear halts*. At most 4 positions are open at once.

Every closed trade is written to `logs/trades.csv`. Activity goes to `logs/bot.log`.
State is saved to `data/state.json`, so the bot resumes where it stopped.

## Configuration

Edit **`config.yaml`**:
- `mode`: `paper` or `live`.
- `feeds`: where each market gets prices (`ccxt`, `mt5`, or `synthetic`). `synthetic`
  produces fake random prices for testing only, and is refused in live mode.
- `instruments`: which symbols to watch, and whether shorting is allowed.
- `risk` and `strategy`: all tunable parameters.

Credentials go in **`.env`** (created from `.env.example`, never committed):
- Crypto: `CCXT_API_KEY`, `CCXT_SECRET`. Create the key with trading permission and
  **withdrawals disabled**.
- Forex/metals: `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`. Optional if the MT5
  terminal is already logged in.

## Command line

```
python -m bot run [--mode paper|live] [--once]
python -m bot backtest --market crypto --symbol BTC/USDT --bars 2000
python -m bot status
python -m bot clear-halt
```

Run tests with `pip install -r requirements-dev.txt` and then `pytest`.

## Project layout

```
bot/
  config.py      configuration + .env loader
  feeds.py       market data: ccxt (crypto), MetaTrader 5 (forex/metals), synthetic (testing)
  brokers.py     paper broker, ccxt spot broker, MetaTrader 5 broker
  strategy.py    entry/exit signal logic
  indicators.py  EMA, RSI, ATR
  risk.py        position sizing and circuit breakers
  engine.py      main loop
  backtest.py    historical replay with the same rules
  state.py       persistent state
tests/           offline unit and integration tests
start_bot.bat    Windows launcher
config.yaml      settings
```

## Limitations (please read)

- **No profit guarantee.** Backtest results are not predictive. Slippage, spreads,
  fees, news events and regime changes can turn a profitable backtest into a loss.
- **Stops are managed by the bot.** Crypto stops are software-managed. If the
  computer or internet goes down, the bot cannot react. MT5 orders also carry
  server-side stops.
- **Currency conversion is not handled.** P&L is computed in the quote currency.
  If your account currency is not USD, check the numbers in your broker.
- **Forex and metals need Windows** and MetaTrader 5 for live data and orders.
- **Crypto exchanges** may restrict access by region. Pick another exchange in
  `config.yaml` (`ccxt_exchange`) if needed.
- The default parameters are a reasonable starting point, not an optimised
  strategy. Optimise only on out-of-sample data, and expect results to degrade in live
  trading.
