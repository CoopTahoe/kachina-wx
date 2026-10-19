"""Offline tests: real fixtures captured 18 Oct 2026, fake WeatherBell. Run: python3 -m unittest -v"""
import copy
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import weatherbell as wbm  # noqa: E402
import winterdesk as wd  # noqa: E402

FX = os.path.join(HERE, "fixtures")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


PAGE = _load(os.path.join(FX, "page_snapshot_2026-10-18.json"))
PAGE = _load(os.path.join(FX, "page_snapshot_2026-10-18.json"))


def kw_table(fields, rows):
    return {"head": {"fields": [{"name": f} for f in fields]},
            "data": [{"time": t, "vals": v} for t, v in rows]}


# Field orders and values copied from the live LoggerNet replies (18 Oct 2026 1715 PDT)
C6 = kw_table(["BattV_Avg", "TdF", "TwF", "WindDir", "WindDir_Avg", "Windspeed_Max", "Windspeed_Avg", "AirTF", "RH"],
              [("2026-10-18T17:15:00", [13.33, 29.93, 39.25, 221.3, 236.8, 22.59, 16.01, 48.81, 47.93])])
WK5 = kw_table(["DBTCDT_Avg", "AirTF_Avg", "TwF_Avg", "TdF_Avg", "RH", "DBTCDT"],
               [("2026-10-18T17:15:00", [1.159, 54.18, 43.37, 29.42, 38.42, -2.761])])
HF5 = kw_table(["BP_inHg", "RH", "TwF", "TdF", "AirTF", "DBTCDT", "DBTCDT_Avg"],
               [("2026-10-18T17:20:00", [30.16, 34.87, 39.11, 24.94, 51.79, -2.316, 5.28])])
# Whiskey hourly depth from the live table: sun-warmed sensor drifting 1 to 32 in with no snow -> must be None
WKH_DEPTHS = [-1.254, 0.302, 1.071, 0.423, 1.947, -0.183, 4.392, 1.988, 5.01, 15.92, 18.19, 27.95, 31.76]
WKH = kw_table(["DBTCDT_Avg"], [(f"2026-10-18T{5 + i:02d}:00:00", [d]) for i, d in enumerate(WKH_DEPTHS)])
HFH = kw_table(["DBTCDT_Avg"], [(f"2026-10-18T{5 + i:02d}:00:00", [0.2]) for i in range(13)])


def fake_fetch(url, as_json=True):
    if "weather.kirkwood.com" in url:
        for key, tab in (("Chair6_Top.Min15", C6), ("Whiskey.Min_5", WK5), ("HayFlat.FiveMin", HF5),
                         ("Whiskey.Hourly", WKH), ("HayFlat.Hourly", HFH)):
            if key in url.replace("%3A", ":"):
                return copy.deepcopy(tab)
        raise AssertionError(url)
    name = {wd.CSSL: "cssl.html", wd.CPC_WEEKLY: "cpc_wksst9120.for", wd.CPC_DISC: "ensodisc.html"}.get(url)
    if name:
        with open(os.path.join(FX, name), encoding="utf-8") as f:
            return f.read()
    if url.startswith(wd.CMM_PAGE):
        return '<div><script type="application/json" id="cmmw-snap">' + json.dumps(PAGE) + "</script></div>"
    raise AssertionError("unexpected fetch " + url)


