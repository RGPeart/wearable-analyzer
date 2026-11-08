# wearable-analyzer

Source-agnostic analytics for wearable and training data. It reads an **Apple Health export** (Apple Watch / iPhone), a **Fitbod** lifting export and **GPX** tracks, reconciles workouts that more than one app recorded, and prints workout, recovery, training-load and lifting-volume summaries. Everything runs locally; nothing is uploaded.

```
$ wearables summary demo/          # a folder holding export.xml and WorkoutExport.csv
94 workouts, 2026-05-05 to 2026-10-27

sport              count  hours     km
-----------------  -----  -----  -----
running               69   49.1  505.9
strength_training     14   10.5    0.0
walking               11    7.3   32.9

Last 30 days up to 2026-10-31:
  resting HR: 60.5 bpm (prior 30d 58.8, +1.6)
  HRV (SDNN): 47.2 ms (prior 30d 48.0, -0.8)
  sleep: 7.0 h/night
  steps: 8,502/day

$ wearables strength demo/ --weeks 4 --top 4
week of     sessions  sets  reps  volume lb
----------  --------  ----  ----  ---------
2026-10-19         1    10    41      7,310
...
exercise        sets  volume lb  best lb
--------------  ----  ---------  -------
Back Squat        15     20,470      195
Bench Press       18     18,450      140
```

(All numbers above come from the built-in synthetic generator, not a real person.)

Charts come two ways, and both draw from the same figure code (`wearables/figures.py`):

```
wearables report demo/ -o report.html   # one HTML file: summary tiles + 6 charts, opens in any browser
wearables app demo/                     # interactive dashboard on http://127.0.0.1:8501 (Streamlit)
```

## Install

```
uv venv && uv pip install -e '.[dev]'    # Python 3.11+, everything incl. charts and tests
uv pip install -e .                      # CLI only (gpxpy)
uv pip install -e '.[report]'            # + plotly, for `wearables report`
uv pip install -e '.[app]'               # + plotly and streamlit, for `wearables app`
```

## Use

Export your data: Health app → profile picture → **Export All Health Data** (Apple), and Fitbod's workout CSV export (`WorkoutExport.csv`). Keep both out of git (`.gitignore` already blocks `*.zip`, `export.xml`, `*.gpx`).

```
wearables summary  export.zip                 # sports, 30-day resting HR / HRV / sleep / steps trends
wearables weekly   export.zip --weeks 12      # workouts, hours, km, kcal per week
wearables load     export.zip                 # rough 7d vs 28d training load
wearables strength my-data/                   # weekly lifting volume + top exercises (needs Fitbod)
wearables summary  routes/ --json             # a folder of .gpx files; --json works everywhere
wearables report   my-data/ -o report.html    # charts as one file; --since 2026-01-01, --offline
wearables app      my-data/                   # interactive dashboard (range, heart-rate overrides)
wearables synth    demo.xml --fitbod demo.csv # synthetic Apple export + Fitbod CSV for demos
```

A path can be an Apple `export.xml` / `export.zip`, a Fitbod `WorkoutExport.csv`, a `.gpx` file, or a folder. A folder holding both an Apple export and a Fitbod CSV is merged. `python -m wearables ...` works too. The zip is read in place and the XML is streamed, so large exports do not need to fit in memory. Notes about merging and skipped records go to stderr.

## Design

```
adapters/apple_health.py ┐
adapters/fitbod.py       ├─> model.Dataset ─> merge.reconcile ─> analytics ─┬─> cli
adapters/gpx.py          ┘                                                  └─> figures ─┬─> report (static HTML)
(future: Oura, Garmin, …)                                                                └─> app (Streamlit)
```

The charts grew in three steps, each on top of the last: the CLI and `--json` first, then `figures.py` plus a static `report` (a file you can open, send or archive, with no server), then the Streamlit `app` as a thin layer that calls the same figure functions and adds a range picker and heart-rate overrides. Fixing a chart in `figures.py` fixes it in both.

An adapter is a module with `load(path) -> Dataset`. Adapters only say what their source says. `merge.reconcile` then resolves overlaps, and analytics only see the neutral model (`Workout`, `StrengthSet`, `DailyMetric`, `SleepNight`), so a new source needs no changes to them.

Choices worth knowing:

