import json
from datetime import datetime, timezone
from pathlib import Path
import yfinance as yf

SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT"]
OUT = Path("data/stocks.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

stocks = {}
for s in SYMBOLS:
    try:
        h = yf.Ticker(s).history(period="3mo")[["Open", "High", "Low", "Close"]].dropna()
        candles = [[round(float(v), 2) for v in row] for row in h.values][-45:]
        last, prev = candles[-1][3], candles[-2][3]
        stocks[s] = {"price": last, "change_pct": round((last / prev - 1) * 100, 2),
                     "last_date": str(h.index[-1].date()), "candles": candles}
        print(s, last)
    except Exception as e:
        print(s, "failed:", e)

if not stocks:
    raise SystemExit("no stock data fetched")

old = json.loads(OUT.read_text()) if OUT.exists() else {}
if old.get("stocks") == stocks:
    print("no change")
else:
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "stocks": stocks}, indent=1))
    print("saved")
