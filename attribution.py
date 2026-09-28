"""
Per-strategy SHADOW LEDGER for the netted portfolio account.

The account holds one combined position per symbol, so the broker cannot say which strategy earned what. This module
replays each strategy's rules (portfolio_rules.target_weights - the same code the bot trades with) day by day from the
account's first fill, sizes each sleeve exactly like the bot (weight x total equity / price, whole shares, same
event/drift-band rule), and prices every sleeve trade at the account's ACTUAL market-on-close fill price for that
symbol and day (falling back to the official close if there was no fill). Result per strategy: realized P&L,
open (unrealized) P&L, closed trades (a "trade" = one holding spell of one symbol, from first buy until flat),
win %, profit factor. The sum of the three sleeves is compared with the real account P&L (`attribution_gap`),
so any drift of the shadow split from reality is visible.
"""
import math
from collections import defaultdict, deque
import pandas as pd
import portfolio_rules as R

SLEEVES = ("A1", "D", "TSY")
NAMES = {"A1": "A1 · index mean reversion", "D": "D · asset rotation", "TSY": "TSY-ME · bond month-end"}
DRIFT_BAND = 0.25


def sleeve_ledger(data, calendar, start, end, fill_px, equity0):
    """data: {symbol: DataFrame(open, high, low, close)} of COMPLETED daily bars; calendar: trading days (may extend
    into the future, needed for month-end detection); fill_px: {(Timestamp day, symbol): price}; equity0: account
    starting equity. Returns {sleeve: stats dict}."""
    days = [d for d in data["SPY"].index if pd.Timestamp(start) <= d <= pd.Timestamp(end)]
    cash = {sl: equity0 / len(SLEEVES) for sl in SLEEVES}
    lots = {sl: defaultdict(deque) for sl in SLEEVES}          # FIFO lots: [qty, price]
    realized = {sl: 0.0 for sl in SLEEVES}
    spell = {sl: defaultdict(float) for sl in SLEEVES}         # realized P&L of the currently open spell per symbol
    closed = {sl: [] for sl in SLEEVES}                         # finished spells: dicts
    opened = {sl: {} for sl in SLEEVES}
    prev_w = {sl: {} for sl in SLEEVES}
    qty = lambda sl, s: sum(l[0] for l in lots[sl][s])
    for t in days:
        close = {s: float(data[s].close.loc[t]) for s in R.ALL_SYMBOLS if t in data[s].index}
        e_total = sum(cash.values()) + sum(qty(sl, s) * close[s] for sl in SLEEVES for s in lots[sl] if s in close)
        for sl in SLEEVES:
            w, _ = R.target_weights(data, t, calendar=calendar, sleeves={sl: R.SLEEVE[sl]})
            for s in R.UNIVERSE[sl]:
                if s not in close:
                    continue
                q = qty(sl, s)
                tq = int(math.floor(w[s] * e_total / close[s])) if w[s] > 0 else 0
                if w[s] == 0:
                    trade = q != 0
                else:
                    trade = abs(w[s] - prev_w[sl].get(s, 0.0)) > 1e-12 or abs(q - tq) > DRIFT_BAND * max(tq, 1)
                dq = tq - q
                if not trade or dq == 0:
                    continue
                px = fill_px.get((t, s), close[s])
                if dq > 0:
                    if q == 0:
                        opened[sl][s] = t
                    lots[sl][s].append([dq, px])
                    cash[sl] -= dq * px
                else:
                    m_left = -dq
                    cash[sl] += m_left * px
                    while m_left > 0 and lots[sl][s]:
                        lot = lots[sl][s][0]
                        m = min(m_left, lot[0])
                        pnl = (px - lot[1]) * m
                        realized[sl] += pnl
                        spell[sl][s] += pnl
                        lot[0] -= m
                        m_left -= m
                        if lot[0] == 0:
                            lots[sl][s].popleft()
                    if qty(sl, s) == 0:
                        closed[sl].append({"symbol": s, "opened": str(opened[sl].get(s, t).date()),
                                           "closed": str(t.date()), "pnl": spell[sl][s]})
                        spell[sl][s] = 0.0
            prev_w[sl] = w
    out = {}
    last = days[-1] if days else None
    for sl in SLEEVES:
        unreal, positions = 0.0, []
        for s, ls in lots[sl].items():
            q = sum(l[0] for l in ls)
            if q <= 0:
                continue
            cost = sum(l[0] * l[1] for l in ls)
            px = float(data[s].close.loc[last])
            unreal += q * px - cost
            positions.append({"symbol": s, "qty": q, "avg_entry": cost / q, "last": px, "unrealized_pl": q * px - cost})
        pnls = [c["pnl"] for c in closed[sl]]
        wins, gp, gl = sum(p > 0 for p in pnls), sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
        st = {"name": NAMES[sl], "realized_pl": realized[sl], "unrealized_pl": unreal,
              "closed_trades_count": len(pnls), "open_positions": positions,
              "sleeve_equity": equity0 / len(SLEEVES) + realized[sl] + unreal}
        if pnls:
            st["win_pct"] = round(100 * wins / len(pnls), 1)
        if pnls and gl > 0:
            st["profit_factor"] = round(gp / gl, 2)
        out[sl] = st
    return out
