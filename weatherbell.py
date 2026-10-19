"""
WeatherBell maps client for the Winter Desk pipeline.
Cooper Marketing & Media | Coop | RS#69

Coop holds a WeatherBell license for public use of these maps. This client
signs in with his account (GitHub secrets WB_USERNAME / WB_PASSWORD), asks the
same API the maps.weatherbell.com app uses for frame lists, and signs out at
the end so the run does not hold one of his sessions open.

What the app does (read from maps.weatherbell.com main bundle, 18 Oct 2026):
  POST https://maps.api.weatherbell.com/image/   Content-Type: text/plain
       body JSON {action:"init",     type, product, domain, param}        -> [init, ...]
       body JSON {action:"forecast", type, product, domain, param, init}  -> ["<validEpoch>-<hash>", ...]
       body JSON {action:"analysis", type:"analysis", product, domain, param} -> ["<epoch>-<hash>", ...]
  GET  https://maps.api.weatherbell.com/auth/     session check
  Session cookie comes from POST https://www.weatherbell.com/login (username, password, remember_me).
  HTTP 417 from the API means too many sessions on the account.

Standard library only.
"""

import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

WWW = "https://www.weatherbell.com"
API = "https://maps.api.weatherbell.com"
UA = "KachinaWx/2.0 (Cooper Marketing & Media; licensed WeatherBell user)"
FRAME_RE = re.compile(r"^\d{9,11}-[A-Za-z0-9_-]+$")


class WeatherBellError(Exception):
    pass


class WeatherBell:
    def __init__(self, username, password, timeout=30, pause=0.35, ssl_context=None):
        if not username or not password:
            raise WeatherBellError("WB_USERNAME or WB_PASSWORD secret is not set")
        self.username, self.password = username, password
        self.timeout, self.pause = timeout, pause
        self.jar = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)]
        if ssl_context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
        self.op = urllib.request.build_opener(*handlers)
        self.calls = 0

    # ── low level ──────────────────────────────────────────────────────
    def _req(self, method, url, data=None, headers=None):
        h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
             "Origin": "https://maps.weatherbell.com", "Referer": "https://maps.weatherbell.com/"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        self.calls += 1
        try:
            with self.op.open(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:500]

    def _api(self, body):
        time.sleep(self.pause)  # be polite; about 40 calls a run
        st, txt = self._req("POST", API + "/image/", json.dumps(body).encode(), {"Content-Type": "text/plain"})
        if st == 417:
            raise WeatherBellError("HTTP 417: WeatherBell says too many sessions are open on the account")
        if st in (401, 403):
            raise WeatherBellError(f"HTTP {st}: not signed in or no access to {body.get('product')}")
        if st != 200:
            raise WeatherBellError(f"HTTP {st} for {body.get('action')} {body.get('product')}/{body.get('param')}: {txt[:120]}")
        try:
            return json.loads(txt)
        except ValueError:
            raise WeatherBellError(f"Non-JSON reply for {body.get('action')} {body.get('param')}: {txt[:120]}")

    # ── session ───────────────────────────────────────────────────────
    def login(self):
        form = urllib.parse.urlencode({"username": self.username, "password": self.password,
                                       "remember_me": "1"}).encode()
        st, txt = self._req("POST", WWW + "/login", form,
                            {"Content-Type": "application/x-www-form-urlencoded",
                             "Origin": WWW, "Referer": WWW + "/login"})
        if st >= 400:
            raise WeatherBellError(f"Login page answered HTTP {st}")
        if not any(".weatherbell.com" in (c.domain or "") or "weatherbell" in (c.domain or "") for c in self.jar):
            raise WeatherBellError("Login set no WeatherBell cookie (wrong password, captcha or a changed login form)")
        if re.search(r'name="password"', txt) and re.search(r"(incorrect|invalid|try again)", txt, re.I):
            raise WeatherBellError("WeatherBell rejected the username or password")
        return self.check()

    def check(self):
        st, txt = self._req("GET", API + "/auth/")
        if st == 417:
            raise WeatherBellError("HTTP 417: too many sessions on the WeatherBell account")
        if st != 200:
            raise WeatherBellError(f"Session check /auth/ answered HTTP {st}")
        return True

    def logout(self):
        try:
            self._req("GET", WWW + "/logout")
        except Exception:
            pass

    # ── data ──────────────────────────────────────────────────────────
    def inits(self, product, domain, param, type_="model"):
        r = self._api({"action": "init", "type": type_, "product": product, "domain": domain, "param": param})
        if not isinstance(r, list) or not r:
            raise WeatherBellError(f"No model runs listed for {product}/{param}")
        out = sorted({str(x) for x in r if re.fullmatch(r"\d{9,11}", str(x))}, key=int, reverse=True)
        if not out:
            raise WeatherBellError(f"Model run list for {param} had no run times")
        return out  # newest first

    def forecast(self, product, domain, param, init, type_="model"):
        r = self._api({"action": "forecast", "type": type_, "product": product, "domain": domain,
                       "param": param, "init": str(init)})
        return _frames(r, param)

    def analysis(self, product, domain, param):
        r = self._api({"action": "analysis", "type": "analysis", "product": product, "domain": domain, "param": param})
        return _frames(r, param)


