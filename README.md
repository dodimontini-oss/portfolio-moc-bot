# Strategy bots — A1, D, TSY-ME (Alpaca paper, one account each)

Three strategies from the Sept-2026 research (`Desktop/BOT/research_2026_09/FINAL_REPORT.md`). Each runs at FULL size
in its OWN dedicated Alpaca **paper** account (never more than 100% invested, never short). One workflow runs all three.

| Strategy | Rule (all orders market-on-close) |
|---|---|
| A1 index mean reversion (SPY QQQ IWM DIA MDY, 20% of equity each) | buy when close < SMA10 − 1.5×ATR10; sell at the first close > prior day's high, or after 10 days |
| D asset rotation (SPY EFA EEM TLT IEF GLD DBC VNQ) | last trading day of month: hold the top-3 by 126-day return (only if > 0), 1/3 of equity each |
| TSY-ME (TLT IEF LQD, 1/3 of equity each) | buy at the close 6 trading days before month-end, sell at the month's last close |

Expected, from a replay of this exact code (2003 to Aug 2026, costs included; past results guarantee nothing):

| Account | CAGR full / 2019+ | Max drawdown | Sharpe | Orders/yr |
|---|---|---|---|---|
| A1 | 4.2% / 4.2% | −21.3% | 0.51 | ~73 |
| D | 13.2% / 14.1% | −22.8% | 1.00 | ~16 |
| TSY | 2.6% / 2.8% | −9.3% | 0.66 | ~72 |

## Setup
Secrets (Settings → Secrets and variables → Actions), one pair per account:
`ALPACA_API_KEY_A1` / `ALPACA_SECRET_KEY_A1`, `ALPACA_API_KEY_D` / `ALPACA_SECRET_KEY_D`,
`ALPACA_API_KEY_TSY` / `ALPACA_SECRET_KEY_TSY`. A strategy with no secrets is skipped, so accounts can be added one at a time.

## Operation
- Runs once per trading day in the window close−25min … close−11min (15:35–15:49 ET on normal days, earlier on half-days).
- Stateless: targets are recomputed from market data each run; a missed day is caught up at the next run's close.
- Re-runs the same day are safe (client_order_id `pfa1-/pfd-/pftsy-YYYYMMDD-SYM-side`; an already-queued symbol is skipped).
- Each bot aborts without trading if its account holds anything outside its own symbols, any short, or another bot's orders.
- Kill switch: repository variable `BOT_ENABLED=false`. Paper-only URL is hardcoded.
- `DRY_RUN=1` (repository variable) logs the plan without sending orders.
- Manual check any time: Actions → Run workflow → mode `dryrun-now` (computes targets with current prices, places nothing).

## Files
`portfolio_rules.py` (pure strategy rules, shared with the backtest) · `portfolio_bot.py` (Alpaca I/O, window, order planning).
Offline tests + replay backtest live in `Desktop/BOT/portfolio_bot/` (they need the local research data).
