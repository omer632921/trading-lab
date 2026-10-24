import json, os, requests
from datetime import datetime, timezone
from pathlib import Path

COINS = {"Bitcoin": "XBTUSD", "Ethereum": "ETHUSD", "Solana": "SOLUSD"}
LOG = Path("data/rule_log.json")
PAPER = Path("data/paper.json")
LOG.parent.mkdir(parents=True, exist_ok=True)
TOPIC = os.environ.get("NTFY_TOPIC", "")
START_CASH, FEE = 10000.0, 0.001   # paper account: $10,000, 0.1% fee per trade

def api(path, **params):
    r = requests.get("https://api.kraken.com/0/public/" + path, params=params, timeout=30).json()
    if r.get("error"):
        raise RuntimeError(r["error"][0])
    return r["result"]

def closes(pair):
    res = api("OHLC", pair=pair, interval=1440)
    rows = [v for k, v in res.items() if k != "last"][0][:-1]
    return [float(x[4]) for x in rows]

def live_price(pair):
    return float(list(api("Ticker", pair=pair).values())[0]["c"][0])

def notify(title, text):
    if TOPIC:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=text.encode(),
                      headers={"Title": title}, timeout=15)

log = json.loads(LOG.read_text()) if LOG.exists() else []
paper = json.loads(PAPER.read_text()) if PAPER.exists() else None
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
today = now[:10]

data = {}
for name, pair in COINS.items():
    c = closes(pair)
    avg = sum(c[-50:]) / 50
    data[name] = {"state": "IN" if c[-1] > avg else "OUT",
                  "close": c[-1], "avg": avg, "px": live_price(pair)}

if paper is None:
    slot = START_CASH / len(COINS)
    paper = {"start": now, "start_cash": START_CASH, "fee": FEE,
             "slots": {n: {"cash": slot, "units": 0.0, "start_px": d["px"]} for n, d in data.items()},
             "trades": [], "equity": []}

for name, d in data.items():
    prev = next((e["state"] for e in reversed(log) if e["coin"] == name), None)
    changed = prev is not None and prev != d["state"]
    log.append({"ts": now, "coin": name, "state": d["state"], "price": d["close"],
                "avg50": round(d["avg"], 2), "changed": changed})
    s, px = paper["slots"][name], d["px"]
    if d["state"] == "IN" and s["units"] == 0:
        paper["trades"].append({"ts": now, "coin": name, "side": "BUY",
                                "price": px, "value": round(s["cash"], 2)})
        s["units"] = s["cash"] * (1 - FEE) / px
        s["cash"] = 0.0
        notify("Paper BUY", f"{name} flipped to IN. Paper buy at {px:,.2f}")
    elif d["state"] == "OUT" and s["units"] > 0:
        proceeds = s["units"] * px * (1 - FEE)
        paper["trades"].append({"ts": now, "coin": name, "side": "SELL",
                                "price": px, "value": round(proceeds, 2)})
        s["cash"], s["units"] = proceeds, 0.0
        notify("Paper SELL", f"{name} flipped to OUT. Paper sell at {px:,.2f}")
    print(name, d["state"], "changed" if changed else "no change")

slot0 = paper["start_cash"] / len(COINS)
paper_val = sum(s["cash"] + s["units"] * data[n]["px"] for n, s in paper["slots"].items())
hold_val = sum(slot0 / s["start_px"] * (1 - FEE) * data[n]["px"] for n, s in paper["slots"].items())
point = {"date": today, "paper": round(paper_val, 2), "hold": round(hold_val, 2)}
if paper["equity"] and paper["equity"][-1]["date"] == today:
    paper["equity"][-1] = point
else:
    paper["equity"].append(point)
print("paper account", point)

LOG.write_text(json.dumps(log, indent=1))
PAPER.write_text(json.dumps(paper, indent=1))
