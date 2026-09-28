"""
JSON snapshot of the portfolio bot's Alpaca paper account for the Algo Desk dashboard (same output format as the other
bots' dashboard_snapshot.py): account, open positions, and closed trades reconstructed FIFO per symbol from filled
orders. Prints ONE json blob between marker lines so the dashboard routine can grep it out of the Action's log.

Read-only: no orders are placed.
Env: ALPACA_API_KEY, ALPACA_SECRET_KEY
"""
import json, os
from collections import defaultdict, deque
from datetime import datetime, timezone
import requests

BASE = "https://paper-api.alpaca.markets"
H = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
BOT_ID = "portfolio_moc"
BOT_LABEL = "Portfolio MOC (A1 + D + TSY)"
STARTING_EQUITY = 100000.0


def get(path, params=None):
    r = requests.get(BASE + path, headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def all_orders():
    out, after = [], "2000-01-01T00:00:00Z"
    while True:
        page = get("/v2/orders", {"status": "all", "limit": 500, "direction": "asc", "after": after, "nested": "false"})
        if not page:
            return out
        out += page
        if len(page) < 500:
            return out
        after = page[-1]["submitted_at"]


def closed_trades(orders):
    """FIFO-match filled quantities per symbol; the bot is long-only, so a sell closes earlier buys."""
    fills = [o for o in orders if float(o.get("filled_qty") or 0) > 0 and o.get("filled_avg_price")]
    fills.sort(key=lambda o: o.get("filled_at") or o.get("updated_at") or "")
    lots, closed = defaultdict(deque), []
    for o in fills:
        s, q, px = o["symbol"], float(o["filled_qty"]), float(o["filled_avg_price"])
        sign = 1 if o["side"] == "buy" else -1
        while q > 1e-9 and lots[s] and lots[s][0]["sign"] != sign:
            lot = lots[s][0]
            m = min(q, lot["qty"])
            closed.append({"closed_at": o.get("filled_at"), "opened_at": lot["t"], "symbol": s, "qty": m,
                           "entry": lot["price"], "exit": px, "pnl": (px - lot["price"]) * m * lot["sign"]})
            q -= m
            lot["qty"] -= m
            if lot["qty"] <= 1e-9:
                lots[s].popleft()
        if q > 1e-9:
            lots[s].append({"sign": sign, "qty": q, "price": px, "t": o.get("filled_at")})
    return closed


def strategy_sleeves(orders, equity):
    """Per-strategy shadow ledger (attribution.py) from the first fill to the last COMPLETED session."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    import pandas as pd
    import attribution, portfolio_bot as PB, portfolio_rules as R
    ny = ZoneInfo("America/New_York")
    fills = [o for o in orders if (o.get("client_order_id") or "").startswith("pf-") and o.get("filled_at")
             and float(o.get("filled_qty") or 0) > 0]
    empty = {sl: {"name": attribution.NAMES[sl], "realized_pl": 0.0, "unrealized_pl": 0.0, "closed_trades_count": 0,
                  "open_positions": []} for sl in attribution.SLEEVES}
    if not fills:
        return {"sleeves": empty, "attribution_note": "no fills yet"}
    fill_px = {}
    for o in fills:
        d = pd.Timestamp(pd.Timestamp(o["filled_at"]).tz_convert(ny).date())
        fill_px[(d, o["symbol"])] = float(o["filled_avg_price"])
    start = min(d for d, _ in fill_px)
    now = datetime.now(ny)
    last_done = now.date() if now.hour * 60 + now.minute >= 16 * 60 + 20 else now.date() - timedelta(days=1)
    api = PB.Alpaca(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    raw = api.daily_bars(R.ALL_SYMBOLS, (start - pd.Timedelta(days=640)).date(), last_done)
    data = {s: pd.DataFrame([{"date": pd.Timestamp(b["t"][:10]), "open": b["o"], "high": b["h"], "low": b["l"],
                              "close": b["c"]} for b in raw.get(s, [])]).set_index("date") for s in R.ALL_SYMBOLS}
    cal, _ = PB.trading_calendar(api, last_done)
    led = attribution.sleeve_ledger(data, cal, start, pd.Timestamp(last_done), fill_px, STARTING_EQUITY)
    shadow = sum(v["realized_pl"] + v["unrealized_pl"] for v in led.values())
    return {"sleeves": led, "attribution_as_of": str(last_done),
            "attribution_gap": (equity - STARTING_EQUITY) - shadow,
            "attribution_note": "Per-strategy split is a shadow ledger at actual MOC fill prices; gap = real account P&L minus the sum of the three"}


def run():
    acct = get("/v2/account")
    equity = float(acct["equity"])
    positions = [{
        "symbol": p["symbol"], "side": p["side"].upper(), "qty": float(p["qty"]),
        "entry": float(p["avg_entry_price"]), "current": float(p["current_price"]),
        "unrealized_pl": float(p["unrealized_pl"]), "unrealized_pl_pct": float(p["unrealized_plpc"]) * 100,
    } for p in get("/v2/positions")]
    orders = all_orders()
    trades = closed_trades(orders)
    unreal = sum(p["unrealized_pl"] for p in positions)
    pending = [{"symbol": o["symbol"], "side": o["side"], "qty": o["qty"], "type": f'{o["type"]}/{o["time_in_force"]}'}
               for o in get("/v2/orders", {"status": "open", "limit": 500})]
    try:
        attrib = strategy_sleeves(orders, equity)
    except Exception as e:                      # attribution must never break the main snapshot
        attrib = {"attribution_error": f"{type(e).__name__}: {e}"[:300]}
    snap = {
        "bot_id": BOT_ID, "label": BOT_LABEL, "broker": "Alpaca (stocks)", "currency": "USD",
        "config": "A1 + D + TSY-ME · 1/3 each · MOC",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "equity": equity, "balance": equity - unreal, "unrealized_pl": unreal,
        "realized_pl_alltime": sum(t["pnl"] for t in trades),
        "realized_pl_basis": "Gross execution P&L (FIFO per symbol); broker fees excluded",
        "starting_equity": STARTING_EQUITY,
        "open_positions": positions, "pending_orders": pending, "closed_trades": trades,
        **attrib,
    }
    print("===SNAPSHOT_JSON_START===")
    print(json.dumps(snap))
    print("===SNAPSHOT_JSON_END===")


if __name__ == "__main__":
    run()
