"""
Strategy bot: A1 index mean reversion + D asset rotation + TSY month-end  - Alpaca PAPER only.
STRATEGY=ALL (default): all three in ONE dedicated account, 1/3 of equity each, positions netted per symbol.
STRATEGY=A1|D|TSY: a single strategy at full size in its own dedicated account.

How it works (one run per trading day, ~15:35-15:48 ET):
  1. Checks it is a trading day and inside the pre-close window (close-25min .. close-11min, so half-days work too).
  2. Pulls ~400 days of adjusted daily bars (SIP history through yesterday) + today's live IEX snapshot, which stands
     in for today's close (validated: a 15:50 estimate matches the true-close A1 signal ~98.5% of days).
  3. Computes target weights with portfolio_rules.target_weights() - the same function the replay backtest uses.
  4. Sends MARKET-ON-CLOSE orders (time_in_force="cls") for the differences between target and current shares.
It is STATELESS: targets are recomputed from market data every run, so a missed day is caught up on the next run.
It REQUIRES A DEDICATED ACCOUNT: it aborts if it finds positions/orders it does not recognise.

Env: STRATEGY (ALL|A1|D|TSY, default ALL), ALPACA_API_KEY, ALPACA_SECRET_KEY (if empty the strategy is "not configured" and the run
     exits cleanly); DRY_RUN=1 (compute + log, place nothing);
     FORCE_WINDOW=1 (ignore the time window - for dry-run testing only; refused unless DRY_RUN=1).
"""
import os, sys, math, json, time, logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import portfolio_rules as R

TRADING_BASE_URL = "https://paper-api.alpaca.markets"   # paper only - never change without a deliberate decision
DATA_BASE_URL = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
DRIFT_BAND = 0.25          # re-size an existing holding only when it drifts >25% from target (same as replay)
WINDOW_OPEN_MIN, WINDOW_CLOSE_MIN = 25, 11   # minutes before the close
HISTORY_DAYS = 420
STRATEGIES = ("ALL", "A1", "D", "TSY")


def prefix_for(strategy):
    return "pf" if strategy == "ALL" else f"pf{strategy.lower()}"    # client_order_id prefix: pf- / pfa1- / pfd- / pftsy-


def config_for(strategy):
    """(symbols, order prefix, sleeves) for a STRATEGY value."""
    if strategy == "ALL":
        return R.ALL_SYMBOLS, prefix_for(strategy), dict(R.SLEEVE)
    return R.UNIVERSE[strategy], prefix_for(strategy), {strategy: 1.0}

log = logging.getLogger("portfolio_bot")


class Alpaca:
    def __init__(self, key, secret, session=None):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.s = session or requests.Session()

    def _get(self, base, path, params=None, tries=3):
        for k in range(tries):
            r = self.s.get(base + path, headers=self.h, params=params, timeout=30)
            if r.status_code < 500:
                break
            time.sleep(2 * (k + 1))
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def clock(self): return self._get(TRADING_BASE_URL, "/v2/clock")
    def account(self): return self._get(TRADING_BASE_URL, "/v2/account")
    def positions(self): return self._get(TRADING_BASE_URL, "/v2/positions")
    def open_orders(self): return self._get(TRADING_BASE_URL, "/v2/orders", {"status": "open", "limit": 500})

    def calendar(self, start, end):
        return self._get(TRADING_BASE_URL, "/v2/calendar", {"start": str(start), "end": str(end)})

    def daily_bars(self, symbols, start, end):
        out, token = {}, None
        while True:
            p = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": str(start), "end": str(end),
                 "adjustment": "all", "feed": "sip", "limit": 10000}
            if token:
                p["page_token"] = token
            j = self._get(DATA_BASE_URL, "/v2/stocks/bars", p)
            for s, bars in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend(bars)
            token = j.get("next_page_token")
            if not token:
                return out

    def snapshots(self, symbols):
        return self._get(DATA_BASE_URL, "/v2/stocks/snapshots", {"symbols": ",".join(symbols), "feed": "iex"})

    def submit(self, order):
        r = self.s.post(TRADING_BASE_URL + "/v2/orders", headers=self.h, json=order, timeout=30)
        if r.status_code == 422 and "client_order_id" in r.text:
            return {"status": "duplicate", "client_order_id": order["client_order_id"]}
        if r.status_code >= 400:
            raise RuntimeError(f"order {order} -> {r.status_code}: {r.text[:300]}")
        return r.json()


