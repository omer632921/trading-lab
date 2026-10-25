"""Daily scanner: ranks crypto and S&P 500 stocks by recent trend strength,
runs basic market checks on new tokens, and keeps a scoreboard of how the
top picks did afterwards. It does NOT predict anything."""
import json, time
from datetime import datetime, timezone
from pathlib import Path
import requests
import paper_scan

OUT = Path("data/scan.json")
HIST = Path("data/scan_history.json")
UA = {"User-Agent": "trading-lab-scanner/1.0"}
CHAINS = {"solana", "ethereum", "base", "bsc"}
MEME_WORDS = ["pepe", "doge", "shib", "inu", "cat", "frog", "trump", "pump",
              "moon", "bonk", "wif", "ai agent", "baby", "elon", "meme"]
STABLE = {"usdt", "usdc", "dai", "tusd", "usde", "fdusd", "usdd", "pyusd", "usds", "usdp",
          "gusd", "busd", "frax", "lusd", "susd", "eurc", "eurt", "usdy", "usd0", "usdg",
          "rlusd", "usdtb", "usd1", "usdx", "crvusd", "gho", "usdt0"}
WRAPPED_WORDS = ("wrapped", "staked", "bridged", "restaked", "liquid staking", "stablecoin")


def get_json(url, params=None, tries=3):
    for i in range(tries):
        r = requests.get(url, params=params, headers=UA, timeout=40)
        if r.status_code == 429:
            time.sleep(30 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate limited: " + url)


def pct_rank(vals):
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    out = [0.5] * n
    for rank, i in enumerate(order):
        out[i] = rank / (n - 1) if n > 1 else 0.5
    return out


def sig(x):
    return float(f"{x:.6g}")


# ---------------------------------------------------------------- crypto
def fetch_crypto():
    rows = []
    for page in (1, 2):
        rows += get_json("https://api.coingecko.com/api/v3/coins/markets",
                         {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250,
                          "page": page, "price_change_percentage": "24h,7d,30d"})
        time.sleep(4)
    return rows


def build_crypto(raw):
    keep = []
    for c in raw:
        p, mc, vol = c.get("current_price"), c.get("market_cap") or 0, c.get("total_volume") or 0
        ch24 = c.get("price_change_percentage_24h_in_currency")
        ch7 = c.get("price_change_percentage_7d_in_currency")
        ch30 = c.get("price_change_percentage_30d_in_currency")
        if None in (p, ch24, ch7, ch30):
            continue
        sym, name = (c.get("symbol") or "").lower(), (c.get("name") or "").lower()
        if sym in STABLE or any(w in name for w in WRAPPED_WORDS):
            continue
        if abs(p - 1) < 0.03 and abs(ch30) < 3 and abs(ch7) < 2:
            continue
        if vol < 1_000_000 or mc < 50_000_000:
            continue
        keep.append({"id": c["id"], "symbol": (c.get("symbol") or "").upper(), "name": c.get("name"),
                     "price": p, "ch24": round(ch24, 2), "ch7": round(ch7, 2), "ch30": round(ch30, 2),
                     "mcap": mc, "vol": vol})
    r30, r7 = pct_rank([k["ch30"] for k in keep]), pct_rank([k["ch7"] for k in keep])
    for k, a, b in zip(keep, r30, r7):
        k["score"] = round(100 * (0.6 * a + 0.4 * b))
        k["trend"] = "Up" if k["ch7"] > 0 and k["ch30"] > 0 else "Down" if k["ch7"] < 0 and k["ch30"] < 0 else "Mixed"
        k["flag"] = "Already up a lot (extended)" if k["ch7"] > 80 or k["ch30"] > 150 else ""
    keep.sort(key=lambda k: -k["score"])
    return keep


# ---------------------------------------------------------------- stocks
FALLBACK = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V", "UNH",
            "XOM", "WMT", "MA", "COST", "HD", "PG", "NFLX", "BAC", "AMD"]


def sp500():
    try:
        import pandas as pd
        t = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                         storage_options=UA)[0]
        info = {}
        for _, r in t.iterrows():
            info[str(r["Symbol"]).replace(".", "-")] = (str(r["Security"]), str(r.get("GICS Sector", "")))
        if len(info) > 400:
            return info
    except Exception as e:
        print("S&P 500 list failed, using fallback:", e)
    return {s: (s, "") for s in FALLBACK}


def fetch_stocks():
    import yfinance as yf
    info = sp500()
    data = yf.download(list(info), period="1y", interval="1d", auto_adjust=True,
                       group_by="ticker", threads=True, progress=False)
    return info, data