def _frames(r, param):
    if isinstance(r, dict) and r.get("error"):
        raise WeatherBellError(f"{param}: {r['error']}")
    if not isinstance(r, list):
        raise WeatherBellError(f"{param}: frame list was not a list")
    fr = [str(x) for x in r if FRAME_RE.match(str(x))]
    if not fr:
        raise WeatherBellError(f"{param}: empty frame list")
    fr.sort(key=lambda s: int(s.split("-", 1)[0]))
    return fr


# Map catalogue: snapshot key -> WeatherBell param. Must match the page's wb schema.
NAM = {"product": "nam-nest", "domain": "california", "min_frames": 50, "ref": "gust10",
       "maps": {"mslp": "t850_mslp_prcp6hr", "reflectivity": "refc_ptype", "helicity": "hlcy_0-3000",
                "precip12": "precip_12hr_inch", "snow12": "snow_12hr", "snowTotal": "total_snow_10to1",
                "tmax": "t2m_f_max_last24", "tmin": "t2m_f_min_last24", "shear": "shear_0v6000m",
                "wind10": "wnd10m_stream_mph", "gust10": "gust_mph", "wind925": "uv925_mslp"}}
AIFS = {"product": "ecmwf-aifs-ensemble-avg", "domain": "namer", "min_frames": 40, "ref": "precip24",
        "maps": {"mslp": "t850_mslp_prcp6hr", "precip24": "precip_24hr_inch", "z500a5": "z500_anom_5day",
                 "mslpMin": "mslp_ens_min", "snow5": "snow_120hr"}}
GOES = {"goesW": ("goes-sat", "california", "ir_clean_window"),
        "goesE": ("goes-sat", "namer", "ir_clean_window")}
GOES_KEEP = 96


def collect_group(wb, spec, log):
    """Pick ONE run for the whole group, then pull every map at that run.

    The Chrome collector probed the first map (mslp) and that map has lagged
    one to eight runs behind the rest since 16 Oct 2026, which dragged the
    page. Here the reference map (gust10 / precip24) picks the run: the
    newest run whose reference frame list is complete (min_frames), else the
    run before it. A map that does not have that run yet falls back to its
    own newest earlier run and is reported as MAP LAG.
    Returns {key: map dict}, {key: error}, lag list, chosen init.
    """
    prod, dom, maps = spec["product"], spec["domain"], spec["maps"]
    ref_key = spec["ref"]
    ref_inits = wb.inits(prod, dom, maps[ref_key])
    chosen, ref_frames = None, None
    for init in ref_inits[:3]:
        fr = wb.forecast(prod, dom, maps[ref_key], init)
        if len(fr) >= spec["min_frames"]:
            chosen, ref_frames = init, fr
            break
        log(f"{prod} {ref_key} run {init} has {len(fr)} frames (< {spec['min_frames']}), trying the run before")
    if not chosen:
        raise WeatherBellError(f"{prod}: no complete run in the newest three for {ref_key}")

    out, errs, lag = {ref_key: _map(prod, dom, maps[ref_key], chosen, ref_frames)}, {}, []
    for key, param in maps.items():
        if key == ref_key:
            continue
        try:
            inits = wb.inits(prod, dom, param)
            use = chosen if chosen in inits else next((i for i in inits if int(i) <= int(chosen)), inits[-1])
            if use != chosen:
                lag.append({"group": prod, "map": key, "run": use, "groupRun": chosen,
                            "hours": round((int(chosen) - int(use)) / 3600)})
            out[key] = _map(prod, dom, param, use, wb.forecast(prod, dom, param, use))
        except WeatherBellError as e:
            errs[key] = str(e)
    return out, errs, lag, chosen


def _map(product, domain, param, init, frames):
    return {"product": product, "domain": domain, "param": param, "type": "model",
            "init": str(init), "frames": frames}


def collect_goes(wb, key):
    prod, dom, param = GOES[key]
    fr = wb.analysis(prod, dom, param)
    return {"type": "analysis", "product": prod, "domain": dom, "param": param, "frames": fr[-GOES_KEEP:]}
