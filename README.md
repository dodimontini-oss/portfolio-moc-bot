# Portfolio bot — A1 + D + TSY-ME (one Alpaca paper account)

Three strategies from the Sept-2026 research (`Desktop/BOT/research_2026_09/FINAL_REPORT.md`), run together in ONE
dedicated Alpaca **paper** account: 1/3 of equity per strategy, positions netted per symbol (SPY, TLT and IEF are
shared), never more than 100% invested, never short.

| Strategy | Rule (orders: plain market orders at ~15:35 ET) |
|---|---|
| A1 index mean reversion (SPY QQQ IWM DIA MDY, 20% of its third each) | buy when close < SMA10 − 1.5×ATR10; sell at the first close > prior day's high, or after 10 days |
| D asset rotation (SPY EFA EEM TLT IEF GLD DBC VNQ) | last trading day of month: hold the top-3 by 126-day return (only if > 0), 1/3 of its third each |
| TSY-ME (TLT IEF LQD, 1/3 of its third each) | buy at the close 6 trading days before month-end, sell at the month's last close |

Expected, from a replay of this exact code (2003 to Aug 2026, costs included; past results guarantee nothing):
combined CAGR 6.9% (2019+: 7.2%), max drawdown −9.1%, Sharpe 1.14; ~150 orders/year.
(Each strategy alone: A1 4.2% / −21%; D 13.2% / −23%; TSY 2.6% / −9%.)

## Setup
Secrets (Settings → Secrets and variables → Actions): `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` of a NEW, dedicated paper account.

## Operation
- Runs once per trading day in the window close−25min … close−11min (15:35–15:49 ET on normal days, earlier on half-days).
- Stateless: targets are recomputed from market data each run; a missed day is caught up at the next run's close.
- Re-runs the same day are safe (client_order_id `pf-YYYYMMDD-SYM-side`; an already-queued symbol is skipped).
- Aborts without trading if the account holds anything outside its 14 symbols, any short, or orders it did not place.
- Kill switch: repository variable `BOT_ENABLED=false`. Paper-only URL is hardcoded.
- `DRY_RUN=1` (repository variable) logs the plan without sending orders.
- Manual check any time: Actions → Run workflow → mode `dryrun-now` (computes targets with current prices, places nothing).
- `STRATEGY=A1|D|TSY` runs a single strategy at full size instead (not used by the workflow).

## Files
`portfolio_rules.py` (pure strategy rules, shared with the backtest) · `portfolio_bot.py` (Alpaca I/O, window, order planning).
Offline tests + replay backtest live in `Desktop/BOT/portfolio_bot/` (they need the local research data).
