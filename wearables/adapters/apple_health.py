"""Apple Health ``export.xml`` / ``export.zip`` adapter.

The file is streamed with ``iterparse`` because real exports reach gigabytes.
The zip is read in place; nothing is extracted to disk.
"""

import dataclasses
import re
import zipfile
from array import array
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import IO
from xml.etree import ElementTree as ET

from wearables.model import DailyMetric, Dataset, SleepNight, Workout
from wearables.tz import local_day, local_night

SOURCE = "apple_health"

_Q = "HKQuantityTypeIdentifier"
T_HR = f"{_Q}HeartRate"
T_RHR = f"{_Q}RestingHeartRate"
T_HRV = f"{_Q}HeartRateVariabilitySDNN"
T_STEPS = f"{_Q}StepCount"
T_ENERGY = f"{_Q}ActiveEnergyBurned"
T_SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
_SLEEP = "HKCategoryValueSleepAnalysis"
SLEEP_ASLEEP = {
    f"{_SLEEP}Asleep",  # pre-iOS 16
    f"{_SLEEP}AsleepUnspecified",
    f"{_SLEEP}AsleepCore",
    f"{_SLEEP}AsleepDeep",
    f"{_SLEEP}AsleepREM",
}
SLEEP_IN_BED = f"{_SLEEP}InBed"

# Apple splits one idea into several types; the analyzer treats them alike.
SPORT_ALIASES = {
    "functional_strength_training": "strength_training",
    "traditional_strength_training": "strength_training",
}
# Background heart-rate sampling is sparse; an average from a couple of samples is misleading.
MIN_HR_SAMPLES_PER_MIN = 1.0
MIN_HR_SAMPLES = 3

_LENGTH_M = {"m": 1.0, "km": 1000.0, "mi": 1609.344, "yd": 0.9144, "ft": 0.3048}
_DURATION_S = {"s": 1.0, "sec": 1.0, "min": 60.0, "hr": 3600.0}


def parse_date(s: str) -> datetime:
    """``2024-03-01 07:12:00 -0500`` -> aware datetime (much faster than strptime)."""
    return datetime.fromisoformat(f"{s[:19]}{s[20:23]}:{s[23:25]}")


def sport_name(raw: str) -> str:
    s = raw.removeprefix("HKWorkoutActivityType") or "other"
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()
    return SPORT_ALIASES.get(name, name)


def _meters(value: float, unit: str) -> float | None:
    factor = _LENGTH_M.get(unit)
    return value * factor if factor else None


def _kcal(value: float, unit: str) -> float | None:
    if unit in {"Cal", "kcal"}:
        return value
    if unit == "kJ":
        return value / 4.184
    return None


def _num(attrs: dict[str, str], key: str) -> float | None:
    try:
        return float(attrs[key])
    except (KeyError, ValueError):
        return None


@contextmanager
def open_export(path: Path) -> Iterator[IO[bytes]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "export.xml"]
            if not names:
                raise ValueError(f"{path.name}: no export.xml inside the zip")
            with zf.open(names[0]) as fh:
                yield fh
    else:
        with path.open("rb") as fh:
            yield fh


class _Accumulator:
    def __init__(self) -> None:
        self.hr_ts = array("d")
        self.hr_bpm = array("f")
        self.hr_sorted = True
        self.rhr: dict[date, list[float]] = defaultdict(list)
        self.hrv: dict[date, list[float]] = defaultdict(list)
        self.steps: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.asleep: list[tuple[datetime, datetime]] = []
        self.in_bed: list[tuple[datetime, datetime]] = []
        self.workouts: list[Workout] = []
        self.current: dict | None = None
        self.skipped = 0

    def record(self, a: dict[str, str]) -> None:
        kind = a.get("type")
        if kind not in {T_HR, T_RHR, T_HRV, T_STEPS, T_SLEEP}:
            return
        try:
            start = parse_date(a["startDate"])
            if kind == T_SLEEP:
                value = a.get("value")
                if value in SLEEP_ASLEEP:
                    self.asleep.append((start, parse_date(a["endDate"])))
                elif value == SLEEP_IN_BED:
                    self.in_bed.append((start, parse_date(a["endDate"])))
                return
            value = float(a["value"])
        except (KeyError, ValueError):
            self.skipped += 1
            return
        if kind == T_HR:
            ts = start.timestamp()
            if self.hr_ts and ts < self.hr_ts[-1]:
                self.hr_sorted = False
            self.hr_ts.append(ts)
            self.hr_bpm.append(value)
        elif kind == T_RHR:
            self.rhr[local_day(start)].append(value)
        elif kind == T_HRV:
            self.hrv[local_day(start)].append(value)
        else:
            self.steps[local_day(start)][a.get("sourceName", "?")] += value

    def begin_workout(self) -> None:
        self.current = {"distance_m": None, "energy_kcal": None, "avg_hr": None, "max_hr": None}

    def workout_stat(self, a: dict[str, str]) -> None:
        if self.current is None:
            return
        kind, unit = a.get("type", ""), a.get("unit", "")
        if kind.startswith(f"{_Q}Distance"):
            total = _num(a, "sum")
            if total is not None:
                self.current["distance_m"] = _meters(total, unit)
        elif kind == T_ENERGY:
            total = _num(a, "sum")
            if total is not None:
                self.current["energy_kcal"] = _kcal(total, unit)
        elif kind == T_HR:
            self.current["avg_hr"] = _num(a, "average")
            self.current["max_hr"] = _num(a, "maximum")

    def end_workout(self, a: dict[str, str]) -> None:
        cur, self.current = self.current or {}, None
        try:
            start, end = parse_date(a["startDate"]), parse_date(a["endDate"])
        except (KeyError, ValueError):
            self.skipped += 1
            return
        duration = _num(a, "duration")
        if duration is None:
            duration_s = (end - start).total_seconds()
        else:
            duration_s = duration * _DURATION_S.get(a.get("durationUnit", "min"), 60.0)
        # Pre-iOS 16 exports keep totals on the Workout element itself.
        if cur.get("distance_m") is None and (legacy := _num(a, "totalDistance")) is not None:
            cur["distance_m"] = _meters(legacy, a.get("totalDistanceUnit", ""))
        if cur.get("energy_kcal") is None and (legacy := _num(a, "totalEnergyBurned")) is not None:
            cur["energy_kcal"] = _kcal(legacy, a.get("totalEnergyBurnedUnit", ""))
        self.workouts.append(
            Workout(
                source=SOURCE,
                sport=sport_name(a.get("workoutActivityType", "")),
                start=start,
                end=end,
                duration_s=duration_s,
                app=a.get("sourceName"),
                **cur,
            )
        )

    def heart_rate_between(self, start: datetime, end: datetime) -> tuple[float, float, int] | None:
        """(mean, max, sample count) of heart-rate samples inside the window."""
        lo, hi = bisect_left(self.hr_ts, start.timestamp()), bisect_right(self.hr_ts, end.timestamp())
        window = self.hr_bpm[lo:hi]
        return (fmean(window), max(window), len(window)) if window else None

    def sort_heart_rate(self) -> None:
        if self.hr_sorted:
            return
        order = sorted(range(len(self.hr_ts)), key=self.hr_ts.__getitem__)
        self.hr_ts = array("d", (self.hr_ts[i] for i in order))
        self.hr_bpm = array("f", (self.hr_bpm[i] for i in order))
        self.hr_sorted = True