# ---------------------------------------------------------------- data assembly
def build_data(api, today, symbols):
    """dict symbol -> DataFrame(open, high, low, close) with a provisional row for `today` from the live snapshot."""
    start = today - timedelta(days=int(HISTORY_DAYS * 1.5))
    raw = api.daily_bars(symbols, start, today - timedelta(days=1))
    snaps = api.snapshots(symbols)
    data = {}
    for s in symbols:
        bars = raw.get(s, [])
        df = pd.DataFrame([{"date": pd.Timestamp(b["t"][:10]), "open": b["o"], "high": b["h"], "low": b["l"],
                            "close": b["c"]} for b in bars]).set_index("date") if bars else \
            pd.DataFrame(columns=["open", "high", "low", "close"])
        df = df[df.index < pd.Timestamp(today)]
        sn = snaps.get(s) or {}
        lt = sn.get("latestTrade") or {}
        last = lt.get("p")
        if last is None or pd.Timestamp(lt.get("t", "1970-01-01")).tz_convert(NY).date() != today:
            raise RuntimeError(f"{s}: no trade today {today} in the live snapshot (latestTrade={lt}) - cannot price, aborting")
        db = sn.get("dailyBar") or {}
        if db and db.get("t", "")[:10] == str(today):   # bars are stamped at NY midnight = 04:00Z/05:00Z, same date
            o_, h_, l_ = db["o"], max(db["h"], last), min(db["l"], last)
        else:                                  # thin IEX coverage: degrade to the last trade (only affects A1's ATR)
            log.warning("%s: no IEX daily bar yet today - using last trade for open/high/low", s)
            o_ = h_ = l_ = last
        row = pd.DataFrame({"open": [o_], "high": [h_], "low": [l_], "close": [last]}, index=[pd.Timestamp(today)])
        data[s] = pd.concat([df, row]).astype(float)
    return data


def trading_calendar(api, today):
    cal = api.calendar(today - timedelta(days=int(HISTORY_DAYS * 1.5)), today + timedelta(days=45))
    days = pd.DatetimeIndex([pd.Timestamp(c["date"]) for c in cal])
    close_times = {pd.Timestamp(c["date"]): c["close"] for c in cal}
    return days, close_times


# ---------------------------------------------------------------- order planning (pure, unit-tested)
def plan_orders(weights, prev_weights, equity, prices, positions, open_orders, today, symbols, prefix):
    """Returns (orders, notes). positions: {sym: qty}. open_orders: list of Alpaca order dicts."""
    notes, orders = [], []
    pending = {o["symbol"] for o in open_orders if o.get("client_order_id", "").startswith(f"{prefix}-{today:%Y%m%d}")}
    for s in symbols:
        w, pw = weights.get(s, 0.0), prev_weights.get(s, 0.0)
        q = int(positions.get(s, 0))
        px = prices[s]
        tq = int(math.floor(w * equity / px)) if w > 0 else 0
        if s in pending:
            notes.append(f"{s}: MOC order already queued today - skip"); continue
        if w == 0:
            trade = q != 0
        else:
            event = abs(w - pw) > 1e-12
            trade = event or abs(q - tq) > DRIFT_BAND * max(tq, 1)
        dq = tq - q
        if not trade or dq == 0:
            continue
        side = "buy" if dq > 0 else "sell"
        qty = abs(dq)
        if side == "sell" and qty > q:        # never go short
            qty = q
        if qty <= 0:
            continue
        orders.append({"symbol": s, "qty": str(qty), "side": side, "type": "market", "time_in_force": "cls",
                       "client_order_id": f"{prefix}-{today:%Y%m%d}-{s}-{side}"})
        notes.append(f"{s}: target w={w:.4f} ({tq} sh) held {q} -> {side} {qty}")
    return orders, notes


