#!/usr/bin/env python3
"""
KACHINA WX  |  Morning weather-page data run
Cooper Marketing & Media  |  Coop | RS#69

Builds the auto-updated weather data for:
  * CMM Winter Desk   (coopermarketing.org/tahoeweather, JSON tag #cmmw-snap)
  * KSEF Snow Desk    (kirkwoodskiteam.com/snow-desk, #ksd-snapshot,
                       and the Team Room card, #ksef-sdw-snap)

Sources (all public, no logins):
  Kirkwood LoggerNet stations, Open-Meteo, NWS (alerts + Reno AFD),
  UC Berkeley Central Sierra Snow Lab, NOAA CPC weekly ENSO table.

NOT covered here (needs a signed-in session, stays with the Chrome job):
  * WeatherBell map frame lists (wb.*) on the Winter Desk
  * Writing into Squarespace itself
  * Coop-written posts (posts[]) and the KSEF /weather NWS block (nws)

Standard library only. Python 3.9+ (uses zoneinfo).

USAGE
  python3 kachina_wx.py                       # build both, write to ./out
  python3 kachina_wx.py --out ~/tahoe-weather/kachina/out
  python3 kachina_wx.py --merge-ksef old_ksd.json   # also write a full merged snapshot
  python3 kachina_wx.py --merge-cmm  old_cmm.json
  python3 kachina_wx.py --publish-dir ~/kachina-data --push   # copy JSON into a git repo and push

Every source is independent: if one fails, that block keeps its previous
value from state.json and the failure is listed in run_report.json.
Never invents a number.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
UA = "KachinaWx/1.0 (Cooper Marketing & Media; coop@coopermarketing.org)"
TIMEOUT = 25

KIRKWOOD = "https://weather.kirkwood.com/"
KW_BASE = {"lat": 38.684, "lon": -120.065, "elev_m": 2377}    # 7,800 ft
KW_SUMMIT = {"lat": 38.684, "lon": -120.065, "elev_m": 2987}  # 9,800 ft

# Nearby resorts, names and labels exactly as the Snow Desk shows them.
NEARBY = [
    {"name": "Kirkwood",        "el": "7,800'", "lat": 38.6847, "lon": -120.0654, "home": True},
    {"name": "Heavenly",        "el": "8,530'", "lat": 38.9353, "lon": -119.9400},
    {"name": "Mt. Rose",        "el": "8,860'", "lat": 39.3283, "lon": -119.8853},
    {"name": "Sierra-at-Tahoe", "el": "7,550'", "lat": 38.7996, "lon": -120.0806},
    {"name": "Bear Valley",     "el": "7,710'", "lat": 38.4688, "lon": -120.0396},
    {"name": "Northstar",       "el": "7,870'", "lat": 39.2746, "lon": -120.1210},
    {"name": "Dodge Ridge",     "el": "7,550'", "lat": 38.1882, "lon": -119.9550},
    {"name": "Palisades Tahoe", "el": "7,550'", "lat": 39.1970, "lon": -120.2357},
    {"name": "Sugar Bowl",      "el": "7,550'", "lat": 39.3047, "lon": -120.3335},
]

ALERT_POINTS = [(38.684, -120.065), (38.93, -120.04)]  # Kirkwood, South Lake Tahoe


# ─────────────────────────────────────────────────────────────── helpers ──

def now_pt():
    return datetime.now(PT)


def _ssl_context():
    """python.org builds on macOS ship without a CA bundle. Use certifi if
    installed, else the macOS system bundle. Verification stays ON."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for path in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem"):
        if os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


SSL_CTX = _ssl_context()


