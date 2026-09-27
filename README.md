# Portfolio bot — A1 + D + TSY-ME (Alpaca paper)

Three strategies from the Sept-2026 research (`Desktop/BOT/research_2026_09/FINAL_REPORT.md`), run as ONE netted
portfolio in ONE dedicated Alpaca **paper** account, 1/3 of equity per strategy, never more than 100% invested.

| Strategy | Rule (all orders market-on-close) |
|---|---|
| A1 index mean reversion (SPY QQQ IWM DIA MDY, 20% of sleeve each) | buy when close < SMA10 − 1.5×ATR10; sell at the first close > prior day's high, or after 10 days |
| D asset rotation (SPY EFA EEM TLT IEF GLD DBC VNQ) | last trading day of month: hold the top-3 by 126-day return (only if > 0), 1/3 of sleeve each |
| TSY-ME (TLT IEF LQD, 1/3 of sleeve each) | buy at the close 6 trading days before month-end, sell at the month's last close |

Expected (replay of this exact code, 2003-2026, costs included): CAGR ~6.9%, max drawdown ~-9%, Sharpe ~1.1;
2019-2026: CAGR ~7.2%, max DD -9.1%. About 150 orders/year. Past results do not guarantee anything.

## Operation
- Runs once per trading day in the window close−25min … close−11min (15:35–15:49 ET on normal days).
- Stateless: targets are recomputed from market data each run; a missed day is caught up at the next run's close.
- Re-runs the same day are safe (client_order_id `pf-YYYYMMDD-SYM-side`; an already-queued symbol is skipped).
- Aborts without trading if the account holds anything outside its 14 symbols, any short, or foreign open orders.
- Kill switch: repository variable `BOT_ENABLED=false`. Paper-only URL is hardcoded.
- `DRY_RUN=1` (repository variable) logs the plan without sending orders.
- Manual check any time: Actions → Run workflow → mode `dryrun-now` (computes targets with current prices, places nothing).

## Files
`portfolio_rules.py` (pure strategy rules, shared with the backtest) · `portfolio_bot.py` (Alpaca I/O, window, order planning).
Offline tests + replay backtest live in `Desktop/BOT/portfolio_bot/` (they need the local research data).