class FakeWB:
    """Mimics the API: 12Z Oct 18 is the newest NAM run but only 30 frames in (still arriving),
    so the group must use 06Z. mslp only has runs up to 12Z Oct 16 (the lag seen on the live page)."""
    def __init__(self, fail_login=False):
        self.calls, self.logged_out = 0, False
        N12, N06, N16 = "1792324800", "1792303200", "1792152000"
        self.nam_inits = {p: [N12, N06, "1792281600"] for p in wbm.NAM["maps"].values()}
        self.nam_inits["t850_mslp_prcp6hr"] = [N16, "1792130400"]
        self.N12, self.N06, self.N16 = N12, N06, N16

    def inits(self, product, domain, param):
        self.calls += 1
        if product == "nam-nest":
            return self.nam_inits[param]
        return ["1792303200", "1792281600"]

    def forecast(self, product, domain, param, init):
        self.calls += 1
        n = 30 if (product == "nam-nest" and init == self.N12) else 61
        return [f"{int(init) + 3600 * i}-h{param[:3]}{i}" for i in range(n)]

    def analysis(self, product, domain, param):
        self.calls += 1
        return [f"{1792300000 + 300 * i}-g{domain[:2]}{i}" for i in range(120)]

    def logout(self):
        self.logged_out = True


