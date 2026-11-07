"""Synthetic data: a made-up person, rendered as a realistic Apple Health ``export.xml``.

``build`` returns the ground truth; ``write_apple_export`` renders it plus the
messy parts of a real export (two devices counting the same steps, overlapping
sleep records, heart-rate samples). Parsing the file should give the truth back,
which is what the round-trip tests check. It also makes a safe public demo.
"""

import math
import random
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

from wearables.model import DailyMetric, Dataset, SleepNight, StrengthSet, Workout

KG_PER_LB = 0.45359237
WATCH = "Apple Watch"
# Apple's exports print every date in the exporting phone's zone, here fixed EST like a real November export.
TZ = timezone(timedelta(hours=-5))
_APPLE_TYPE = {"strength_training": "FunctionalStrengthTraining"}
_Q = "HKQuantityTypeIdentifier"
_S = "HKCategoryValueSleepAnalysis"
_W = "HKWorkoutActivityType"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def _workout(rng: random.Random, day: date, sport: str) -> Workout:
    if sport == "running":
        long_run = day.weekday() == 5
        km = _clamp(rng.gauss(10 if long_run else 5.5, 1.5), 3, 16)
        duration = round(km * _clamp(rng.gauss(5.8, 0.4), 4.5, 7.5) * 60)
        avg = round(_clamp(rng.gauss(152, 6), 130, 175), 1)
        start = _at(day, 8 if long_run else 18)
        return Workout(
            source="synthetic",
            sport=sport,
            start=start,
            end=start + timedelta(seconds=duration),
            duration_s=duration,
            distance_m=round(km * 1000, 1),
            energy_kcal=round(km * rng.gauss(62, 3), 1),
            avg_hr=avg,
            max_hr=round(avg + rng.gauss(14, 3), 1),
            app=WATCH,
        )
    if sport == "strength_training":
        duration, avg = 45 * 60, round(rng.gauss(115, 6), 1)
        start = _at(day, 7)
        return Workout(
            source="synthetic",
            sport=sport,
            start=start,
            end=start + timedelta(seconds=duration),
            duration_s=duration,
            energy_kcal=round(rng.gauss(250, 25), 1),
            avg_hr=avg,
            max_hr=round(avg + 25, 1),
            app="Fitbod",
        )
    duration, start = 40 * 60, _at(day, 10)
    return Workout(
        source="synthetic",
        sport="walking",
        start=start,
        end=start + timedelta(seconds=duration),
        duration_s=duration,
        distance_m=round(rng.gauss(3000, 300), 1),
        energy_kcal=round(rng.gauss(150, 15), 1),
        avg_hr=round(rng.gauss(100, 5), 1),
        max_hr=118.0,
        app=WATCH,
    )


# (exercise, load multiplier, base weight in lb, timed hold in seconds)
_LIFTS = [
    ("Back Squat", 1.0, 185, 0),
    ("Bench Press", 1.0, 135, 0),
    ("Deadlift", 1.0, 225, 0),
    ("Dumbbell Curl", 2.0, 25, 0),
    ("Overhead Press", 1.0, 95, 0),
    ("Plank", 0.0, 0, 45),
]


def _strength_sets(rng: random.Random, w: Workout) -> list[StrengthSet]:
    out: list[StrengthSet] = []
    for name, mult, base_lb, hold in rng.sample(_LIFTS, 3):
        lb = base_lb + rng.choice([-10, -5, 0, 5, 10]) if base_lb else 0
        for k in range(4):
            warm = k == 0 and base_lb > 0
            out.append(
                StrengthSet(
                    session_start=w.start,
                    order=len(out) + 1,
                    exercise=name,
                    reps=0 if hold else rng.randint(6, 10),
                    weight_kg=(lb * 0.5 if warm else lb) * KG_PER_LB,
                    multiplier=mult,
                    duration_s=float(hold),
                    is_warmup=warm,
                )
            )
    return out