def build_stocks(info, data):
    keep, last_date = [], ""
    top = set(data.columns.get_level_values(0))
    for s, (name, sector) in info.items():
        if s not in top:
            continue
        try:
            df = data[s].dropna(subset=["Close"])
            close, volu = df["Close"], df["Volume"]
            if len(close) < 100:
                continue
            price = float(close.iloc[-1])
            dvol = float((close * volu).iloc[-20:].mean())
            if dvol < 5_000_000:
                continue
            ma50 = float(close.iloc[-50:].mean())
            keep.append({"symbol": s, "name": name, "sector": sector, "price": round(price, 2),
                         "ch1": round((price / float(close.iloc[-2]) - 1) * 100, 2),
                         "r20": round((price / float(close.iloc[-21]) - 1) * 100, 2),
                         "r90": round((price / float(close.iloc[-91]) - 1) * 100, 2),
                         "vs50": round((price / ma50 - 1) * 100, 2), "dvol": dvol})
            last_date = max(last_date, str(close.index[-1].date()))
        except Exception:
            continue
    r90, r20 = pct_rank([k["r90"] for k in keep]), pct_rank([k["r20"] for k in keep])
    for k, a, b in zip(keep, r90, r20):
        k["score"] = round(100 * (0.6 * a + 0.4 * b))
        k["trend"] = "Up" if k["vs50"] > 0 and k["r90"] > 0 else "Down" if k["vs50"] < 0 and k["r90"] < 0 else "Mixed"
    keep.sort(key=lambda k: -k["score"])
    return keep, last_date


# ---------------------------------------------------------------- tokens
def fetch_pairs():
    base = "https://api.dexscreener.com"
    found = {}
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        try:
            for p in get_json(base + path):
                if p.get("chainId") in CHAINS and p.get("tokenAddress"):
                    found.setdefault(p["chainId"], set()).add(p["tokenAddress"])
        except Exception as e:
            print("discovery failed:", path, e)
        time.sleep(1.2)
    pairs = []
    for chain, addrs in found.items():
        addrs = sorted(addrs)
        for i in range(0, len(addrs), 30):
            try:
                pairs += get_json(f"{base}/tokens/v1/{chain}/{','.join(addrs[i:i + 30])}") or []
            except Exception as e:
                print("token lookup failed:", e)
            time.sleep(0.5)
    for w in MEME_WORDS:
        try:
            pairs += get_json(base + "/latest/dex/search", {"q": w}).get("pairs", [])
        except Exception as e:
            print("search failed:", w, e)
        time.sleep(0.4)
    return pairs


def check_token(p, now_ms):
    fails, warns = [], []
    liq = (p.get("liquidity") or {}).get("usd") or 0
    vol = (p.get("volume") or {}).get("h24") or 0
    tx = (p.get("txns") or {}).get("h24") or {}
    buys, sells = tx.get("buys") or 0, tx.get("sells") or 0
    created = p.get("pairCreatedAt")
    age_h = (now_ms - created) / 3.6e6 if created else None
    fdv = p.get("fdv") or p.get("marketCap") or 0
    ch24 = (p.get("priceChange") or {}).get("h24")
    if age_h is None or age_h < 24:
        fails.append("Younger than 24 hours")
    elif age_h < 168:
        warns.append("Under 7 days old")
    if liq < 50_000:
        fails.append("Liquidity under $50k")
    elif liq < 150_000:
        warns.append("Thin liquidity (under $150k)")
    if vol < 25_000:
        fails.append("24h volume under $25k")
    if buys + sells < 100:
        fails.append("Fewer than 100 trades in 24h")
    if sells == 0 and buys > 50:
        fails.append("No sells at all (possible honeypot)")
    elif buys + sells > 0 and sells / (buys + sells) < 0.2:
        warns.append("Very few sells")
    if liq > 0 and vol / liq > 15:
        warns.append("Volume far above liquidity (possible wash trading or pump)")
    if liq > 0 and fdv / liq > 200:
        warns.append("Value is huge compared with liquidity (hard to sell)")
    if ch24 is not None and ch24 > 300:
        warns.append("Up over 300% in a day (pump risk)")
    if ch24 is not None and ch24 < -50:
        warns.append("Down over 50% in a day")
    status = "FAIL" if fails else "WARN" if warns else "PASS"
    pc = p.get("priceChange") or {}
    return {"chain": p.get("chainId"), "symbol": (p.get("baseToken") or {}).get("symbol"),
            "name": (p.get("baseToken") or {}).get("name"), "url": p.get("url"),
            "address": (p.get("baseToken") or {}).get("address"),
            "price": p.get("priceUsd"), "age_h": round(age_h, 1) if age_h is not None else None,
            "liq": liq, "vol": vol, "buys": buys, "sells": sells, "fdv": fdv,
            "ch1": pc.get("h1"), "ch6": pc.get("h6"), "ch24": ch24,
            "status": status, "reasons": fails + warns}