def get(url, as_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/html, */*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def num(x, nd=None):
    """Number or None. Kirkwood loggers send 'NAN' strings."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd) if nd is not None else v


def r1(x):
    return num(x, 1)


def m_to_ft(m):
    return None if m is None else int(round(m * 3.28084))


def ft_label_to_m(label):
    return round(int(re.sub(r"[^\d]", "", label)) / 3.28084)


def logger_iso(t):
    """Kirkwood logger time is naive Pacific. Return ISO with offset."""
    dt = datetime.fromisoformat(t).replace(tzinfo=PT)
    return dt.isoformat(timespec="seconds")


def kt(t):
    """'HHMM Mon D' like the Winter Desk shows it."""
    dt = datetime.fromisoformat(t)
    return f"{dt:%H%M} {dt:%b} {dt.day}"


def mil(dt):
    return f"{dt:%H%M}"


# ──────────────────────────────────────────────────── Kirkwood stations ──

def kw_query(table, n=1):
    url = (KIRKWOOD + "?command=DataQuery&format=json&uri="
           + urllib.parse.quote("Server:" + table) + f"&mode=most-recent&p1={n}")
    j = get(url)
    names = [f["name"] for f in j["head"]["fields"]]
    rows = []
    for d in j["data"]:
        rec = {"time": d["time"]}
        rec.update(dict(zip(names, d["vals"])))
        rows.append(rec)
    rows.sort(key=lambda r: r["time"])  # oldest first
    return rows


def pick(rec, *keys):
    for k in keys:
        if k in rec:
            return rec[k]
    return None


def hn24(hourly_rows):
    """24 hour new snow from the hourly depth sensor. Same rules as the
    Winter Desk JS: median of first 3 vs last 3, reject noisy or out of
    range samples. None means the sensor is too noisy to call."""
    try:
        v = [num(pick(r, "DBTCDT_Avg", "DBTCDT")) for r in hourly_rows]
        if len(v) < 12 or any(x is None for x in v[:3] + v[-3:]):
            return None
        A, B = v[:3], v[-3:]
        md = lambda a: sorted(a)[1]
        sp = lambda a: max(a) - min(a)
        if any(x < -5 or x > 150 for x in A + B) or sp(A) > 3 or sp(B) > 3:
            return None
        now = md(B)
        if now < 1:
            return 0
        return round(max(0.0, now - md(A)), 1)
    except Exception:
        return None


def build_kirkwood():
    c6 = kw_query("Chair6_Top.Min15")[-1]
    c6h = kw_query("Chair6_Top.Hourly", 24)
    hf5 = kw_query("HayFlat.FiveMin", 3)
    wk5 = kw_query("Whiskey.Min_5", 3)
    hfh = kw_query("HayFlat.Hourly", 25)
    wkh = kw_query("Whiskey.Hourly", 25)

    hf_now, wk_now = hf5[-1], wk5[-1]
    hf_hn, wk_hn = hn24(hfh), hn24(wkh)

    # Ridge hourly and 24 h peak
    speeds = [r1(r.get("Windspeed_Avg")) for r in c6h]
    gusts = [r1(r.get("Windspeed_Max")) for r in c6h]
    peak = None
    for r in c6h:
        g = num(r.get("Windspeed_Max"))
        if g is not None and (peak is None or g > peak["gust"]):
            peak = {"gust": round(g, 2),
                    "time": logger_iso(r.get("Windspeed_TMx") or r["time"]),
                    "dir": r1(r.get("WindDir_Avg"))}

    ridge = {
        "name": "Ch. 6 Ridge", "el": "9,186'",
        "time": logger_iso(c6["time"]),
        "dir": r1(c6.get("WindDir_Avg")), "speed": num(c6.get("Windspeed_Avg"), 2),
        "gust": num(c6.get("Windspeed_Max"), 2), "airT": num(c6.get("AirTF"), 2),
        "wetB": num(c6.get("TwF"), 2), "rh": r1(c6.get("RH")),
        "hourStart": logger_iso(c6h[0]["time"]) if c6h else None,
        "hourly": {"speed": speeds, "gust": gusts},
        "peak": peak,
    }

    def plot(key, name, el, now_rec, five, h):
        return {
            "key": key, "name": name, "el": el,
            "time": logger_iso(now_rec["time"]),
            "airT": num(pick(now_rec, "AirTF", "AirTF_Avg"), 2),
            "wetB": num(pick(now_rec, "TwF", "TwF_Avg"), 2),
            "rh": num(pick(now_rec, "RH", "RH_Avg"), 2),
            "hsSamples": [num(pick(r, "DBTCDT", "DBTCDT_Avg"), 3) for r in five],
            "hn24": h,
        }

    ksef_live = {
        "source": KIRKWOOD + "index.html",
        "pulled": now_pt().isoformat(timespec="seconds"),
        "ridge": ridge,
        "plots": [plot("hayflat", "Hayflat Snow Plot", "7,850'", hf_now, hf5, hf_hn),
                  plot("whiskey", "Whiskey Snow Plot", "8,475'", wk_now, wk5, wk_hn)],
    }

    cmm_kw = {
        "pulled": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "cornice": {"time": kt(c6["time"]), "dir": num(c6.get("WindDir_Avg")),
                    "speed": num(c6.get("Windspeed_Avg")), "gust": num(c6.get("Windspeed_Max")),
                    "airT": num(c6.get("AirTF")), "wetB": num(c6.get("TwF"))},
        "whiskey": {"time": kt(wk_now["time"]), "airT": num(pick(wk_now, "AirTF_Avg", "AirTF")),
                    "wetB": num(pick(wk_now, "TwF_Avg", "TwF")), "hn24": wk_hn},
        "hayflat": {"time": kt(hf_now["time"]), "airT": num(pick(hf_now, "AirTF", "AirTF_Avg")),
                    "wetB": num(pick(hf_now, "TwF", "TwF_Avg")), "hn24": hf_hn},
    }

    stale_h = (now_pt() - datetime.fromisoformat(c6["time"]).replace(tzinfo=PT)).total_seconds() / 3600
    return ksef_live, cmm_kw, round(stale_h, 1)


# ──────────────────────────────────────────────────────────── Open-Meteo ──

OM = "https://api.open-meteo.com/v1/forecast"
OM_UNITS = "temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&timezone=America%2FLos_Angeles"


def om(lat, lon, elev_m, extra):
    return get(f"{OM}?latitude={lat}&longitude={lon}&elevation={elev_m}&{OM_UNITS}&{extra}")


def build_forecast():
    daily = ("weather_code,temperature_2m_max,temperature_2m_min,snowfall_sum,precipitation_sum,"
             "precipitation_probability_max,wind_gusts_10m_max,wind_direction_10m_dominant")
    b = om(KW_BASE["lat"], KW_BASE["lon"], KW_BASE["elev_m"],
           f"daily={daily}&hourly=freezing_level_height&forecast_days=10"
           "&current=temperature_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m,relative_humidity_2m")
    s = om(KW_SUMMIT["lat"], KW_SUMMIT["lon"], KW_SUMMIT["elev_m"],
           "daily=temperature_2m_max,temperature_2m_min,snowfall_sum,wind_gusts_10m_max&forecast_days=10")
    bd, sd = b["daily"], s["daily"]

    # daily minimum freezing level, feet
    frz_by_day = {}
    for t, v in zip(b["hourly"]["time"], b["hourly"]["freezing_level_height"]):
        if v is None:
            continue
        d = t[:10]
        frz_by_day[d] = min(frz_by_day.get(d, v), v)
    unit = (b.get("hourly_units") or {}).get("freezing_level_height", "m")
    to_ft = (lambda v: None if v is None else int(round(v))) if unit.startswith("ft") else m_to_ft
    frz = [to_ft(frz_by_day.get(d)) for d in bd["time"]]

    rl = lambda a: [r1(x) for x in a]
    kirkwood = {
        "dates": bd["time"],
        "base": {"code": bd["weather_code"], "hi": rl(bd["temperature_2m_max"]),
                 "lo": rl(bd["temperature_2m_min"]), "snow": [num(x, 3) for x in bd["snowfall_sum"]],
                 "precip": [num(x, 3) for x in bd["precipitation_sum"]],
                 "pop": bd["precipitation_probability_max"], "gust": rl(bd["wind_gusts_10m_max"]),
                 "dir": bd["wind_direction_10m_dominant"]},
        "summit": {"hi": rl(sd["temperature_2m_max"]), "lo": rl(sd["temperature_2m_min"]),
                   "snow": [num(x, 3) for x in sd["snowfall_sum"]], "gust": rl(sd["wind_gusts_10m_max"])},
        "frz": frz,
    }

    c = b["current"]
    current = {"time": c["time"][11:13] + c["time"][14:16], "temp": r1(c["temperature_2m"]),
               "wind": r1(c["wind_speed_10m"]), "gust": r1(c["wind_gusts_10m"]),
               "dir": c["wind_direction_10m"], "rh": c["relative_humidity_2m"]}
    return kirkwood, current


def build_nearby():
    out = []
    for r in NEARBY:
        j = om(r["lat"], r["lon"], ft_label_to_m(r["el"]),
               "daily=snowfall_sum,precipitation_probability_max&forecast_days=5")
        sn = [x or 0 for x in j["daily"]["snowfall_sum"]]
        pp = [x or 0 for x in j["daily"]["precipitation_probability_max"]]
        row = {"name": r["name"], "el": r["el"], "snow": round(sum(sn), 1), "pop": max(pp)}
        if r.get("home"):
            row["home"] = True
        out.append(row)
    return out


# ──────────────────────────────────────────────────────────────────── NWS ──

def build_alerts():
    seen, out = set(), []
    for lat, lon in ALERT_POINTS:
        j = get(f"https://api.weather.gov/alerts/active?point={lat},{lon}")
        for f in j.get("features", []):
            p = f["properties"]
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            end = p.get("ends") or p.get("expires")
            when = ""
            if end:
                e = datetime.fromisoformat(end).astimezone(PT)
                when = f" until {mil(e)} {e:%a}"
            out.append({"type": p["event"], "text": f"{p['event']}{when}. {p.get('areaDesc', '').split(';')[0]}".strip()})
    return out


def build_afd_issued():
    j = get("https://api.weather.gov/products/types/AFD/locations/REV")
    t = datetime.fromisoformat(j["@graph"][0]["issuanceTime"]).astimezone(PT)
    return f"{mil(t)} {t.tzname()} {t:%a} {t.day} {t:%b %Y}"


# ─────────────────────────────────────────────────────────── Snow Lab ──

def build_cssl():
    html = get("https://cssl.berkeley.edu/", as_json=False)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)

    def grab(label):
        m = re.search(label + r"[^0-9\-]{0,40}(-?\d+(?:\.\d+)?)", text, re.I)
        return num(m.group(1)) if m else None

    c = {"pulled": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
         "snow24": grab(r"24[- ]Hour Snowfall"), "snow48": grab(r"48[- ]Hour Snowfall"),
         "snow7d": grab(r"7[- ]Day Snowfall"), "tempF": grab(r"Current Temperature")}
    missing = [k for k, v in c.items() if v is None]
    if missing:
        raise ValueError("Snow Lab page did not show: " + ", ".join(missing))
    return c


# ───────────────────────────────────────────────────────────── CPC ENSO ──

def build_enso_weekly():
    txt = get("https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for", as_json=False)
    rows = []
    for line in txt.splitlines():
        m = re.match(r"\s*(\d{2}[A-Z]{3}\d{4})(.*)", line)
        if not m:
            continue
        vals = [float(x) for x in re.findall(r"-?\d+\.\d", m.group(2))]
        if len(vals) == 8:
            rows.append((m.group(1), vals))
    if len(rows) < 2:
        raise ValueError("CPC weekly table not parsed")
    (wk, v), (_, pv) = rows[-1], rows[-2]
    week = datetime.strptime(wk.title(), "%d%b%Y").strftime("%Y-%m-%d")
    return {"week": week, "nino12": v[1], "nino3": v[3], "nino34": v[5], "nino4": v[7], "prevNino34": pv[5]}


def enso_read(weekly, prev_read, today):
    """Plain factual line. Kept short; Coop's voice line can be added by the Chrome job."""
    d = round(weekly["nino34"] - weekly["prevNino34"], 1)
    move = "holding steady" if d == 0 else (f"up {d}" if d > 0 else f"down {abs(d)}")
    text = (f"Nino 3.4 ran {weekly['nino34']:+.1f} C the week of {weekly['week']}, {move} from the week before. "
            "Next CPC weekly update lands Monday. Signal in the Pacific; the Sierra still votes storm by storm.")
    if prev_read and prev_read.get("date") == today:
        return prev_read
    return {"date": today, "text": text}


# ──────────────────────────────────────────────────────────────── run ──

def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="Kachina morning weather data run")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    ap.add_argument("--merge-ksef", help="existing full #ksd-snapshot JSON file to merge into")
    ap.add_argument("--merge-cmm", help="existing full #cmmw-snap JSON file to merge into")
    ap.add_argument("--publish-dir", help="git repo folder to copy the JSON into")
    ap.add_argument("--push", action="store_true", help="git commit and push in --publish-dir")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    state_path = os.path.join(a.out, "state.json")
    state = load(state_path, {"ksef": {}, "cmm": {}})
    ksef, cmm = dict(state.get("ksef", {})), dict(state.get("cmm", {}))
    report = {"started": now_pt().isoformat(timespec="seconds"), "ok": [], "failed": {}, "notes": []}
    today = now_pt().strftime("%Y-%m-%d")

    def step(name, fn):
        try:
            res = fn()
            report["ok"].append(name)
            return res
        except Exception as e:  # keep prior value, log it
            report["failed"][name] = f"{type(e).__name__}: {e}"[:300]
            return None

    kw = step("kirkwood_stations", build_kirkwood)
    if kw:
        live, cmm_kw, stale = kw
        prev = {p["key"]: p.get("hn24") for p in (ksef.get("live") or {}).get("plots", [])}
        for p in live["plots"]:
            p["hn24prev"] = prev.get(p["key"])
        ksef["live"], cmm["kirkwood"] = live, cmm_kw
        if stale > 3:
            report["notes"].append(f"Kirkwood readings are {stale} hours old")

    fc = step("open_meteo_kirkwood", build_forecast)
    if fc:
        ksef["kirkwood"], ksef["current"] = fc

    nb = step("open_meteo_nearby", build_nearby)
    if nb:
        ksef["nearby"] = nb

    al = step("nws_alerts", build_alerts)
    if al is not None:
        ksef["alerts"] = al

    afd = step("nws_afd", build_afd_issued)
    if afd:
        ksef["afdIssued"] = afd

    cs = step("snow_lab", build_cssl)
    if cs:
        cmm["cssl"] = cs

    wk = step("cpc_weekly", build_enso_weekly)
    if wk:
        enso = dict(cmm.get("enso") or {})
        enso["weekly"] = wk
        enso["read"] = enso_read(wk, enso.get("read"), today)
        enso["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        cmm["enso"] = enso

    stamp = now_pt()
    ksef["updated"] = stamp.strftime("%Y-%m-%dT%H:%M")
    cmm["updated"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    # Auto-only files (overlay these onto the page snapshot)
    save(os.path.join(a.out, "ksef_snowdesk_auto.json"), ksef)
    teamroom = {k: v for k, v in ksef.items() if k not in ("nws", "tableNote")}
    save(os.path.join(a.out, "ksef_teamroom_auto.json"), teamroom)
    save(os.path.join(a.out, "cmm_winterdesk_auto.json"), cmm)
    save(state_path, {"ksef": ksef, "cmm": cmm})

    # Optional full merges: keeps posts[], nws, tableNote, wb.* from the old snapshot
    if a.merge_ksef:
        old = load(a.merge_ksef, None)
        if old:
            merged = {**old, **ksef}
            save(os.path.join(a.out, "ksef_snowdesk_full.json"), merged)
    if a.merge_cmm:
        old = load(a.merge_cmm, None)
        if old:
            merged = {**old, **{k: v for k, v in cmm.items() if k != "wb"}}
            if "enso" in cmm and "enso" in old:
                merged["enso"] = {**old["enso"], **cmm["enso"]}  # keep disc{} from the old one
            save(os.path.join(a.out, "cmm_winterdesk_full.json"), merged)

    if a.publish_dir and os.path.exists(os.path.join(a.publish_dir, ".kachina-cloud")):
        report["notes"].append("Publishing is handled by GitHub Actions; local copy kept as a backup only")
    elif a.publish_dir:
        try:
            for f in ("ksef_snowdesk_auto.json", "ksef_teamroom_auto.json", "cmm_winterdesk_auto.json"):
                shutil.copy2(os.path.join(a.out, f), os.path.join(a.publish_dir, f))
            if a.push:
                env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # fail fast, never hang on a login prompt
                g = lambda *c: subprocess.run(["git", "-C", a.publish_dir, *c], check=True,
                                              capture_output=True, text=True, timeout=90, env=env)
                g("add", "-A")
                if subprocess.run(["git", "-C", a.publish_dir, "diff", "--cached", "--quiet"]).returncode:
                    g("commit", "-m", f"Kachina wx {stamp:%Y-%m-%d %H%M}")
                    g("push")
            report["ok"].append("publish")
        except Exception as e:
            detail = (getattr(e, "stderr", "") or "").strip()
            report["failed"]["publish"] = f"{type(e).__name__}: {e} {detail}"[:400]

    report["finished"] = now_pt().isoformat(timespec="seconds")
    save(os.path.join(a.out, "run_report.json"), report)
    bad = ", ".join(report["failed"]) or "none"
    print(f"Kachina wx {stamp:%H%M} ({stamp:%-I:%M %p}): {len(report['ok'])} ok, failed: {bad}")
    for n in report["notes"]:
        print("  note:", n)
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