def check_dedicated(positions, open_orders, symbols, prefix):
    foreign = [p["symbol"] for p in positions if p["symbol"] not in symbols]
    foreign += [o["symbol"] for o in open_orders if not o.get("client_order_id", "").startswith(prefix + "-")]
    shorts = [p["symbol"] for p in positions if float(p["qty"]) < 0]
    if foreign or shorts:
        raise RuntimeError(f"account is not dedicated to this bot: foreign={foreign} shorts={shorts} - aborting, nothing placed")


# ---------------------------------------------------------------- main
def run(api, strategy, now=None, dry_run=False, force_window=False):
    if strategy not in STRATEGIES:
        raise ValueError(f"STRATEGY must be one of {STRATEGIES}, got {strategy!r}")
    symbols, prefix, sleeves = config_for(strategy)
    now = now or datetime.now(NY)
    today = now.date()
    cal_days, close_times = trading_calendar(api, today)
    if pd.Timestamp(today) not in cal_days:
        log.info("%s is not a trading day - nothing to do", today); return {"status": "holiday"}
    close_dt = datetime.combine(today, datetime.strptime(close_times[pd.Timestamp(today)], "%H:%M").time(), NY)
    win_open, win_close = close_dt - timedelta(minutes=WINDOW_OPEN_MIN), close_dt - timedelta(minutes=WINDOW_CLOSE_MIN)
    if not (win_open <= now <= win_close):
        if not (force_window and dry_run):
            log.info("outside the pre-close window %s-%s ET (now %s) - nothing to do", win_open.time(), win_close.time(), now.time())
            return {"status": "outside_window"}
        log.warning("FORCE_WINDOW in dry run: evaluating outside the window with the latest prices")

    data = build_data(api, today, symbols)
    t = pd.Timestamp(today)
    w, detail = R.target_weights(data, t, calendar=cal_days, sleeves=sleeves)
    yday = cal_days[cal_days < t][-1]
    hist = {s: df.loc[:yday] for s, df in data.items()}
    pw, _ = R.target_weights(hist, yday, calendar=cal_days, sleeves=sleeves)

    acct = api.account()
    equity = float(acct["equity"])
    pos_list, oo = api.positions(), api.open_orders()
    check_dedicated(pos_list, oo, symbols, prefix)
    positions = {p["symbol"]: float(p["qty"]) for p in pos_list}
    prices = {s: float(data[s].close.iloc[-1]) for s in symbols}
    orders, notes = plan_orders(w, pw, equity, prices, positions, oo, today, symbols, prefix)

    for k in (("A1", "D", "TSY") if strategy == "ALL" else (strategy,)):
        log.info("[%s] equity %.2f | %s holds after today's close: %s", strategy, equity, k, detail[k] or "nothing (cash)")
    log.info("target weights: %s", {k: round(v, 4) for k, v in w.items() if v})
    log.info("gross target %.3f", sum(w.values()))
    for n in notes:
        log.info("  %s", n)
    placed = []
    if dry_run:
        log.info("DRY RUN - %d order(s) planned, none sent", len(orders))
    else:
        for o in sorted(orders, key=lambda o: o["side"] != "sell"):   # sells first
            res = api.submit(o)
            placed.append(res)
            log.info("  submitted %s %s %s -> %s", o["side"], o["qty"], o["symbol"], res.get("status"))
    return {"status": "ok", "orders": orders, "weights": w, "detail": detail, "placed": placed}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dry = os.getenv("DRY_RUN", "0") == "1"
    force = os.getenv("FORCE_WINDOW", "0") == "1"
    if force and not dry:
        log.error("FORCE_WINDOW requires DRY_RUN=1 - refusing"); sys.exit(2)
    strategy = os.getenv("STRATEGY", "ALL") or "ALL"
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        log.warning("[%s] not configured (no ALPACA_API_KEY/SECRET for this strategy) - skipping", strategy)
        return
    api = Alpaca(key, secret)
    out = run(api, strategy, dry_run=dry, force_window=force)
    print(json.dumps({k: v for k, v in out.items() if k != "placed"}, default=str))


if __name__ == "__main__":
    main()
