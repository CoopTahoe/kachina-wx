# Winter Desk in the cloud: setup

Built 18 Oct 2026 by Thor (chief of staff) after both desk jobs failed all afternoon on the
Squarespace admin permission check. This moves the weather-page data off the Mac and out of Chrome.

## What changes

| Before | After |
|---|---|
| CMM desk job (Chrome on the Mac) pulls data and saves it into the Squarespace page | GitHub Actions builds `snapshot.json` four times a day, with a backup slot 30 min earlier |
| KSEF desk job copies the CMM snapshot into the KSEF page | Both pages read the same `snapshot.json` through a small live loader. No copy job |
| A failed save leaves the page stale until a person steps in | A failed part keeps its last good value, the run turns red, and GitHub emails you |

Shared file: `https://cooptahoe.github.io/kachina-wx/snapshot.json` (run report: `health.json`)

## Files

- `winterdesk.py`: builds the snapshot in the same schema the pages already use (WeatherBell frames, Kirkwood stations, Snow Lab, CPC ENSO)
- `weatherbell.py`: signs in with your WeatherBell login, lists the map frames, signs out
- `.github/workflows/kachina-wx.yml`: replaces the current workflow file
- `tests/`: offline tests using real data captured 18 Oct 2026. The workflow runs them before every build
- `page-loader/cmm-loader.html`, `page-loader/ksef-loader.html`: one paste per page

## Setup, about 15 minutes

1. **Upload the files.** github.com/CoopTahoe/kachina-wx, then Add file, then Upload files. Drag in everything from this folder, keeping the folder paths. Commit to `main`.
2. **Add two secrets.** Settings, Secrets and variables, Actions, New repository secret:
   `WB_USERNAME` (your WeatherBell username or email) and `WB_PASSWORD`.
3. **Check Pages.** Settings, Pages: Source "Deploy from a branch", `main`, `/ (root)`.
4. **Check Actions is on.** Actions tab. If GitHub shows a banner saying workflows are disabled, enable them. The old workflow stopped publishing after 13 Oct; the cause is not established.
5. **First run.** Actions, kachina-wx, Run workflow, tick **seed_from_page**, Run.
6. **Check the result.** Open `https://cooptahoe.github.io/kachina-wx/health.json`. Expect `"published":true` and `"failed":{}`. If `weatherbell_login` is listed under `failed`, the maps are the only thing that did not refresh; read the reason in that line.
7. **Paste the loaders** (needs your OK, see below). In each page's weather code block, paste the matching loader **right after** the line that closes the JSON snapshot tag (`<script type="application/json" id="cmmw-snap">…</script>`, or `id="ksefw-snap"` on KSEF), just before the main `<script>`. Save.
8. **Verify each page.** The stamp ("Winter Desk data updated HHMM" or "Kirkwood desk data updated HHMM") should show the latest GitHub run time.

## How the loader behaves (tested in headless Chrome on copies of both live pages)

| Shared file | Page shows | `data-snap-src` on the root div |
|---|---|---|
| Newer than the embedded copy | the shared file | `live` |
| Older than the embedded copy | the embedded copy | `embedded-newer` |
| 404 or bad JSON | the embedded copy | `embedded-fallback` |
| No answer in 2.5 s | the embedded copy | `embedded-timeout` |

The loader never edits the Overwatch guard or the main script. Heading count and map count matched the unpatched page in every case.

## Held for your OK (Thor has not changed any of these)

1. **Paste the two loaders** into the CMM and KSEF code blocks (step 7). This is the one-time loader edit you approved on 17 Oct.
2. **Overwatch prompt:** make `snapshot.json` and `health.json` the check of record for freshness and frames. Also confirm `Kachina live loader` and `OWG=` are on both pages, and stop firing the desk jobs (they fail on the permission check anyway).
3. **KSEF desk job** (trig_01KQmCwUHkSNJgKpegxwLyfF): disable once the KSEF loader is live, because it becomes redundant.
4. **CMM desk job** (trig_01AV4JnF68j5xiVdB52CpCae): disable after two clean GitHub runs. Leaving it on is harmless, because the loader always picks the newer snapshot.
5. **Ops Board STEP 4 and the Reel job:** read `snapshot.json` directly instead of the page's raw HTML. Raw HTML keeps the old embedded copy, so a job reading it would see stale data.
6. **Old files** `cmm_winterdesk_auto.json`, `ksef_*_auto.json`, `run_report.json`, `state.json` and `kachina_wx.py`: delete them, so no job reads the retired pipe by mistake.

## Risks, with tags

- [Guessing] **WeatherBell terms.** Your license covers public use of the maps. Whether it also covers scripted access to their maps API is not established, so confirm with WeatherBell before relying on it.
- [Likely] **Session limit.** The WeatherBell app answers HTTP 417 when an account has too many sessions open. The script signs out at the end of every run, but a run that overlaps your own browsing could still trip it. `health.json` will say so.
- [Guessing] **Bot protection.** WeatherBell may block sign-ins from GitHub's servers. If that happens, everything except the maps keeps updating, the old map frames stay, and the guard swaps in the NOAA backups once those frames age out.
- [Certain] **The WeatherBell part is untested live.** Its calls are built from the maps.weatherbell.com app code (18 Oct 2026). This session had no way to sign in, so the first real run is the test.
- [Certain] **Fixed:** the old workflow committed as `kachina@users.noreply.github.com`, which GitHub links to a stranger's account named "kachina". New commits post as `github-actions[bot]`.

Coop | RS#69
