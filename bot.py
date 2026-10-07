#!/usr/bin/env python3
"""DYOR watchlist bot - legitimacy gate for Solana + Robinhood. Free APIs only."""
import os, json, time, re, datetime, urllib.request, urllib.parse, pathlib

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
SEEN_F = ROOT / "seen.json"
MODE = os.environ.get("MODE", "loop")
MIN = CFG.get("min_score", 60)

def get(url, t=20):
    req = urllib.request.Request(url, headers={"User-Agent": "dyor-bot/1.0"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read().decode())

def tg(text):
    data = urllib.parse.urlencode({"chat_id": CFG["telegram_chat_id"],
        "text": text, "parse_mode": "Markdown",
        "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(urllib.request.Request(
        f"https://api.telegram.org/bot{CFG['telegram_token']}/sendMessage",
        data=data), timeout=15)

def domain_age(url):
    try:
        m = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if not m: return None
        d = get(f"https://rdap.org/domain/{m.group(1).lower()}", 15)
        for ev in d.get("events", []):
            if ev.get("eventAction") in ("registration", "registered"):
                reg = datetime.datetime.fromisoformat(ev["eventDate"].replace("Z","+00:00"))
                return (datetime.datetime.now(datetime.timezone.utc) - reg).days
    except Exception: return None
    return None

def ca_on_site(ca, url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return ca.lower() in r.read(400000).decode("utf-8","ignore").lower()
    except Exception: return None

def jup_validated():
    try:
        raw = urllib.request.urlopen(
            "https://raw.githubusercontent.com/jup-ag/token-list/main/validated-tokens.csv",
            timeout=20).read().decode()
        return {l.split(",")[2].strip() for l in raw.splitlines()[1:] if len(l.split(","))>2}
    except Exception: return set()

def rugcheck(mint):
    try: return get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary", 20)
    except Exception: return {}

def profiles():
    try: return get("https://api.dexscreener.com/token-profiles/latest/v1", 25)
    except Exception: return []

def gate(t, validated):
    ca, chain = t["tokenAddress"], t["chainId"]
    s, rs = 0, []
    def add(p, m):
        nonlocal s; s += p; rs.append(f"{'✅' if p>=0 else '❌'} {m} ({p:+d})")
    sites = [w["url"] for w in t.get("websites",[]) if w.get("url")]
    socials = [x["url"] for x in t.get("socials",[]) if x.get("url")]
    if not sites: return False, -999, ["no website - instant reject"]
    age = domain_age(sites[0])
    if age is not None:
        add(20 if age>180 else 5 if age>60 else -25, f"domain age {age}d")
    on = ca_on_site(ca, sites[0])
    if on: add(25, "CA verified on project website")
    elif on is False: add(-100, "CA NOT on website - possible fake")
    if ca in validated: add(30, "Jupiter validated list")
    if not socials: add(-15, "no socials linked")
    if chain == "solana":
        rc = rugcheck(ca)
        if rc.get("mintAuthority"): add(-100, "MINT ENABLED - reject")
        elif rc: add(20, "mint revoked")
        if rc.get("freezeAuthority"): add(-40, "freeze enabled")
        elif rc: add(10, "freeze revoked")
        r = rc.get("score", 0)
        add(10 if r>3000 else -50 if r<1000 else 0, f"rugcheck {r}")
    if chain == "robinhood":
        add(0, "Robinhood: limited scanners, held to higher bar")
        if not (on or ca in validated): add(-100, "cannot verify on Robinhood chain")
        if age is not None and age<90: add(-40, "domain <90d on new chain")
    return s >= MIN, s, rs

def alert(t, s, rs):
    ca = t["tokenAddress"]
    return "\n".join([
        "🛡️ *DYOR WATCHLIST - passed legitimacy gate*\n",
        f"*Chain:* {t['chainId']}",
        f"*Name:* {t.get('name','?')} (${t.get('symbol','?')})",
        f"*CA:* `{ca}`",
        f"*Site:* {(t.get('websites') or [{}])[0].get('url','?')}",
        f"*Pairs:* {t.get('url') or f'https://dexscreener.com/{t['chainId']}/{ca}'}",
        f"*Score:* {s}/{MIN} required\n*Checks:*",
        *[f"  {r}" for r in rs[:12]],
        "\n⚠️ _Verifiable ≠ guaranteed. DYOR before buying._"])

def main():
    validated = jup_validated()
    seen = set(json.loads(SEEN_F.read_text())) if SEEN_F.exists() else set()
    for t in profiles():
        if t.get("chainId") not in CFG["chains"]: continue
        k = f"{t['chainId']}:{t['tokenAddress']}"
        if k in seen: continue
        seen.add(k)
        try:
            ok, s, rs = gate(t, validated)
            print(("[PASS] " if ok else "[skip] ") + k, s)
            if ok: tg(alert(t, s, rs))
        except Exception as e: print("[err]", k, e)
    SEEN_F.write_text(json.dumps(sorted(seen)))
    if MODE != "cron": time.sleep(CFG.get("poll_seconds", 120)); main()

if __name__ == "__main__": main()
