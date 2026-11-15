"""Apple Health DuckDB adapter.

Reads a database that holds an Apple Health export as typed tables, so a dashboard can start in seconds
instead of re-parsing a multi-gigabyte ``export.xml``. It needs two tables, with UTC timestamps stored
as naive ``TIMESTAMP`` columns:

``records``
    One row per ``<Record>``: ``type`` (full HealthKit identifier, e.g.
    ``HKQuantityTypeIdentifierStepCount``), ``source_name``, ``value_num`` (quantity types), ``value_text``
    (category types such as sleep), ``start_utc``, ``end_utc``. Sorting by ``(type, start_utc)`` makes the
    per-type queries below cheap.
``workouts``
    One row per ``<Workout>``: ``workout_id``, ``workout_activity_type`` (full identifier), ``source_name``,
    ``start_utc``, ``end_utc``, ``duration_min``, ``active_energy`` and ``active_energy_unit``, ``distance``
    and ``distance_unit``, ``hr_avg``, ``hr_max`` (the workout's own statistics).

Nothing else is read, and the file is opened read-only. Days and sleep nights are bucketed in the reference
zone from :mod:`wearables.tz`, computed here from the UTC columns, so the zone a database's own views were
built with does not matter. Results match :mod:`wearables.adapters.apple_health` on the same export (the
parity test in ``tests/test_health_duckdb.py`` checks this).
"""

from datetime import UTC, date, datetime
from pathlib import Path

from wearables.adapters import apple_health as ah
from wearables.model import DailyMetric, Dataset, Workout
from wearables.tz import TZ

REQUIRED_TABLES = ("records", "workouts")

# Local calendar day of a UTC TIMESTAMP column, in the reference zone (DST-aware via DuckDB's ICU).
_LOCAL_DAY = f"CAST(timezone('{TZ.key}', timezone('UTC', start_utc)) AS DATE)"


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)


def _connect(path: Path):
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            f"{path.name}: reading a DuckDB file needs the duckdb package "
            "(pip install 'wearable-analyzer[duckdb]')"
        ) from exc
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise ValueError(f"{path.name}: cannot open as a DuckDB database ({exc})") from exc
    present = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if missing := [t for t in REQUIRED_TABLES if t not in present]:
        con.close()
        raise ValueError(f"{path.name}: missing table(s) {', '.join(missing)}; expected Apple Health tables")
    return con


def _workouts(con) -> list[Workout]:
    rows = con.execute(
        """
        SELECT workout_id, workout_activity_type, source_name, start_utc, end_utc, duration_min,
               active_energy, active_energy_unit, distance, distance_unit, hr_avg, hr_max
        FROM workouts ORDER BY start_utc, workout_id
        """
    ).fetchall()
    # Background heart rate, only for workouts with no statistics of their own.
    samples = {
        wid: (mean, peak, n)
        for wid, mean, peak, n in con.execute(
            """
            SELECT w.workout_id, avg(r.value_num), max(r.value_num), count(*)
            FROM workouts w
            JOIN records r ON r.type = ? AND r.start_utc BETWEEN w.start_utc AND w.end_utc
            WHERE w.hr_avg IS NULL
            GROUP BY w.workout_id
            """,
            [ah.T_HR],
        ).fetchall()
    }
    out = []
    for wid, kind, app, start, end, minutes, energy, energy_unit, dist, dist_unit, hr_avg, hr_max in rows:
        start, end = _utc(start), _utc(end)
        duration_s = minutes * 60 if minutes is not None else (end - start).total_seconds()
        if hr_avg is None and (hr := samples.get(wid)):
            mean, peak, n = hr
            if n >= max(ah.MIN_HR_SAMPLES, ah.MIN_HR_SAMPLES_PER_MIN * duration_s / 60):
                hr_avg, hr_max = mean, peak
        out.append(
            Workout(
                source=ah.SOURCE,
                sport=ah.sport_name(kind or ""),
                start=start,
                end=end,
                duration_s=duration_s,
                distance_m=ah._meters(dist, dist_unit or "") if dist is not None else None,
                energy_kcal=ah._kcal(energy, energy_unit or "") if energy is not None else None,
                avg_hr=hr_avg,
                max_hr=hr_max,
                app=app,
            )
        )
    return out


def _daily(con) -> list[DailyMetric]:
    def per_day(kind: str, agg: str) -> dict[date, float]:
        return dict(
            con.execute(
                f"SELECT {_LOCAL_DAY}, {agg}(value_num) FROM records WHERE type = ? GROUP BY 1", [kind]
            ).fetchall()
        )

    rhr, hrv = per_day(ah.T_RHR, "avg"), per_day(ah.T_HRV, "avg")
    # iPhone and Watch both count steps; the busiest source is the better single estimate.
    steps = dict(
        con.execute(
            f"""
            SELECT day, max(total) FROM (
                SELECT {_LOCAL_DAY} AS day, source_name, sum(value_num) AS total
                FROM records WHERE type = ? GROUP BY 1, 2
            ) GROUP BY day
            """,
            [ah.T_STEPS],
        ).fetchall()
    )
    return [
        DailyMetric(
            day=day,
            steps=round(steps[day]) if day in steps else None,
            resting_hr=rhr.get(day),
            hrv_ms=hrv.get(day),
        )
        for day in sorted(set(rhr) | set(hrv) | set(steps))
    ]


def _sleep(con):
    asleep, in_bed = [], []
    for value, start, end in con.execute(
        "SELECT value_text, start_utc, end_utc FROM records WHERE type = ? ORDER BY start_utc", [ah.T_SLEEP]
    ).fetchall():
        if start is None or end is None:
            continue
        span = (_utc(start), _utc(end))
        if value in ah.SLEEP_ASLEEP:
            asleep.append(span)
        elif value == ah.SLEEP_IN_BED:
            in_bed.append(span)
    return ah._sleep_nights(asleep, in_bed)


def load(path: str | Path) -> Dataset:
    path = Path(path)
    con = _connect(path)
    try:
        workouts, daily, sleep = _workouts(con), _daily(con), _sleep(con)
    finally:
        con.close()
    warnings = []
    if estimated := sum(s.in_bed_only for s in sleep):
        warnings.append(f"{estimated} sleep nights have no asleep stages; time in bed is used and flagged")
    return Dataset(workouts, daily, sleep, warnings=warnings)
