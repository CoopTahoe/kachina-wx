#!/usr/bin/env python3
"""
KACHINA WINTER DESK | shared weather-page snapshot, built in the cloud
Cooper Marketing & Media | Coop | RS#69

Builds ONE snapshot.json in the exact schema of the page tag
<script type="application/json" id="cmmw-snap"> on coopermarketing.org/tahoeweather
(the KSEF page's #ksefw-snap is a copy of the same thing). GitHub Pages serves
it at https://cooptahoe.github.io/kachina-wx/snapshot.json and the live
loader on both pages reads it. No Mac, no Chrome, no Squarespace admin.

Parts (each independent; a part that fails keeps its previous value):
  wb.nam, wb.aifs     WeatherBell frame lists (Coop's licensed login, GitHub secrets)
  wb.goesW, wb.goesE  WeatherBell GOES-West IR loops
  kirkwood            Kirkwood LoggerNet: Cornice Express (Chair 6 top), Whiskey, Hayflat
  cssl                UC Berkeley Central Sierra Snow Lab
  enso                NOAA CPC weekly table + monthly ENSO discussion

Outputs (in --out, default the repo root):
  snapshot.json  the page snapshot
  health.json    what worked, what failed, run times, map lag, age of each part
Never invents a number. Exit code 0 = published clean, 1 = published with a failed part
(that part kept its old value), 2 = validation failed (nothing published).

USAGE
  python3 winterdesk.py                         # full run (needs WB_USERNAME / WB_PASSWORD for maps)
  python3 winterdesk.py --skip-wb               # everything except WeatherBell
  python3 winterdesk.py --seed page             # first run: start from the live page snapshot
Standard library only, Python 3.9+.
"""

import argparse
import html
import json
import math
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import weatherbell as wbm

PT = ZoneInfo("America/Los_Angeles")
UA = "KachinaWx/2.0 (Cooper Marketing & Media; coop@coopermarketing.org)"
TIMEOUT = 25
KIRKWOOD = "https://weather.kirkwood.com/"
CMM_PAGE = "https://www.coopermarketing.org/tahoeweather"
CPC_WEEKLY = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
CPC_DISC = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/ensodisc.shtml"
CSSL = "https://cssl.berkeley.edu/"
MONTHS = "January February March April May June July August September October November December".split()


# ─────────────────────────────────────────────────────────────── helpers ──

def ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL = ssl_ctx()