EVM_ID = {"ethereum": "1", "bsc": "56", "base": "8453"}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def goplus_evm(chain, addrs):
    """Contract checks for EVM tokens. Returns {address_lower: (danger, warn)}."""
    out = {}
    for i in range(0, len(addrs), 25):
        batch = addrs[i:i + 25]
        try:
            res = get_json(f"https://api.gopluslabs.io/api/v1/token_security/{EVM_ID[chain]}",
                           {"contract_addresses": ",".join(batch)}).get("result") or {}
        except Exception as e:
            print("goplus failed:", chain, str(e)[:80])
            continue
        for a, d in res.items():
            if not isinstance(d, dict) or not d:
                continue
            bad, warn = [], []
            if d.get("is_honeypot") == "1":
                bad.append("Honeypot: you may not be able to sell")
            if d.get("cannot_sell_all") == "1":
                bad.append("Cannot sell all tokens")
            if d.get("is_open_source") == "0":
                bad.append("Contract code not published")
            if d.get("hidden_owner") == "1":
                bad.append("Hidden owner")
            if d.get("selfdestruct") == "1":
                bad.append("Contract can self-destruct")
            if d.get("transfer_pausable") == "1":
                warn.append("Owner can pause trading")
            if d.get("is_blacklisted") == "1":
                warn.append("Owner can blacklist wallets")
            if d.get("is_mintable") == "1":
                warn.append("Owner can mint more tokens")
            if d.get("can_take_back_ownership") == "1":
                warn.append("Ownership can be taken back")
            if d.get("is_proxy") == "1":
                warn.append("Upgradeable contract (code can change)")
            for k, label in (("buy_tax", "Buy"), ("sell_tax", "Sell")):
                t = _f(d.get(k))
                if t is not None and t > 0.10:
                    bad.append(f"{label} tax {t * 100:.0f}%")
                elif t is not None and t > 0.03:
                    warn.append(f"{label} tax {t * 100:.0f}%")
            holders = [h for h in (d.get("holders") or []) if isinstance(h, dict)]
            top = sum((_f(h.get("percent")) or 0) for h in holders
                      if h.get("is_contract") != 1 and not h.get("is_locked"))
            if holders and top > 0.5:
                bad.append(f"Top wallets hold {top * 100:.0f}% of supply")
            elif holders and top > 0.25:
                warn.append(f"Top wallets hold {top * 100:.0f}% of supply")
            owner = _f(d.get("owner_percent"))
            if owner is not None and owner > 0.05:
                warn.append(f"Owner holds {owner * 100:.0f}%")
            out[a.lower()] = (bad, warn)
        time.sleep(1)
    return out


def rugcheck_sol(addrs):
    """Contract checks for Solana tokens. Returns {address: (danger, warn)}."""
    out = {}
    for a in addrs:
        try:
            d = get_json(f"https://api.rugcheck.xyz/v1/tokens/{a}/report/summary", tries=2)
        except Exception as e:
            print("rugcheck failed:", str(e)[:80])
            time.sleep(1)
            continue
        bad, warn = [], []
        for r in d.get("risks") or []:
            name = str(r.get("name") or "risk")[:80]
            (bad if r.get("level") == "danger" else warn).append(name)
        out[a] = (bad, warn)
        time.sleep(0.7)
    return out


def contract_checks(rows, limit=120):
    """Adds contract results to rows that passed the market checks. A token whose
    contract could not be checked can never show PASS."""
    todo = [r for r in rows if r["status"] != "FAIL" and r.get("address")][:limit]
    by_chain = {}
    for r in todo:
        by_chain.setdefault(r["chain"], []).append(r["address"])
    found = {}
    for chain, addrs in by_chain.items():
        if chain in EVM_ID:
            for k, v in goplus_evm(chain, addrs).items():
                found[(chain, k)] = v
        elif chain == "solana":
            for k, v in rugcheck_sol(addrs).items():
                found[(chain, k.lower())] = v
    for r in rows:
        r["contract"] = "skipped"
        if r not in todo:
            continue
        hit = found.get((r["chain"], (r.get("address") or "").lower()))
        if hit is None:
            r["contract"] = "unchecked"
            r["reasons"].append("Contract could not be checked")
            if r["status"] == "PASS":
                r["status"] = "WARN"
            continue
        bad, warn = hit
        r["contract"] = "danger" if bad else "warn" if warn else "clean"
        r["reasons"] = bad + r["reasons"] + warn
        if bad:
            r["status"] = "FAIL"
        elif warn and r["status"] == "PASS":
            r["status"] = "WARN"
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    rows.sort(key=lambda r: (order[r["status"]], -r["liq"]))
    return rows