def build(days: int, seed: int, end: date) -> Dataset:
    rng = random.Random(seed)
    ds = Dataset()
    for i in range(days):
        day = end - timedelta(days=days - 1 - i)
        ds.daily.append(
            DailyMetric(
                day=day,
                steps=int(_clamp(rng.gauss(8500, 2500), 1500, 20000)),
                resting_hr=round(58 + 3 * math.sin(i / 20) + rng.gauss(0, 1.2), 1),
                hrv_ms=round(max(15.0, rng.gauss(48, 8)), 1),
            )
        )
        ds.sleep.append(SleepNight(day, round(_clamp(rng.gauss(7.2, 0.8), 4.5, 9.5) * 60) * 60.0))
        wd = day.weekday()
        if wd in (1, 3, 5) and rng.random() < 0.85:
            ds.workouts.append(_workout(rng, day, "running"))
        if wd == 0 and rng.random() < 0.7:
            lift = _workout(rng, day, "strength_training")
            ds.workouts.append(lift)
            ds.sets += _strength_sets(rng, lift)
        if wd == 6 and rng.random() < 0.5:
            ds.workouts.append(_workout(rng, day, "walking"))
    return ds


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %z")


def _apple_type(sport: str) -> str:
    return _APPLE_TYPE.get(sport) or "".join(p.title() for p in sport.split("_"))


def _record(kind: str, source: str, unit: str | None, start: datetime, end: datetime, value) -> str:
    u = f' unit="{unit}"' if unit else ""
    return (
        f'<Record type="{kind}" sourceName="{source}"{u} creationDate="{_fmt(end)}" '
        f'startDate="{_fmt(start)}" endDate="{_fmt(end)}" value="{value}"/>'
    )


def _sleep_lines(night: SleepNight) -> list[str]:
    start = _at(night.night, 23)
    stages = [("AsleepCore", 0.55), ("AsleepDeep", 0.20), ("AsleepREM", 0.25)]
    lines, cursor = [], start
    for name, share in stages:
        nxt = cursor + timedelta(seconds=night.asleep_s * share)
        lines.append(
            _record("HKCategoryTypeIdentifierSleepAnalysis", "Apple Watch", None, cursor, nxt, f"{_S}{name}")
        )
        cursor = nxt
    end = start + timedelta(seconds=night.asleep_s)
    ph = "iPhone"
    kind = "HKCategoryTypeIdentifierSleepAnalysis"
    lines += [
        _record(kind, ph, None, start - timedelta(minutes=15), end + timedelta(minutes=10), f"{_S}InBed"),
        _record(kind, ph, None, start + timedelta(minutes=10), end - timedelta(minutes=10), f"{_S}Asleep"),
        _record(kind, "Apple Watch", None, end, end + timedelta(minutes=10), f"{_S}Awake"),
    ]
    return lines