def get(url, as_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html, */*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else v


def iso_z(dt=None):
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def strip_html(page):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t)


# ──────────────────────────────────────────────────── Kirkwood stations ──
# Same queries and rules as the Chrome desk job's STEP 1 (Cornice Express is the Chair 6 top station).

def kw_query(table, n=1, fetch=get):
    url = (KIRKWOOD + "?command=DataQuery&format=json&uri=" + urllib.parse.quote("Server:" + table)
           + f"&mode=most-recent&p1={n}")
    j = fetch(url)
    names = [f["name"] for f in j["head"]["fields"]]
    rows = [dict(zip(names, d["vals"]), time=d["time"]) for d in j["data"]]
    rows.sort(key=lambda r: r["time"])
    return rows


def hn24(rows):
    """24 h new snow from the hourly depth sensor: median of the first 3 vs last 3
    readings; None when the sensor is too noisy to call (never a guess)."""
    try:
        v = [num(r["DBTCDT_Avg"] if "DBTCDT_Avg" in r else r.get("DBTCDT")) for r in rows]
        if len(v) < 12:
            return None
        A, B = v[:3], v[-3:]
        if any(x is None or x < -5 or x > 150 for x in A + B):
            return None
        if max(A) - min(A) > 3 or max(B) - min(B) > 3:
            return None
        md = lambda a: sorted(a)[1]
        now = md(B)
        if now < 1:
            return 0
        return round(max(0, now - md(A)), 1)
    except Exception:
        return None


def kt(s):
    d = datetime.fromisoformat(s)
    return f"{d:%H%M} {d:%b} {d.day}"


def build_kirkwood(fetch=get):
    c = kw_query("Chair6_Top.Min15", 1, fetch)[-1]
    w = kw_query("Whiskey.Min_5", 1, fetch)[-1]
    h = kw_query("HayFlat.FiveMin", 1, fetch)[-1]
    wh = kw_query("Whiskey.Hourly", 25, fetch)
    hh = kw_query("HayFlat.Hourly", 25, fetch)
    kw = {"pulled": iso_z(),
          "cornice": {"time": kt(c["time"]), "dir": num(c.get("WindDir_Avg")), "speed": num(c.get("Windspeed_Avg")),
                      "gust": num(c.get("Windspeed_Max")), "airT": num(c.get("AirTF")), "wetB": num(c.get("TwF"))},
          "whiskey": {"time": kt(w["time"]), "airT": num(w.get("AirTF_Avg")), "wetB": num(w.get("TwF_Avg")),
                      "hn24": hn24(wh)},
          "hayflat": {"time": kt(h["time"]), "airT": num(h.get("AirTF")), "wetB": num(h.get("TwF")),
                      "hn24": hn24(hh)}}
    newest = max(c["time"], w["time"], h["time"])
    age_h = (datetime.now(PT) - datetime.fromisoformat(newest).replace(tzinfo=PT)).total_seconds() / 3600
    notes = []
    if age_h > 3:
        notes.append(f"Kirkwood readings are {age_h:.1f} hours old")
    for st in ("cornice", "whiskey", "hayflat"):
        if any(kw[st][k] is None for k in ("airT", "wetB")):
            notes.append(f"Kirkwood {st} sent NAN for a temperature")
    return kw, notes


# ─────────────────────────────────────────────────────────── Snow Lab ──

def build_cssl(fetch=get):
    t = strip_html(fetch(CSSL, as_json=False))

    def grab(label):
        m = re.search(label + r"[^0-9\-]{0,40}(-?\d+(?:\.\d+)?)", t, re.I)
        return num(m.group(1)) if m else None

    c = {"pulled": iso_z(), "snow24": grab(r"24[- ]Hour Snowfall"), "snow48": grab(r"48[- ]Hour Snowfall"),
         "snow7d": grab(r"7[- ]Day Snowfall"), "tempF": grab(r"Current Temperature")}
    missing = [k for k, v in c.items() if v is None]
    if missing:
        raise ValueError("Snow Lab page did not show: " + ", ".join(missing))
    return c


# ───────────────────────────────────────────────────────────── CPC ENSO ──

def build_enso_weekly(fetch=get):
    rows = []
    for line in fetch(CPC_WEEKLY, as_json=False).splitlines():
        m = re.match(r"\s*(\d{2}[A-Z]{3}\d{4})\s+(.*)", line)
        if not m:
            continue
        vals = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", m.group(2))]
        if len(vals) == 8:
            rows.append((m.group(1), vals))
    if len(rows) < 2:
        raise ValueError("CPC weekly table not parsed")
    (wk, v), (_, pv) = rows[-1], rows[-2]
    week = datetime.strptime(wk.title(), "%d%b%Y").strftime("%Y-%m-%d")
    return {"week": week, "nino12": v[1], "nino3": v[3], "nino34": v[5], "nino4": v[7], "prevNino34": pv[5]}


def _date_words(s):
    m = re.search(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", s)
    if not m:
        return None
    return datetime(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1))).strftime("%Y-%m-%d")


def build_enso_disc(fetch=get):
    """Monthly ENSO discussion. Only fields the page already shows; anything not
    found raises, so the old values stay (never a guess)."""
    t = strip_html(fetch(CPC_DISC, as_json=False))
    head = re.search(r"DIAGNOSTIC DISCUSSION.{0,120}?(\d{1,2}\s+(?:" + "|".join(MONTHS) + r")\s+\d{4})", t)
    issued = _date_words(head.group(1)) if head else None
    status = re.search(r"ENSO Alert System Status:\s*([A-Za-zñÑ ]+?(?:Advisory|Watch|Not Active|Warning))", t)
    idx = re.search(r"\+?(-?\d+\.\d)\s*°?\s*C in Ni(?:ñ|n)o-?3\.4,\s*\+?(-?\d+\.\d)\s*°?\s*C in Ni(?:ñ|n)o-?3,"
                    r"\s*and\s*\+?(-?\d+\.\d)\s*°?\s*C in Ni(?:ñ|n)o-?1\+2", t)
    odds = re.search(r"chance is (\d{1,3})% for September-November, (\d{1,3})% for October-December,"
                     r" and (\d{1,3})% for November-January", t)
    nxt = re.search(r"next ENSO Diagnostics? Discussion is scheduled for (\d{1,2}\s+\w+\s+\d{4})", t, re.I)
    d = {"issued": issued, "status": status.group(1).strip() if status else None,
         "nino34m": float(idx.group(1)) if idx else None, "nino3m": float(idx.group(2)) if idx else None,
         "nino12m": float(idx.group(3)) if idx else None,
         "oddsSON": int(odds.group(1)) if odds else None, "oddsOND": int(odds.group(2)) if odds else None,
         "oddsNDJ": int(odds.group(3)) if odds else None,
         "next": _date_words(nxt.group(1)) if nxt else None}
    missing = [k for k, v in d.items() if v is None]
    if missing:
        # Seasons roll forward each month (SON/OND/NDJ -> OND/NDJ/DJF); the page labels need a relabel then.
        raise ValueError("ENSO discussion fields not found: " + ", ".join(missing)
                         + " (if the seasons changed, the page labels need Coop's relabel)")
    for k in ("nino34m", "nino3m", "nino12m"):
        if d[k] == int(d[k]):
            d[k] = int(d[k])  # keep 3 not 3.0, like the page snapshot
    return d


def enso_read_fact(weekly, disc):
    """Plain factual line, used only when new CPC numbers make the old read wrong.
    Coop's voice line (Thor draft, Coop approves) can replace it any time."""
    d = round(weekly["nino34"] - weekly["prevNino34"], 1)
    move = "unchanged" if d == 0 else (f"up {d:.1f}" if d > 0 else f"down {abs(d):.1f}")
    wk = datetime.strptime(weekly["week"], "%Y-%m-%d")
    nxt = datetime.strptime(disc["next"], "%Y-%m-%d") if disc and disc.get("next") else None
    s = (f"Niño-3.4 ran {weekly['nino34']:+.1f}°C the week of {wk:%B} {wk.day}, {move} from the week before."
         + (f" CPC status: {disc['status']}." if disc and disc.get("status") else "")
         + (f" Next CPC discussion {nxt:%B} {nxt.day}." if nxt else ""))
    return s


# ────────────────────────────────────────────────────────────── snapshot ──

SNAP_RE = re.compile(r'<script type="application/json" id="cmmw-snap">(.*?)</script>', re.S)


def seed_from_page(fetch=get):
    m = SNAP_RE.search(fetch(CMM_PAGE + "?v=seed", as_json=False))
    if not m:
        raise ValueError("cmmw-snap tag not found on the CMM page")
    return json.loads(m.group(1))


WB_KEYS = {"nam": list(wbm.NAM["maps"]), "aifs": list(wbm.AIFS["maps"])}


def validate(s):
    """Schema gate before anything is published. Returns a list of problems."""
    p = []
    if not parse_iso(s.get("updated")):
        p.append("updated is not a time")
    wb = s.get("wb") or {}
    for g, keys in WB_KEYS.items():
        for k in keys:
            m = (wb.get(g) or {}).get(k)
            if not m or not m.get("frames") or not m.get("init"):
                p.append(f"wb.{g}.{k} missing frames or init")
            elif not all(wbm.FRAME_RE.match(f) for f in m["frames"]):
                p.append(f"wb.{g}.{k} has a bad frame id")
    for g in ("goesW", "goesE"):
        if not (wb.get(g) or {}).get("frames"):
            p.append(f"wb.{g} missing frames")
    kw = s.get("kirkwood") or {}
    for st in ("cornice", "whiskey", "hayflat"):
        if not isinstance(kw.get(st), dict) or not kw[st].get("time"):
            p.append(f"kirkwood.{st} missing")
    for k in ("cssl", "enso"):
        if not isinstance(s.get(k), dict):
            p.append(f"{k} missing")
    if len(json.dumps(s)) > 400_000:
        p.append("snapshot over 400 KB")
    return p


def age_hours(iso, now):
    t = parse_iso(iso)
    return None if not t else round((now - t).total_seconds() / 3600, 1)


def run(args, fetch=get, wb_factory=None):
    now = datetime.now(timezone.utc)
    out = args.out
    snap_path, health_path = os.path.join(out, "snapshot.json"), os.path.join(out, "health.json")
    health = {"started": iso_z(now), "ok": [], "failed": {}, "notes": [], "mapLag": [], "runs": {}}

    def step(name, fn):
        try:
            r = fn()
            health["ok"].append(name)
            return r
        except Exception as e:
            health["failed"][name] = f"{type(e).__name__}: {e}"[:300]
            return None

    # 1) previous snapshot: the published one, else (first run) the live page
    prev = None
    if os.path.exists(snap_path) and args.seed != "page":
        try:
            with open(snap_path, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception as e:
            health["notes"].append(f"snapshot.json unreadable, reseeding from page: {e}")
    if prev is None:
        prev = step("seed_from_page", lambda: seed_from_page(fetch))
    if prev is None:
        fx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "page_snapshot_2026-10-18.json")
        if os.path.exists(fx):
            with open(fx, encoding="utf-8") as f:
                prev = json.load(f)
            health["notes"].append("Seeded from the 18 Oct 2026 page copy; every part that refreshes this run replaces it")
    if prev is None:
        health["finished"] = iso_z()
        _save(health_path, health)
        print("FAILED: no previous snapshot and the page seed failed; nothing published")
        return 2
    snap = json.loads(json.dumps(prev))  # deep copy
    snap.setdefault("wb", {}).setdefault("pulled", {})

    # 2) WeatherBell
    if args.skip_wb:
        health["notes"].append("WeatherBell skipped by --skip-wb; map frames keep their previous values")
    else:
        wb = step("weatherbell_login", lambda: (wb_factory or _wb_from_env)())
        if wb:
            try:
                for g, spec in (("nam", wbm.NAM), ("aifs", wbm.AIFS)):
                    r = step(f"wb_{g}", lambda spec=spec: wbm.collect_group(wb, spec, health["notes"].append))
                    if r:
                        maps, errs, lag, chosen = r
                        snap["wb"].setdefault(g, {}).update(maps)
                        snap["wb"]["pulled"][g] = iso_z()
                        health["runs"][g] = _z(chosen)
                        health["mapLag"] += [{**x, "run": _z(x["run"]), "groupRun": _z(x["groupRun"])} for x in lag]
                        for k, e in errs.items():
                            health["failed"][f"wb_{g}_{k}"] = e
                for g in ("goesW", "goesE"):
                    m = step(f"wb_{g}", lambda g=g: wbm.collect_goes(wb, g))
                    if m:
                        snap["wb"][g] = m
                        snap["wb"]["pulled"][g] = iso_z()
            finally:
                wb.logout()
                health["notes"].append(f"WeatherBell API calls this run: {wb.calls}")

    # 3) Kirkwood, Snow Lab, ENSO
    kw = step("kirkwood", lambda: build_kirkwood(fetch))
    if kw:
        snap["kirkwood"], n = kw
        health["notes"] += n
    cs = step("snow_lab", lambda: build_cssl(fetch))
    if cs:
        snap["cssl"] = cs
    enso = dict(snap.get("enso") or {})
    wk = step("cpc_weekly", lambda: build_enso_weekly(fetch))
    dc = step("cpc_discussion", lambda: build_enso_disc(fetch))
    changed = False
    if wk:
        changed |= (enso.get("weekly") or {}).get("week") != wk["week"]
        enso["weekly"] = wk
    if dc and dc["issued"] >= ((enso.get("disc") or {}).get("issued") or ""):
        changed |= (enso.get("disc") or {}).get("issued") != dc["issued"]
        enso["disc"] = dc
    if wk or dc:
        enso["checked"] = iso_z()
    if changed and enso.get("weekly"):
        enso["read"] = {"date": datetime.now(PT).strftime("%Y-%m-%d"),
                        "text": enso_read_fact(enso["weekly"], enso.get("disc"))}
        health["notes"].append("New CPC numbers: El Niño read reset to a plain factual line; Coop's voice line can replace it")
    snap["enso"] = enso

    # 4) stamp, validate, publish. Never put a fresh stamp on data that did not refresh.
    fresh_parts = {"wb_nam", "wb_aifs", "wb_goesW", "wb_goesE", "kirkwood", "snow_lab", "cpc_weekly"}
    if not fresh_parts & set(health["ok"]):
        health["failed"]["publish"] = "No part refreshed this run; snapshot left as it was so the page stamp stays honest"
        health["published"], health["finished"] = False, iso_z()
        _save(health_path, health)
        print("FAILED: nothing refreshed, snapshot NOT published")
        return 2
    snap["updated"] = iso_z()
    problems = validate(snap)
    nowz = datetime.now(timezone.utc)
    health["age"] = {"nam": age_hours(snap["wb"]["pulled"].get("nam"), nowz),
                     "aifs": age_hours(snap["wb"]["pulled"].get("aifs"), nowz),
                     "goesW": age_hours(snap["wb"]["pulled"].get("goesW"), nowz),
                     "kirkwood": age_hours((snap.get("kirkwood") or {}).get("pulled"), nowz),
                     "cssl": age_hours((snap.get("cssl") or {}).get("pulled"), nowz)}
    health["finished"] = iso_z()
    if problems:
        health["failed"]["validate"] = "; ".join(problems)[:500]
        health["published"] = False
        _save(health_path, health)
        print("FAILED validation, snapshot NOT published: " + "; ".join(problems))
        return 2
    _save(snap_path, snap)
    health["published"] = True
    health["snapshotUpdated"] = snap["updated"]
    _save(health_path, health)
    pt = datetime.now(PT)
    bad = ", ".join(health["failed"]) or "none"
    print(f"Winter Desk snapshot {pt:%H%M} ({pt:%-I:%M %p}) published. ok: {len(health['ok'])}, failed: {bad}")
    for n in health["notes"]:
        print("  note:", n)
    for x in health["mapLag"]:
        print(f"  MAP LAG {x['group']} {x['map']} {x['run']} vs {x['groupRun']} ({x['hours']} h)")
    return 1 if health["failed"] else 0  # published either way; 1 turns the GitHub run red so Coop gets the email


def _z(init):
    return datetime.fromtimestamp(int(init), timezone.utc).strftime("%d %b %HZ")


def _wb_from_env():
    wb = wbm.WeatherBell(os.environ.get("WB_USERNAME"), os.environ.get("WB_PASSWORD"), ssl_context=SSL)
    wb.login()
    return wb


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Kachina Winter Desk snapshot")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--skip-wb", action="store_true", help="do not call WeatherBell")
    ap.add_argument("--seed", choices=["auto", "page"], default="auto",
                    help="page = start from the live CMM page snapshot even if snapshot.json exists")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