class Pipeline(unittest.TestCase):
    def run_pipe(self, wb=None, skip_wb=False, seed="auto", prev=None):
        d = tempfile.mkdtemp()
        if prev is not None:
            with open(os.path.join(d, "snapshot.json"), "w", encoding="utf-8") as f:
                json.dump(prev, f)
        args = SimpleNamespace(out=d, skip_wb=skip_wb, seed=seed)
        rc = wd.run(args, fetch=fake_fetch, wb_factory=(lambda: wb) if wb else None)
        snap = _load(os.path.join(d, "snapshot.json")) if os.path.exists(os.path.join(d, "snapshot.json")) else None
        return rc, snap, _load(os.path.join(d, "health.json"))

    def test_full_run_schema_matches_page(self):
        wb = FakeWB()
        rc, s, h = self.run_pipe(wb=wb)
        self.assertEqual(rc, 0, h["failed"])
        self.assertEqual(set(s), set(PAGE))                       # same top-level keys as the live page
        self.assertEqual(set(s["wb"]), set(PAGE["wb"]))
        self.assertEqual(set(s["wb"]["nam"]), set(PAGE["wb"]["nam"]))
        self.assertEqual(set(s["wb"]["aifs"]), set(PAGE["wb"]["aifs"]))
        self.assertEqual(set(s["wb"]["nam"]["gust10"]), set(PAGE["wb"]["nam"]["gust10"]))
        self.assertEqual(set(s["kirkwood"]["cornice"]), set(PAGE["kirkwood"]["cornice"]))
        self.assertEqual(set(s["enso"]["disc"]), set(PAGE["enso"]["disc"]))
        self.assertTrue(wb.logged_out)
        self.assertEqual(wd.validate(s), [])

    def test_group_run_choice_and_map_lag(self):
        wb = FakeWB()
        rc, s, h = self.run_pipe(wb=wb)
        nam = s["wb"]["nam"]
        self.assertEqual(nam["gust10"]["init"], wb.N06)          # incomplete 12Z skipped
        self.assertEqual(nam["snow12"]["init"], wb.N06)          # whole group on one run
        self.assertEqual(nam["mslp"]["init"], wb.N16)            # lagging map keeps its own newest run
        lag = [x for x in h["mapLag"] if x["map"] == "mslp"]
        self.assertEqual(len(lag), 1)
        self.assertEqual(lag[0]["hours"], 42)
        self.assertEqual(len(s["wb"]["goesW"]["frames"]), 96)

    def test_kirkwood_values_and_noisy_sensor(self):
        rc, s, h = self.run_pipe(skip_wb=True)
        kw = s["kirkwood"]
        self.assertEqual(kw["cornice"], {"time": "1715 Oct 18", "dir": 236.8, "speed": 16.01, "gust": 22.59,
                                         "airT": 48.81, "wetB": 39.25})
        self.assertIsNone(kw["whiskey"]["hn24"])                 # noisy sensor: no number, never a guess
        self.assertEqual(kw["hayflat"]["hn24"], 0)
        self.assertEqual(kw["hayflat"]["airT"], 51.79)

    def test_snowlab_and_enso(self):
        rc, s, h = self.run_pipe(skip_wb=True)
        self.assertEqual(s["cssl"]["tempF"], 57.6)
        self.assertEqual(s["cssl"]["snow24"], 0)
        self.assertEqual(s["enso"]["weekly"], PAGE["enso"]["weekly"])   # same week -> same numbers
        self.assertEqual(s["enso"]["disc"], PAGE["enso"]["disc"])       # parser reproduces the desk job's values
        self.assertEqual(s["enso"]["read"], PAGE["enso"]["read"])       # nothing new -> Coop's read kept

    def test_new_cpc_week_resets_read_to_facts(self):
        prev = copy.deepcopy(PAGE)
        prev["enso"]["weekly"]["week"] = "2026-09-30"
        rc, s, h = self.run_pipe(skip_wb=True, prev=prev)
        t = s["enso"]["read"]["text"]
        self.assertIn("Niño-3.4 ran +3.3°C the week of October 7, up 0.1 from the week before.", t)
        self.assertIn("El Niño Advisory", t)
        self.assertIn("November 12", t)
        for banned in ("hill", "kid", "—"):
            self.assertNotIn(banned, t)

    def test_weatherbell_down_keeps_old_frames(self):
        def boom():
            raise wbm.WeatherBellError("Login set no WeatherBell cookie")
        d = tempfile.mkdtemp()
        rc = wd.run(SimpleNamespace(out=d, skip_wb=False, seed="page"), fetch=fake_fetch, wb_factory=boom)
        s = _load(os.path.join(d, "snapshot.json"))
        h = _load(os.path.join(d, "health.json"))
        self.assertEqual(rc, 1)                                  # published, but the run goes red
        self.assertIn("weatherbell_login", h["failed"])
        self.assertEqual(s["wb"]["nam"], PAGE["wb"]["nam"])      # old frames kept, nothing invented
        self.assertNotEqual(s["updated"], PAGE["updated"])

    def test_validation_blocks_broken_snapshot(self):
        prev = copy.deepcopy(PAGE)
        del prev["wb"]["nam"]["gust10"]
        rc, s, h = self.run_pipe(skip_wb=True, prev=prev)
        self.assertEqual(rc, 2)
        self.assertFalse(h["published"])
        self.assertIn("wb.nam.gust10", h["failed"]["validate"])

    def test_seed_from_page(self):
        rc, s, h = self.run_pipe(skip_wb=True)                  # no snapshot.json yet -> seeds from the page
        self.assertIn("seed_from_page", h["ok"])
        self.assertEqual(s["wb"]["aifs"], PAGE["wb"]["aifs"])


    def test_nothing_refreshed_does_not_restamp(self):
        def dead(url, as_json=True):
            raise OSError("network down")
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "snapshot.json"), "w", encoding="utf-8") as f:
            json.dump(PAGE, f)
        rc = wd.run(SimpleNamespace(out=d, skip_wb=True, seed="auto"), fetch=dead)
        self.assertEqual(rc, 2)
        self.assertEqual(_load(os.path.join(d, "snapshot.json"))["updated"], PAGE["updated"])
        self.assertFalse(_load(os.path.join(d, "health.json"))["published"])


class Frames(unittest.TestCase):
    def test_frames_filter_and_sort(self):
        self.assertEqual(wbm._frames(["1792540800-b", "1792450800-a", "junk"], "x"),
                         ["1792450800-a", "1792540800-b"])
        with self.assertRaises(wbm.WeatherBellError):
            wbm._frames({"error": "no forecast"}, "mslp")
        with self.assertRaises(wbm.WeatherBellError):
            wbm._frames([], "mslp")

    def test_page_frames_pass_the_frame_check(self):
        for g in ("nam", "aifs"):
            for m in PAGE["wb"][g].values():
                self.assertTrue(all(wbm.FRAME_RE.match(f) for f in m["frames"]))


if __name__ == "__main__":
    unittest.main()