def merge_intervals(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Union of overlapping spans, so a Watch and a phone logging the same sleep count once."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _night_totals(spans: list[tuple[datetime, datetime]]) -> dict[date, float]:
    totals: dict[date, float] = defaultdict(float)
    for start, end in merge_intervals(spans):
        totals[local_night(start)] += (end - start).total_seconds()
    return totals


def _sleep_nights(
    asleep: list[tuple[datetime, datetime]], in_bed: list[tuple[datetime, datetime]]
) -> list[SleepNight]:
    """Asleep time per night; nights with no asleep stages (pre-iOS 16) fall back to time in bed, flagged."""
    nights = {n: SleepNight(n, secs) for n, secs in _night_totals(asleep).items()}
    for night, secs in _night_totals(in_bed).items():
        nights.setdefault(night, SleepNight(night, secs, in_bed_only=True))
    return [nights[n] for n in sorted(nights)]


def _daily(acc: _Accumulator) -> list[DailyMetric]:
    days = set(acc.rhr) | set(acc.hrv) | set(acc.steps)
    out = []
    for day in sorted(days):
        # iPhone and Watch both count steps; the busiest source is the better single estimate.
        per_source = acc.steps.get(day)
        out.append(
            DailyMetric(
                day=day,
                steps=round(max(per_source.values())) if per_source else None,
                resting_hr=fmean(acc.rhr[day]) if day in acc.rhr else None,
                hrv_ms=fmean(acc.hrv[day]) if day in acc.hrv else None,
            )
        )
    return out


def load(path: str | Path) -> Dataset:
    acc = _Accumulator()
    records = 0
    with open_export(Path(path)) as fh:
        root = None
        for event, elem in ET.iterparse(fh, events=("start", "end")):
            if root is None:
                root = elem
            tag = elem.tag
            if event == "start":
                if tag == "Workout":
                    acc.begin_workout()
                continue
            if tag == "Record":
                acc.record(elem.attrib)
                elem.clear()
                records += 1
                if records % 50_000 == 0:
                    root.clear()  # drop references to finished children
            elif tag == "WorkoutStatistics":
                acc.workout_stat(elem.attrib)
            elif tag == "Workout":
                acc.end_workout(elem.attrib)
                elem.clear()

    acc.sort_heart_rate()
    workouts = []
    for w in acc.workouts:
        if w.avg_hr is None and (hr := acc.heart_rate_between(w.start, w.end)):
            mean, peak, n = hr
            if n >= max(MIN_HR_SAMPLES, MIN_HR_SAMPLES_PER_MIN * w.duration_s / 60):
                w = dataclasses.replace(w, avg_hr=mean, max_hr=peak)
        workouts.append(w)
    workouts.sort(key=lambda w: w.start)

    sleep = _sleep_nights(acc.asleep, acc.in_bed)
    warnings = []
    if acc.skipped:
        warnings.append(f"skipped {acc.skipped} records with unreadable dates or values")
    if estimated := sum(s.in_bed_only for s in sleep):
        warnings.append(f"{estimated} sleep nights have no asleep stages; time in bed is used and flagged")
    return Dataset(workouts, _daily(acc), sleep, warnings=warnings)