def build_tokens(pairs, now_ms):
    best = {}
    for p in pairs:
        if p.get("chainId") not in CHAINS:
            continue
        addr = (p.get("baseToken") or {}).get("address")
        if not addr:
            continue
        key = (p["chainId"], addr)
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if key not in best or liq > ((best[key].get("liquidity") or {}).get("usd") or 0):
            best[key] = p
    rows = [check_token(p, now_ms) for p in best.values()]
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    rows.sort(key=lambda r: (order[r["status"]], -r["liq"]))
    return rows[:200]


# ---------------------------------------------------------------- scoreboard
def median(v):
    v = sorted(v)
    n = len(v)
    return None if n == 0 else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def scoreboard(hist, horizon=7):
    res = {}
    for u in ("crypto", "stocks"):
        tests, wins, excess = 0, 0, []
        for i, s in enumerate(hist):
            sd = datetime.strptime(s["date"], "%Y-%m-%d")
            later = next((t for t in hist[i + 1:]
                          if 0 <= (datetime.strptime(t["date"], "%Y-%m-%d") - sd).days - horizon <= 3), None)
            if not later or not s["picks"].get(u):
                continue
            a, b = s["prices"][u], later["prices"][u]
            allr = [b[k] / a[k] - 1 for k in a if k in b and a[k] > 0]
            pr = [b[k] / a[k] - 1 for k in s["picks"][u] if k in b and k in a and a[k] > 0]
            if len(allr) < 20 or len(pr) < 5:
                continue
            ex = (sum(pr) / len(pr) - median(allr)) * 100
            tests += 1
            wins += ex > 0
            excess.append(ex)
        res[u] = {"tests": tests, "wins": wins,
                  "avg_excess": round(sum(excess) / len(excess), 2) if excess else None}
    res["days"] = len(hist)
    res["horizon"] = horizon
    return res


# ---------------------------------------------------------------- main
def main():
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    hist = json.loads(HIST.read_text()) if HIST.exists() else []
    out = {"updated": stamp, "errors": {}}
    OUT.parent.mkdir(parents=True, exist_ok=True)

    crypto_rows = stock_rows = None
    last_date = None
    try:
        crypto_rows = build_crypto(fetch_crypto())
        out["crypto"] = {"updated": stamp, "rows": crypto_rows}
        print("crypto rows:", len(crypto_rows))
    except Exception as e:
        out["errors"]["crypto"] = str(e)[:200]
        out["crypto"] = old.get("crypto")
    try:
        info, data = fetch_stocks()
        stock_rows, last_date = build_stocks(info, data)
        out["stocks"] = {"updated": stamp, "last_date": last_date, "rows": stock_rows}
        print("stock rows:", len(stock_rows))
    except Exception as e:
        out["errors"]["stocks"] = str(e)[:200]
        out["stocks"] = old.get("stocks")
    try:
        rows = build_tokens(fetch_pairs(), now.timestamp() * 1000)
        rows = contract_checks(rows)
        out["tokens"] = {"updated": stamp, "rows": rows}
        print("token rows:", len(rows), "pass:", sum(r["status"] == "PASS" for r in rows))
    except Exception as e:
        out["errors"]["tokens"] = str(e)[:200]
        out["tokens"] = old.get("tokens")

    try:
        out["paper"] = paper_scan.step(old.get("paper"), now.strftime("%Y-%m-%d"),
                                       crypto_rows, stock_rows, last_date if stock_rows else None)
        print("paper account:", out["paper"]["equity"], "positions:", len(out["paper"]["positions"]))
    except Exception as e:
        out["errors"]["paper"] = str(e)[:200]
        out["paper"] = old.get("paper")

    snap = {"date": now.strftime("%Y-%m-%d"), "picks": {}, "prices": {}}
    if crypto_rows:
        snap["prices"]["crypto"] = {r["id"]: sig(r["price"]) for r in crypto_rows}
        snap["picks"]["crypto"] = [r["id"] for r in crypto_rows if r["trend"] == "Up"][:10]
    if stock_rows:
        snap["prices"]["stocks"] = {r["symbol"]: sig(r["price"]) for r in stock_rows}
        snap["picks"]["stocks"] = [r["symbol"] for r in stock_rows if r["trend"] == "Up"][:10]
    if snap["prices"]:
        for u in ("crypto", "stocks"):
            snap["prices"].setdefault(u, {})
            snap["picks"].setdefault(u, [])
        hist = [h for h in hist if h["date"] != snap["date"]] + [snap]
        hist = hist[-45:]
    out["scoreboard"] = scoreboard(hist)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    HIST.write_text(json.dumps(hist, separators=(",", ":")))
    if not any(out.get(k) for k in ("crypto", "stocks", "tokens")):
        raise SystemExit("nothing fetched")
    print("saved. errors:", out["errors"])


if __name__ == "__main__":
    main()
