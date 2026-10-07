#!/usr/bin/env python3
"""Watches real, token-less projects and alerts when their token goes live
AND the contract address is written on the project's official website."""
import json, time, re, datetime, urllib.request, urllib.parse, pathlib

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
STATE_F = ROOT / "state.json"
CHAINS = CFG.get("chains", ["solana", "robinhood"])
MIN_TVL = CFG.get("min_tvl", 1_000_000)
MAX_WATCH = CFG.get("max_watch", 150)
MIN_AGE = CFG.get("min_domain_age_days", 90)
TRY_HOURS = 72
UA = {"User-Agent": "Mozilla/5.0 (watchbot)"}

def get(url, t=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=t) as r:
        return json.loads(r.read().decode())

def page(url, t=15):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=t) as r:
            return r.read(600000).decode("utf-8", "ignore")
    except Exception:
        return ""

def tg(text):
    try:
        data = urllib.parse.urlencode({"chat_id": CFG["telegram_chat_id"], "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{CFG['telegram_token']}/sendMessage", data=data), timeout=15)
    except Exception as e:
        print("[err] telegram", e)

def host(url):
    h = urllib.parse.urlparse(url if "//" in url else "https://" + url).netloc.lower()
    return h[4:] if h.startswith("www.") else h

def same(a, b):
    return a == b or a.endswith("." + b) or b.endswith("." + a)

def domain_age(d):
    try:
        root = ".".join(d.split(".")[-2:])
        j = get(f"https://rdap.org/domain/{root}", 15)
        for ev in j.get("events", []):
            if ev.get("eventAction") in ("registration", "registered"):
                reg = datetime.datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
                return (datetime.datetime.now(datetime.timezone.utc) - reg).days
    except Exception:
        pass
    return None

def build_watch(state):
    """Real projects with money in them, a website, an X account, and no token yet."""
    vetted = state.setdefault("vetted", {})
    try:
        protos = get("https://api.llama.fi/protocols", 60)
    except Exception as e:
        print("[err] defillama", e)
        return {}
    cands = []
    for p in protos:
        if (p.get("symbol") or "-").strip() not in ("-", "") or p.get("gecko_id"):
            continue
        if not p.get("url") or not p.get("twitter") or (p.get("tvl") or 0) < MIN_TVL:
            continue
        cands.append(p)
    cands.sort(key=lambda p: -(p.get("tvl") or 0))
    watch, new_checks = {}, 0
    for p in cands[:MAX_WATCH]:
        d = host(p["url"])
        if d not in vetted and new_checks < 40:
            new_checks += 1
            age = domain_age(d)
            if not page(p["url"]):
                continue
            vetted[d] = age is None or age >= MIN_AGE
        if vetted.get(d):
            watch[d] = {"name": p["name"], "url": p["url"], "tvl": p.get("tvl")}
    return watch

_site_cache = {}
def official_text(url):
    d = host(url)
    if d not in _site_cache:
        base = url.rstrip("/")
        _site_cache[d] = " ".join(page(base + x) for x in ("", "/token", "/tokenomics", "/docs")).lower()
    return _site_cache[d]

def scan(watch, state):
    alerted = set(state.setdefault("alerted", []))
    tried = state.setdefault("tried", {})
    now = time.time()
    for d, p in watch.items():
        try:
            res = get("https://api.dexscreener.com/latest/dex/search?q=" + urllib.parse.quote(p["name"]))
        except Exception as e:
            print("[err]", p["name"], e)
            continue
        time.sleep(0.3)
        for pr in res.get("pairs") or []:
            chain = pr.get("chainId")
            if chain not in CHAINS:
                continue
            tok = pr.get("baseToken") or {}
            ca = tok.get("address", "")
            k = f"{chain}:{ca}"
            if not ca or k in alerted:
                continue
            created = pr.get("pairCreatedAt")
            if created and now - created / 1000 > 7 * 86400:
                continue
            sites = [w.get("url", "") for w in (pr.get("info") or {}).get("websites", []) if w.get("url")]
            label = (tok.get("name", "") + " " + tok.get("symbol", "")).lower()
            if not (p["name"].lower() in label or any(same(host(s), d) for s in sites)):
                continue
            first = tried.setdefault(k, now)
            if now - first > TRY_HOURS * 3600:
                continue
            if ca.lower() not in official_text(p["url"]):
                print("[wait] CA not on official site yet:", p["name"], k)
                continue
            liq = (pr.get("liquidity") or {}).get("usd")
            tg("\n".join([
                "REAL PROJECT TOKEN LIVE",
                f"Project: {p['name']} (TVL ${p['tvl']:,.0f})" if p.get("tvl") else f"Project: {p['name']}",
                f"Chain: {chain}",
                f"Token: {tok.get('name','?')} (${tok.get('symbol','?')})",
                f"CA: {ca}",
                f"CA found on official site: {p['url']}",
                f"Liquidity: ${liq:,.0f}" if liq else "Liquidity: unknown",
                f"Chart: {pr.get('url','')}",
                "Check the CA on the official site yourself before buying."]))
            alerted.add(k)
            print("[ALERT]", k)
    state["alerted"] = sorted(alerted)
    state["tried"] = {k: v for k, v in tried.items() if now - v < 7 * 86400}

def main():
    state = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
    watch = build_watch(state)
    print(f"watching {len(watch)} projects")
    scan(watch, state)
    utc = datetime.datetime.now(datetime.timezone.utc)
    if utc.hour >= 8 and state.get("beat") != str(utc.date()):
        tg(f"Bot alive. Watching {len(watch)} real projects for token launches.")
        state["beat"] = str(utc.date())
    STATE_F.write_text(json.dumps(state))

if __name__ == "__main__":
    main()