def write_apple_export(ds: Dataset, path: str | Path, seed: int = 0) -> None:
    rng = random.Random(seed)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE HealthData [",
        "<!ELEMENT HealthData (ExportDate,Me,(Record|Workout)*)>",
        "]>",
        '<HealthData locale="en_US">',
        f'<ExportDate value="{_fmt(_at(max(d.day for d in ds.daily), 23))}"/>' if ds.daily else "",
        '<Me HKCharacteristicTypeIdentifierDateOfBirth="1990-01-01"/>',
    ]
    for d in ds.daily:
        a, b = _at(d.day, 0), _at(d.day, 23, 59)
        half = (d.steps or 0) // 2
        lines.append(_record(f"{_Q}StepCount", "Apple Watch", "count", a, _at(d.day, 12), half))
        lines.append(
            _record(f"{_Q}StepCount", "Apple Watch", "count", _at(d.day, 12), b, (d.steps or 0) - half)
        )
        lines.append(_record(f"{_Q}StepCount", "iPhone", "count", a, b, round((d.steps or 0) * 0.6)))
        lines.append(
            _record(
                f"{_Q}RestingHeartRate",
                "Apple Watch",
                "count/min",
                _at(d.day, 7),
                _at(d.day, 7),
                d.resting_hr,
            )
        )
        lines.append(
            _record(
                f"{_Q}HeartRateVariabilitySDNN", "Apple Watch", "ms", _at(d.day, 3), _at(d.day, 3), d.hrv_ms
            )
        )
    for night in ds.sleep:
        lines += _sleep_lines(night)
    for w in ds.workouts:
        for minute in range(0, int(w.duration_s // 60)):
            t = w.start + timedelta(minutes=minute)
            bpm = round(_clamp(rng.gauss(w.avg_hr or 120, 4), 60, 200))
            lines.append(_record(f"{_Q}HeartRate", "Apple Watch", "count/min", t, t, bpm))
    for w in ds.workouts:
        lines.append(
            f'<Workout workoutActivityType="{_W}{_apple_type(w.sport)}" '
            f'duration="{w.duration_s / 60!r}" durationUnit="min" sourceName="{w.app or WATCH}" '
            f'creationDate="{_fmt(w.end)}" startDate="{_fmt(w.start)}" endDate="{_fmt(w.end)}">'
        )
        lines.append('<MetadataEntry key="HKIndoorWorkout" value="0"/>')
        if w.distance_m is not None:
            kind = "DistanceCycling" if w.sport == "cycling" else "DistanceWalkingRunning"
            lines.append(
                f'<WorkoutStatistics type="{_Q}{kind}" startDate="{_fmt(w.start)}" endDate="{_fmt(w.end)}" '
                f'sum="{w.distance_m / 1000:.4f}" unit="km"/>'
            )
        if w.energy_kcal is not None:
            lines.append(
                f'<WorkoutStatistics type="{_Q}ActiveEnergyBurned" startDate="{_fmt(w.start)}" '
                f'endDate="{_fmt(w.end)}" sum="{w.energy_kcal}" unit="Cal"/>'
            )
        if w.avg_hr is not None:
            lines.append(
                f'<WorkoutStatistics type="{_Q}HeartRate" startDate="{_fmt(w.start)}" '
                f'endDate="{_fmt(w.end)}" average="{w.avg_hr}" minimum="90" '
                f'maximum="{w.max_hr}" unit="count/min"/>'
            )
        lines.append("</Workout>")
    lines.append("</HealthData>")
    Path(path).write_text("\n".join(line for line in lines if line) + "\n", encoding="utf-8")


_FITBOD_HEADER = (
    "Date,Exercise,Reps,Weight(kg),Duration(s),Distance(m),Incline,Resistance,isWarmup,Note,multiplier"
)


def write_fitbod_csv(ds: Dataset, path: str | Path, drop_every: int = 0) -> None:
    """Render ``ds.sets`` (and a cardio copy of each run) the way Fitbod exports them.

    Mirrors the real file: CRLF, newest session first, UTC dates, leading spaces on Reps and multiplier,
    pounds converted with a slightly wrong factor, identical rows for identical sets, stray blank lines.
    ``drop_every=n`` leaves out every nth strength session so merge tests have unmatched Apple workouts.
    """
    sessions: dict[datetime, list[str]] = {}
    for s in ds.sets:
        rows = sessions.setdefault(s.session_start, [])
        rows.append(
            ",".join(
                [
                    s.session_start.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S +0000"),
                    s.exercise,
                    f" {s.reps}",
                    repr(
                        round(s.weight_kg / KG_PER_LB * 0.45359291, 5)
                    ),  # the factor Fitbod's file really uses
                    repr(s.duration_s),
                    "0.0",
                    "0.0",
                    "0.0",
                    "true" if s.is_warmup else "false",
                    "",
                    f" {s.multiplier}",
                ]
            )
        )
    if drop_every:
        for i, key in enumerate(sorted(sessions)):
            if i % drop_every == drop_every - 1:
                del sessions[key]
    for w in ds.workouts:
        if w.sport == "running":
            sessions[w.start] = [
                ",".join(
                    [
                        w.start.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S +0000"),
                        "Running",
                        " 0",
                        "0.0",
                        repr(float(w.duration_s)),
                        repr(w.distance_m or 0.0),
                        "0.0",
                        "0.0",
                        "false",
                        "",
                        " 0.0",
                    ]
                )
            ]
    lines = [_FITBOD_HEADER]
    for n, key in enumerate(sorted(sessions, reverse=True)):
        lines += sessions[key]
        if n % 7 == 3:
            lines.append("")  # the real file has stray blank lines between workouts
    Path(path).write_bytes(("\r\n".join(lines) + "\r\n").encode())
