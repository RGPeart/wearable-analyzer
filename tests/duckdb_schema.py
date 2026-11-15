"""Builds the minimal ``records`` / ``workouts`` tables the DuckDB adapter reads, from an Apple export.xml.

This is a test stand-in for a real pipeline: it mirrors the columns documented in
``wearables/adapters/health_duckdb.py`` (UTC naive timestamps, quantity values in ``value_num``, category values
in ``value_text``, workout statistics pivoted onto the workout row) and nothing more.
"""

from datetime import UTC
from pathlib import Path
from xml.etree import ElementTree as ET

import duckdb

from wearables.adapters.apple_health import parse_date

_DURATION_MIN = {"min": 1.0, "s": 1 / 60, "sec": 1 / 60, "hr": 60.0}

SCHEMA = """
CREATE TABLE records (
    record_id BIGINT, type VARCHAR, metric VARCHAR, source_name VARCHAR, unit VARCHAR,
    value_num DOUBLE, value_text VARCHAR, start_utc TIMESTAMP, end_utc TIMESTAMP
);
CREATE TABLE workouts (
    workout_id INTEGER, workout_activity_type VARCHAR, source_name VARCHAR,
    start_utc TIMESTAMP, end_utc TIMESTAMP, duration_min DOUBLE,
    active_energy DOUBLE, active_energy_unit VARCHAR, distance DOUBLE, distance_unit VARCHAR,
    hr_avg DOUBLE, hr_max DOUBLE
);
"""


def _utc(raw: str):
    return parse_date(raw).astimezone(UTC).replace(tzinfo=None)


def _number(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _workout(wid: int, a: dict, stats: list[dict]) -> tuple:
    energy = next((s for s in stats if s["type"].endswith("ActiveEnergyBurned")), None)
    dist = max(
        (s for s in stats if "Distance" in s["type"]), key=lambda s: _number(s.get("sum")) or 0, default=None
    )
    hr = next((s for s in stats if s["type"].endswith("HeartRate")), None)
    duration = _number(a.get("duration"))
    if duration is not None:
        duration *= _DURATION_MIN.get(a.get("durationUnit", "min"), 1.0)
    return (
        wid,
        a.get("workoutActivityType"),
        a.get("sourceName"),
        _utc(a["startDate"]),
        _utc(a["endDate"]),
        duration,
        _number(energy.get("sum")) if energy else _number(a.get("totalEnergyBurned")),
        energy.get("unit") if energy else a.get("totalEnergyBurnedUnit"),
        _number(dist.get("sum")) if dist else _number(a.get("totalDistance")),
        dist.get("unit") if dist else a.get("totalDistanceUnit"),
        _number(hr.get("average")) if hr else None,
        _number(hr.get("maximum")) if hr else None,
    )


def build(xml_path: str | Path, db_path: str | Path) -> Path:
    records, workouts, stats = [], [], []
    for _, elem in ET.iterparse(xml_path):
        if elem.tag == "WorkoutStatistics":
            stats.append(dict(elem.attrib))
        elif elem.tag == "Workout":
            workouts.append(_workout(len(workouts) + 1, elem.attrib, stats))
            stats = []
        elif elem.tag == "Record":
            a = elem.attrib
            number = _number(a.get("value"))
            kind = a["type"]
            records.append(
                (
                    len(records) + 1,
                    kind,
                    kind.replace("HKQuantityTypeIdentifier", "").replace("HKCategoryTypeIdentifier", ""),
                    a.get("sourceName"),
                    a.get("unit"),
                    number,
                    None if number is not None else a.get("value"),
                    _utc(a["startDate"]),
                    _utc(a["endDate"]),
                )
            )
    con = duckdb.connect(str(db_path))
    con.execute(SCHEMA)
    con.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)", records)
    con.executemany("INSERT INTO workouts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", workouts)
    con.close()
    return Path(db_path)
