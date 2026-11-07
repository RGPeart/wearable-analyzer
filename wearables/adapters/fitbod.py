"""Fitbod ``WorkoutExport.csv`` adapter.

Quirks of the real export (profiled on a 45k-row file):

* ``Date`` is the workout *start* in UTC, repeated on every set row. There is no end time and no per-set time.
* Rows within a workout are in performed order, but the file as a whole runs newest-first.
* Identical sets are identical rows (no set number). Never de-duplicate rows.
* ``Reps`` and ``multiplier`` have a leading space, and a few blank lines sit between rows.
* Weights are pounds converted to kg with float noise, so quarter-pound values are snapped back.
* A one-row "workout" with a distance or timed effort is cardio Fitbod imported from Apple Health.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

from wearables.model import Dataset, StrengthSet, Workout

SOURCE = "fitbod"
HEADER_PREFIX = "Date,Exercise,Reps"
KG_PER_LB = 0.45359237
_SNAP_TOLERANCE_LB = 0.005


def is_fitbod_csv(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return fh.readline().startswith(HEADER_PREFIX)
    except (OSError, UnicodeDecodeError):
        return False


def parse_date(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S %z")


def snap_weight_kg(kg: float) -> float:
    """Snap a lb-converted weight to the nearest quarter pound; leave true-kilogram values alone."""
    lb = kg / KG_PER_LB
    quarter = round(lb * 4) / 4
    return quarter * KG_PER_LB if abs(lb - quarter) <= _SNAP_TOLERANCE_LB else kg


def _f(s: str) -> float:
    s = s.strip()
    return float(s) if s else 0.0


def _is_cardio(rows: list[dict[str, str]]) -> bool:
    if len(rows) != 1:
        return False
    r = rows[0]
    return _f(r["Distance(m)"]) > 0 or (_f(r["Duration(s)"]) > 0 and _f(r["Reps"]) == 0)


def load(path: str | Path) -> Dataset:
    groups: dict[str, list[dict[str, str]]] = {}
    bad = 0
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row.get("Date") or row.get("Exercise") is None:
                continue  # blank lines between rows
            groups.setdefault(row["Date"], []).append(row)

    ds = Dataset()
    for date_str, rows in groups.items():
        try:
            start = parse_date(date_str)
            if _is_cardio(rows):
                r = rows[0]
                duration = _f(r["Duration(s)"])
                ds.workouts.append(
                    Workout(
                        source=SOURCE,
                        sport=r["Exercise"].strip().lower().replace(" ", "_"),
                        start=start,
                        end=start + timedelta(seconds=duration),
                        duration_s=duration,
                        distance_m=_f(r["Distance(m)"]) or None,
                        app="Fitbod",
                        imported=True,
                    )
                )
                continue
            sets = [
                StrengthSet(
                    session_start=start,
                    order=i,
                    exercise=r["Exercise"].strip(),
                    reps=int(_f(r["Reps"])),
                    weight_kg=snap_weight_kg(_f(r["Weight(kg)"])),
                    multiplier=_f(r["multiplier"]),
                    duration_s=_f(r["Duration(s)"]),
                    distance_m=_f(r["Distance(m)"]),
                    is_warmup=r["isWarmup"].strip().lower() == "true",
                )
                for i, r in enumerate(rows, start=1)
            ]
        except (ValueError, KeyError):
            bad += len(rows)
            continue
        ds.sets += sets
        ds.workouts.append(
            Workout(
                source=SOURCE, sport="strength_training", start=start, end=start, duration_s=0.0, app="Fitbod"
            )
        )
    if bad:
        ds.warnings.append(f"fitbod: skipped {bad} rows with unreadable values")
    ds.workouts.sort(key=lambda w: w.start)
    ds.sets.sort(key=lambda s: (s.session_start, s.order))
    return ds
