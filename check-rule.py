import json, os, requests
from datetime import datetime, timezone
from pathlib import Path

COINS = {"Bitcoin": "XBTUSD", "Ethereum": "ETHUSD", "Solana": "SOLUSD"}
FILE = Path("data/rule_log.json")
FILE.parent.mkdir(parents=True, exist_ok=True)
TOPIC = os.environ.get("NTFY_TOPIC", "")

def closes(pair):
    r = requests.get("https://api.kraken.com/0/public/OHLC",
                     params={"pair": pair, "interval": 1440}, timeout=30).json()
    rows = [v for k, v in r["result"].items() if k != "last"][0][:-1]
    return [float(x[4]) for x in rows]

log = json.loads(FILE.read_text()) if FILE.exists() else []
now = datetime.now(timezone.utc).isoformat(timespec="seconds")

for name, pair in COINS.items():
    c = closes(pair)
    avg = sum(c[-50:]) / 50
    state = "IN" if c[-1] > avg else "OUT"
    prev = next((e["state"] for e in reversed(log) if e["coin"] == name), None)
    changed = prev is not None and prev != state
    log.append({"ts": now, "coin": name, "state": state,
                "price": c[-1], "avg50": round(avg, 2), "changed": changed})
    print(name, state, "changed" if changed else "no change")
    if changed and TOPIC:
        requests.post(f"https://ntfy.sh/{TOPIC}", timeout=15,
                      headers={"Title": "Trend rule changed"},
                      data=f"{name} is now {state}. Close {c[-1]:,.2f} vs 50-day avg {avg:,.2f}".encode())

FILE.write_text(json.dumps(log, indent=1))
