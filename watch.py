#!/usr/bin/env python3
"""Watch internship repos for new postings; push matches to ntfy.

Sources are either structured JSON (cheap, ETag-conditional) or markdown
tables (parsed header-aware so a column reshuffle doesn't corrupt fields).
Dedupe is cross-source on normalized company+title, because the aggregators
heavily overlap and each assigns its own ids.
"""
import json, os, re, sys, time, html, urllib.request, urllib.error, pathlib

UA = {"User-Agent": "internship-watch"}
STATE = pathlib.Path(__file__).parent / "state.json"
SEED = os.environ.get("SEED") == "1"
NTFY = os.environ.get("NTFY_TOPIC", "")
TERMS      = set(json.loads(os.environ.get("TERMS",      '["Summer 2027"]')))
CATEGORIES = set(json.loads(os.environ.get("CATEGORIES", '["Software","AI/ML/Data"]')))
LOCATIONS  =     json.loads(os.environ.get("LOCATIONS",  '[]'))
MAX_AGE = 45 * 86400
MIN_ROWS_FRAC = 0.5          # markdown source must keep >=50% of last row count

JSON_SOURCES = {
    "simplify": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "vansh":    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
}
MD_SOURCES = {
    "zapply-ml": "https://raw.githubusercontent.com/zapplyjobs/awesome-ML-internships/main/README.md",
    "speedy":    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
}

# ---------- helpers ----------
def norm(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or "")).lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(inc|llc|corp|corporation|co|the|intern|internship|summer|2026|2027)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def key(company, title):
    return f"{norm(company)}|{norm(title)[:60]}"

def strip_md(cell):
    m = re.search(r"<strong>(.*?)</strong>", cell) or re.search(r"\*\*(.*?)\*\*", cell)
    txt = m.group(1) if m else cell
    txt = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", txt)
    return html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()

def first_url(cell):
    m = re.search(r'href="([^"]+)"', cell) or re.search(r"\]\((https?://[^)]+)\)", cell)
    return m.group(1) if m else ""

def fetch(url, etag):
    req = urllib.request.Request(url, headers=dict(UA))
    if etag: req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.read().decode("utf-8", "replace"), r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304: return None, etag
        print(f"    HTTP {e.code}", file=sys.stderr); return None, etag
    except Exception as e:
        print(f"    error: {e}", file=sys.stderr); return None, etag

# ---------- parsers ----------
def parse_md(text):
    """Header-aware markdown table parse. Returns list of dicts."""
    out, cols = [], None
    for line in text.split("\n"):
        if not line.lstrip().startswith("|"): 
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        low = [strip_md(c).lower() for c in cells]
        if any(c.startswith("company") for c in low):        # header row
            cols = {}
            for i, c in enumerate(low):
                if c.startswith("company"): cols["company"] = i
                elif c.startswith(("role", "position")): cols["title"] = i
                elif c.startswith("location"): cols["loc"] = i
                elif c.startswith(("apply", "posting", "link")): cols["url"] = i
                elif c.startswith("visa"): cols["visa"] = i
                elif c.startswith("salary"): cols["salary"] = i
            continue
        if not cols or set(cells) <= {"", *("-" * n for n in range(1, 12))}:
            continue
        if all(re.fullmatch(r"-{2,}|:?-+:?", c) for c in cells if c):
            continue
        def g(k):
            i = cols.get(k)
            return cells[i] if i is not None and i < len(cells) else ""
        company, title = strip_md(g("company")), strip_md(g("title"))
        if not company or not title:
            continue
        out.append({
            "company_name": company, "title": title,
            "locations": [re.sub(r"\s*\+\d+$", "", strip_md(g("loc"))).strip()] if strip_md(g("loc")) else [],
            "url": first_url(g("url")), "visa": strip_md(g("visa")),
            "salary": strip_md(g("salary")), "active": True, "is_visible": True,
        })
    return out

