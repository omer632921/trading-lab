"""Fake-money account that trades the Scanner picks once a day.
No exchange, no real money. Fixed rules, written down here:
 - Start with $10,000 of pretend cash.
 - Buy the highest-scoring coins/stocks that are in an uptrend
   (skipping coins already up over 80% in 7 days or 150% in 30 days).
 - Max 10 positions (max 5 crypto, max 5 stocks). Each buy is 5% of the account.
 - Sell when the trend is lost, or the price falls 10% below the purchase price.
 - After a stop-loss, do not buy that asset again for 7 days.
 - Costs: 0.1% fee on every trade, plus slippage (0.3% crypto, 0.05% stocks).
 - Stocks only trade on a new trading day (their prices are daily closes)."""
from datetime import datetime, timedelta

START = 10_000.0
POS_PCT = 0.05
MAX_POS, MAX_PER = 10, 5
STOP = 0.10
FEE = 0.001
SLIP = {"crypto": 0.003, "stocks": 0.0005}
COOLDOWN_DAYS = 7


def _key(u, r):
    return u, (r["id"] if u == "crypto" else r["symbol"])


def new_state(date):
    return {"start": START, "started": date, "cash": START, "positions": {}, "trades": [],
            "history": [], "cooldown": {}, "last_prices": {}, "bench": 1.0, "stock_date": None,
            "fees_paid": 0.0}


def step(state, date, crypto_rows, stock_rows, stock_last_date):
    """crypto_rows / stock_rows may be None when that source failed today."""
    s = state if state and state.get("positions") is not None else new_state(date)
    rows = {}
    for u, rr in (("crypto", crypto_rows), ("stocks", stock_rows)):
        for r in rr or []:
            if r.get("price"):
                rows[_key(u, r)] = r
    pk = lambda k: f"{k[0]}:{k[1]}"

    # benchmark: average daily move of everything scanned (no trading, no fees)
    prev = s["last_prices"]
    moves = [r["price"] / prev[pk(k)] - 1 for k, r in rows.items() if prev.get(pk(k))]
    if len(moves) >= 20:
        s["bench"] *= 1 + sum(moves) / len(moves)
    for k, r in rows.items():
        prev[pk(k)] = r["price"]
    keep = set(map(pk, rows)) | set(s["positions"])
    s["last_prices"] = {k: v for k, v in prev.items() if k in keep}

    # mark open positions to market
    for p in s["positions"].values():
        r = rows.get((p["u"], p["id"]))
        if r:
            p["last"] = r["price"]

    new_stock_day = bool(stock_rows) and stock_last_date and stock_last_date != s.get("stock_date")
    can = lambda u: u == "crypto" and crypto_rows is not None or u == "stocks" and new_stock_day

    def equity():
        return s["cash"] + sum(p["qty"] * p["last"] for p in s["positions"].values())

    def trade(side, u, p_or_r, price, qty, why, pnl=None):
        fee = qty * price * FEE
        s["fees_paid"] += fee
        s["trades"].append({"date": date, "side": side, "u": u, "symbol": p_or_r["symbol"],
                            "name": p_or_r.get("name"), "price": round(price, 6),
                            "qty": qty, "value": round(qty * price, 2), "why": why,
                            "pnl": None if pnl is None else round(pnl, 2)})
        return fee

    # sells
    for k, p in list(s["positions"].items()):
        if not can(p["u"]):
            continue
        r = rows.get((p["u"], p["id"]))
        if not r:
            continue
        why = None
        if r["price"] <= p["entry"] * (1 - STOP):
            why = f"Stop-loss: price is {100 * (r['price'] / p['entry'] - 1):.1f}% from our purchase price"
            s["cooldown"][k] = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
        elif r.get("trend") != "Up":
            why = f"Trend is no longer Up (now {r.get('trend')})"
        if why:
            px = r["price"] * (1 - SLIP[p["u"]])
            fee = trade("SELL", p["u"], p, px, p["qty"], why, pnl=p["qty"] * px * (1 - FEE) - p["cost"])
            s["cash"] += p["qty"] * px - fee
            del s["positions"][k]

    # buys
    eq = equity()
    s["cooldown"] = {k: v for k, v in s["cooldown"].items() if v > date}
    for u, rr in (("crypto", crypto_rows), ("stocks", stock_rows)):
        if not can(u) or not rr:
            continue
        ranked = [r for r in rr if r.get("trend") == "Up" and r.get("price") and not r.get("flag")]
        ranked.sort(key=lambda r: -r["score"])
        for n, r in enumerate(ranked[:25], 1):
            k = _key(u, r)
            ks = pk(k)
            if ks in s["positions"] or ks in s["cooldown"]:
                continue
            if len(s["positions"]) >= MAX_POS or sum(p["u"] == u for p in s["positions"].values()) >= MAX_PER:
                break
            spend = min(eq * POS_PCT, s["cash"] / (1 + FEE))
            if spend < 50:
                break
            px = r["price"] * (1 + SLIP[u])
            qty = spend / px
            why = f"In an uptrend, score {r['score']}, ranked #{n} of today's {u} uptrends"
            fee = trade("BUY", u, r, px, qty, why)
            s["cash"] -= qty * px + fee
            s["positions"][ks] = {"u": u, "id": k[1], "symbol": r["symbol"], "name": r.get("name"),
                                  "qty": qty, "entry": px, "cost": qty * px + fee,
                                  "date": date, "last": r["price"]}
    if stock_rows is not None and stock_last_date:
        s["stock_date"] = stock_last_date

    eq = equity()
    s["history"] = [h for h in s["history"] if h["date"] != date] + [
        {"date": date, "equity": round(eq, 2), "bench": round(START * s["bench"], 2)}]
    s["history"] = s["history"][-400:]
    s["trades"] = s["trades"][-300:]
    s["equity"] = round(eq, 2)
    s["updated"] = date
    return s
