"""Read-only: print this account's orders (status, fills, reject reasons) - diagnostics only, places nothing."""
import os, json, requests
H = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
r = requests.get("https://paper-api.alpaca.markets/v2/orders", headers=H, params={"status": "all", "limit": 100, "direction": "asc"}, timeout=20)
r.raise_for_status()
for o in r.json():
    print(json.dumps({k: o.get(k) for k in ("client_order_id", "symbol", "side", "qty", "type", "time_in_force", "status",
                                               "submitted_at", "filled_at", "filled_qty", "filled_avg_price", "canceled_at",
                                               "expired_at", "failed_at", "replaced_by")}))
a = requests.get("https://paper-api.alpaca.markets/v2/account/activities", headers=H, params={"page_size": 50}, timeout=20)
print("ACTIVITIES", a.status_code)
for x in a.json() if a.ok else []:
    print(json.dumps({k: x.get(k) for k in ("activity_type", "symbol", "side", "qty", "price", "transaction_time", "type", "order_status")}))