def matches(j, src):
    if not (j.get("active") and j.get("is_visible")): return False
    if TERMS:
        if "terms" in j:
            if not (TERMS & set(j.get("terms") or [])): return False
        elif "season" in j:
            if not any(t.split()[0] in str(j.get("season") or "") for t in TERMS): return False
        # markdown sources: repo is already season-scoped, no term field to check
    if CATEGORIES:
        if j.get("category") is not None:
            if j["category"] not in CATEGORIES: return False
        elif src != "zapply-ml":   # that repo is 100% ML by construction
            kw = ("software","engineer","developer","swe","backend","frontend","full stack",
                  "machine learning","data","research","ai ","ml ","computer vision")
            if not any(w in j["title"].lower() for w in kw): return False
    if LOCATIONS:
        locs = j.get("locations") or []
        blob = " ".join(locs).lower()
        if not any(w.lower() in blob for w in LOCATIONS): return False
    return True

def notify(jobs):
    if not NTFY:
        print("  (no NTFY_TOPIC — printing only)"); return
    for j in jobs[:20]:
        extra = " ".join(x for x in [j.get("salary",""), j.get("visa","")] if x)
        body = f"{j['company_name']} — {j['title']}\n{', '.join(j.get('locations') or []) or '—'}"
        if extra.strip(): body += f"\n{extra.strip()}"
        req = urllib.request.Request(f"https://ntfy.sh/{NTFY}", data=body.encode(),
            headers={"Title": f"[{j['_src']}] {j['company_name'][:34]}", "Tags": "briefcase",
                     **({"Actions": f"view, Open posting, {j['url']}"} if j.get("url","").startswith("http") else {})})
        try: urllib.request.urlopen(req, timeout=20).read()
        except Exception as e: print(f"    ntfy failed: {e}", file=sys.stderr)
    if len(jobs) > 20:
        try: urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{NTFY}",
             data=f"+{len(jobs)-20} more new postings".encode()), timeout=20).read()
        except Exception: pass

def main():
    try: st = json.loads(STATE.read_text())
    except Exception: st = {"etags": {}, "seen": {}, "rowcount": {}}
    st.setdefault("rowcount", {})
    now, fresh, dirty = time.time(), [], False

    for name, url in {**JSON_SOURCES, **MD_SOURCES}.items():
        raw, etag = fetch(url, st["etags"].get(name))
        if raw is None:
            print(f"{name}: unchanged"); continue
        is_md = name in MD_SOURCES
        try:
            recs = parse_md(raw) if is_md else json.loads(raw)
        except Exception as e:
            print(f"{name}: PARSE FAILED ({e}) — state untouched", file=sys.stderr); continue
        # format-change guard: a restyled README must fail loudly, not go quiet
        prev = st["rowcount"].get(name, 0)
        if is_md and prev and len(recs) < prev * MIN_ROWS_FRAC:
            msg = f"{name}: FORMAT WARNING — {len(recs)} rows, was {prev}. Not updating state."
            print(msg, file=sys.stderr)
            if NTFY:
                try: urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{NTFY}",
                     data=msg.encode(), headers={"Title":"internship-watch broken","Priority":"high"}), timeout=20).read()
                except Exception: pass
            continue
        st["etags"][name], st["rowcount"][name] = etag, len(recs); dirty = True
        new = []
        for j in recs:
            if not matches(j, name): continue
            k = key(j["company_name"], j["title"])
            if k in st["seen"]: continue
            st["seen"][k] = int(now); j["_src"] = name; new.append(j)
        print(f"{name}: {len(recs)} rows, {len(new)} new & matching")
        if SEED: print(f"  (seeded {len(new)}, not notifying)")
        else: fresh += new

    st["seen"] = {k: v for k, v in st["seen"].items() if now - v < MAX_AGE}
    if fresh:
        for j in fresh[:40]:
            print(f"  NEW [{j['_src']:<9}] {j['company_name'][:24]:<25} {j['title'][:44]}")
        notify(fresh)
    if dirty: STATE.write_text(json.dumps(st))
    print(f"total new: {len(fresh)} | tracking {len(st['seen'])} keys")

if __name__ == "__main__":
    main()