- **One time zone.** Apple prints every date in the exporting phone's zone, whatever the sample's real zone, so the printed offset is not local time. UTC instants stay the stored value and the join key; days, weeks and sleep nights are bucketed in `America/New_York` (DST-aware, `wearables/tz.py`). Hour-of-day for travel in other zones therefore reads in ET.
- **Fitbod ↔ Apple join.** Fitbod writes each strength session to Apple Health as a workout with the same start instant (matched to the second on real data; the window is ±2 min). The merged workout keeps Fitbod's sets and takes Apple's duration, energy and heart rate, because Fitbod's CSV has no end time or heart rate.
- **Fitbod's one-row cardio entries** are copies of Apple workouts (Watch, Nike Run Club, …). They are dropped when the Apple original is within 5 minutes and kept otherwise.
- **Overlapping duplicates** (same sport, different app, ≥80% overlap, e.g. a gym app and a Watch logging one ride) collapse to the record with heart-rate data; a workout that owns lifting sets always wins.
- **Fitbod CSV quirks** handled: UTC dates repeated per set, newest-first file with performed-order rows, identical rows for identical sets (never de-duplicated), leading spaces, blank lines, and pounds converted to kg with float noise (snapped back to quarter pounds).
- **Heart rate per workout** comes from the workout's own statistics. A fallback from raw samples is used only when they average at least one per minute; sparse background sampling would give a misleading average, so those workouts show none.
- **Sleep** is the union of all "asleep" intervals (a Watch and a phone count once). Nights with only in-bed data (pre-iOS 16) fall back to time in bed, are flagged `in_bed_only`, and stay out of the sleep average.
- **Steps**: iPhone and Watch both count; the busiest source per day is used.
- **Lifting volume** = reps × weight × multiplier, warm-ups excluded. Fitbod does not document the multiplier (2.0 for a pair of dumbbells, 0.0 for bodyweight and holds); this is the usual reading.
- **Training load** = minutes × share of heart-rate reserve used (clamped 0 to 1), as 7-day vs 28-day daily averages. It is a rough way to compare weeks, not medical or coaching advice. Workouts without heart rate are skipped and counted, so it understates load when many are missing.
- GPX elevation gain is smoothed by `gpxpy`, so it reads lower than a raw sum.
- **Charts stay local.** The report is a file; by default it loads plotly.js from a CDN (the page fetches the script, your data is embedded in the file and never sent), and `--offline` embeds the library instead (~5 MB). The app binds to `127.0.0.1` and turns Streamlit's usage statistics off. A large Apple export takes a couple of minutes to parse; the app parses once per server run and keeps the result.
- **Load chart caveat.** Workouts with no heart rate are left out and the chart title counts them, so on data where many are missing the lines understate real load.

## Tests

```
pytest && ruff check .
```

The synthetic generator doubles as the test oracle. `synth.build` makes a ground-truth person, `write_apple_export` renders an Apple-style XML (duplicate step sources, overlapping sleep, heart-rate samples, Fitbod-written strength workouts) and `write_fitbod_csv` renders a Fitbod-style CSV (CRLF, blank lines, noisy weights, cardio copies). Round-trip tests check the parsers and the merge recover the truth. Hand-written fixtures cover legacy formats, unit conversions, bad records, time-zone edges and merge corner cases.

## Not yet

- No FIT, TCX, Oura, Garmin or Google Health adapters (Garmin's Personal API and Oura's OAuth API are the candidates).
- The app re-parses on every server start (no on-disk cache), and the charts were checked by tests and a headless run rather than by eye on every browser.
- No hosted demo of the app; the synthetic data would make a safe one.
- Hevy (a second lifting app) is only seen through the Apple workouts it writes; there is no set-level reader.
- Sleep before iOS 16 has no stages, so its quality cannot be analysed.

## Libraries

[`gpxpy`](https://github.com/tkrajina/gpxpy) (Apache-2.0, 1K+ stars) for GPX. Optional: [`plotly`](https://github.com/plotly/plotly.py) (MIT, 16K+ stars) for charts and [`streamlit`](https://github.com/streamlit/streamlit) (Apache-2.0, 40K+ stars) for the dashboard, both chosen over hand-rolled SVG/HTML because the interactivity (hover, zoom, range) is what they exist for. Everything else is the standard library (`zoneinfo` for time zones); `pytest` and `ruff` for development.

## License

MIT
