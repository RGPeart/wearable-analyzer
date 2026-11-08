"""Analytics over a :class:`~wearables.model.Dataset`. Pure functions, no I/O.

Training load here is a rough heart-rate-reserve estimate for comparing weeks
with each other. It is not medical or coaching advice.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import correlation as pearson
from statistics import fmean, median, variance

from wearables.model import Dataset, StrengthSet, Workout
from wearables.tz import local_day


@dataclass(frozen=True)
class WeekSummary:
    week_start: date  # Monday
    workouts: int
    hours: float
    distance_km: float
    energy_kcal: float


@dataclass(frozen=True)
class Trend:
    recent: float
    prior: float
    delta: float


@dataclass(frozen=True)
class LoadSummary:
    rest_hr: float
    max_hr: float
    as_of: date
    acute: float  # mean daily load, last 7 days
    chronic: float  # mean daily load, last 28 days
    ratio: float | None  # acute / chronic
    sessions_counted: int
    sessions_skipped: int  # no heart rate data


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_summary(workouts: Iterable[Workout]) -> list[WeekSummary]:
    """One row per ISO week from the first to the last workout, including empty weeks."""
    buckets: dict[date, list[Workout]] = defaultdict(list)
    for w in workouts:
        buckets[week_start(local_day(w.start))].append(w)
    if not buckets:
        return []
    out = []
    week, last = min(buckets), max(buckets)
    while week <= last:
        ws = buckets.get(week, [])
        out.append(
            WeekSummary(
                week_start=week,
                workouts=len(ws),
                hours=sum(w.duration_s for w in ws) / 3600,
                distance_km=sum(w.distance_m or 0 for w in ws) / 1000,
                energy_kcal=sum(w.energy_kcal or 0 for w in ws),
            )
        )
        week += timedelta(days=7)
    return out


def _window(series: Iterable[tuple[date, float]], as_of: date, days: int, back: int = 0) -> list[float]:
    """Values with ``as_of - back - days < day <= as_of - back``."""
    hi = as_of - timedelta(days=back)
    lo = hi - timedelta(days=days)
    return [v for d, v in series if lo < d <= hi]


def trend(series: Iterable[tuple[date, float]], as_of: date, days: int = 30) -> Trend | None:
    """Mean of the last ``days`` days against the ``days`` before them."""
    series = list(series)
    recent, prior = _window(series, as_of, days), _window(series, as_of, days, back=days)
    if not recent or not prior:
        return None
    r, p = fmean(recent), fmean(prior)
    return Trend(recent=r, prior=p, delta=r - p)


def last_day(ds: Dataset) -> date | None:
    days = [local_day(w.start) for w in ds.workouts] + [d.day for d in ds.daily] + [s.night for s in ds.sleep]
    return max(days) if days else None


def session_load(w: Workout, rest_hr: float, max_hr: float) -> float | None:
    """Minutes weighted by heart-rate reserve used (0..1); None without heart rate data."""
    if w.avg_hr is None or max_hr <= rest_hr:
        return None
    reserve = min(max((w.avg_hr - rest_hr) / (max_hr - rest_hr), 0.0), 1.0)
    return w.duration_s / 60 * reserve


def default_rest_hr(ds: Dataset) -> float | None:
    values = [d.resting_hr for d in ds.daily if d.resting_hr is not None]
    return median(values) if values else None


def default_max_hr(ds: Dataset) -> float | None:
    values = [w.max_hr for w in ds.workouts if w.max_hr is not None]
    return max(values) if values else None


def _resolve_hr(ds: Dataset, rest_hr: float | None, max_hr: float | None) -> tuple[float, float]:
    rest_hr = rest_hr if rest_hr is not None else default_rest_hr(ds)
    max_hr = max_hr if max_hr is not None else default_max_hr(ds)
    if rest_hr is None or max_hr is None:
        raise ValueError("resting and max heart rate are unknown: pass --rest-hr and --max-hr")
    return rest_hr, max_hr


def _daily_loads(ds: Dataset, rest_hr: float, max_hr: float) -> tuple[dict[date, float], int, int]:
    """Load per local day, plus how many workouts were counted and how many had no heart rate."""
    daily: dict[date, float] = defaultdict(float)
    counted = skipped = 0
    for w in ds.workouts:
        load = session_load(w, rest_hr, max_hr)
        if load is None:
            skipped += 1
            continue
        counted += 1
        daily[local_day(w.start)] += load
    return daily, counted, skipped


@dataclass(frozen=True)
class LoadPoint:
    day: date
    acute: float  # mean daily load, 7 days ending on ``day``
    chronic: float  # mean daily load, 28 days ending on ``day``


@dataclass(frozen=True)
class LoadHistory:
    rest_hr: float
    max_hr: float
    points: list[LoadPoint]
    sessions_counted: int
    sessions_skipped: int


def load_history(ds: Dataset, rest_hr: float | None = None, max_hr: float | None = None) -> LoadHistory:
    """Acute and chronic load for every day from the first loaded workout to the last day of data."""
    rest_hr, max_hr = _resolve_hr(ds, rest_hr, max_hr)
    end = last_day(ds)
    if end is None:
        raise ValueError("dataset is empty")
    daily, counted, skipped = _daily_loads(ds, rest_hr, max_hr)
    points: list[LoadPoint] = []
    if daily:
        day = min(daily)
        while day <= end:
            acute = sum(daily.get(day - timedelta(days=i), 0.0) for i in range(7)) / 7
            chronic = sum(daily.get(day - timedelta(days=i), 0.0) for i in range(28)) / 28
            points.append(LoadPoint(day, acute, chronic))
            day += timedelta(days=1)
    return LoadHistory(rest_hr, max_hr, points, counted, skipped)


def rolling_mean(series: Iterable[tuple[date, float]], window: int) -> list[tuple[date, float]]:
    """Mean of the values in the ``window`` calendar days ending on each day that has a value."""
    by_day = dict(series)
    return [
        (d, fmean([by_day[x] for i in range(window) if (x := d - timedelta(days=i)) in by_day]))
        for d in sorted(by_day)
    ]


def load_summary(
    ds: Dataset, rest_hr: float | None = None, max_hr: float | None = None, as_of: date | None = None
) -> LoadSummary:
    rest_hr, max_hr = _resolve_hr(ds, rest_hr, max_hr)
    as_of = as_of or last_day(ds)
    if as_of is None:
        raise ValueError("dataset is empty")

    daily, counted, skipped = _daily_loads(ds, rest_hr, max_hr)
    series = list(daily.items())
    acute = sum(_window(series, as_of, 7)) / 7
    chronic = sum(_window(series, as_of, 28)) / 28
    return LoadSummary(
        rest_hr=rest_hr,
        max_hr=max_hr,
        as_of=as_of,
        acute=acute,
        chronic=chronic,
        ratio=acute / chronic if chronic > 0 else None,
        sessions_counted=counted,
        sessions_skipped=skipped,
    )


def summarize(ds: Dataset, as_of: date | None = None) -> dict:
    as_of = as_of or last_day(ds)
    by_sport: dict[str, list[Workout]] = defaultdict(list)
    for w in ds.workouts:
        by_sport[w.sport].append(w)
    sports = sorted(
        (
            {
                "sport": sport,
                "count": len(ws),
                "hours": sum(w.duration_s for w in ws) / 3600,
                "distance_km": sum(w.distance_m or 0 for w in ws) / 1000,
            }
            for sport, ws in by_sport.items()
        ),
        key=lambda r: (-r["count"], r["sport"]),
    )
    out: dict = {
        "workouts": len(ds.workouts),
        "first_workout": local_day(ds.workouts[0].start) if ds.workouts else None,
        "last_workout": local_day(ds.workouts[-1].start) if ds.workouts else None,
        "as_of": as_of,
        "by_sport": sports,
        "resting_hr": None,
        "hrv_ms": None,
        "sleep_hours_avg_30d": None,
        "steps_avg_30d": None,
        "warnings": list(ds.warnings),
    }
    if as_of is None:
        return out
    out["resting_hr"] = trend(((d.day, d.resting_hr) for d in ds.daily if d.resting_hr is not None), as_of)
    out["hrv_ms"] = trend(((d.day, d.hrv_ms) for d in ds.daily if d.hrv_ms is not None), as_of)
    # Nights known only from time in bed overstate sleep, so they stay out of the average.
    sleep = _window(((s.night, s.asleep_s / 3600) for s in ds.sleep if not s.in_bed_only), as_of, 30)
    steps = _window(((d.day, d.steps) for d in ds.daily if d.steps is not None), as_of, 30)
    out["sleep_hours_avg_30d"] = fmean(sleep) if sleep else None
    out["steps_avg_30d"] = fmean(steps) if steps else None
    return out


@dataclass(frozen=True)
class SleepEffect:
    """Sleep around workout days of one kind, against sleep around days with no workout."""

    group: str  # "any workout" or a sport
    when: str  # "before" (the night ending that morning) or "after" (the night starting that evening)
    nights: int
    mean_h: float
    baseline_nights: int
    baseline_h: float
    diff_h: float  # mean_h - baseline_h
    ci_h: float  # half-width of a ~95% interval on diff_h (Welch, normal approximation)


def _welch_half_width(a: list[float], b: list[float]) -> float:
    return 1.96 * (variance(a) / len(a) + variance(b) / len(b)) ** 0.5


def sleep_vs_workouts(
    ds: Dataset, min_minutes: float = 20, min_nights: int = 10, max_sports: int = 4
) -> list[SleepEffect]:
    """Does sleep differ the night before or after a workout day?

    Only nights with asleep stages count; time-in-bed nights overstate sleep. A night is keyed by the
    evening it starts (see :func:`wearables.tz.local_night`), so the sleep before workout day W is night
    W-1 and the sleep after it is night W. The comparison is observational: an early alarm before a
    morning session would also shorten the night before, and nothing here shows cause. The baseline is
    nights with no workout on the evening before or the morning after. A night can sit in both the
    "before" and "after" groups when workouts fall on consecutive days.
    """
    asleep = {s.night: s.asleep_s / 3600 for s in ds.sleep if not s.in_bed_only}
    by_sport: dict[str, set[date]] = defaultdict(set)
    for w in ds.workouts:
        if w.duration_s >= min_minutes * 60:
            by_sport[w.sport].add(local_day(w.start))
    any_days = set().union(*by_sport.values()) if by_sport else set()
    sports = sorted(by_sport, key=lambda s: (-len(by_sport[s]), s))[:max_sports]
    groups = [("any workout", any_days), *((s, by_sport[s]) for s in sports)]

    # Baseline: nights with no workout on either side (the evening before or the morning after). Comparing
    # against every non-workout day would fold the nights before workouts into the baseline for "after".
    baseline = [h for n, h in asleep.items() if n not in any_days and n + timedelta(days=1) not in any_days]
    out = []
    for when, offset in (("before", -1), ("after", 0)):
        for name, days in groups:
            hit = [asleep[d + timedelta(days=offset)] for d in days if d + timedelta(days=offset) in asleep]
            if len(hit) < max(min_nights, 2) or len(baseline) < max(min_nights, 2):
                continue
            out.append(
                SleepEffect(
                    name,
                    when,
                    len(hit),
                    fmean(hit),
                    len(baseline),
                    fmean(baseline),
                    fmean(hit) - fmean(baseline),
                    _welch_half_width(hit, baseline),
                )  # fmt: skip
            )
    return out


@dataclass(frozen=True)
class SleepRecovery:
    night: date
    sleep_h: float
    resting_hr: float | None  # measured on the morning after the night
    hrv_ms: float | None


def sleep_vs_recovery(ds: Dataset) -> list[SleepRecovery]:
    """Each asleep-stage night joined to the next day's resting heart rate and HRV, where recorded."""
    daily = {d.day: d for d in ds.daily}
    out = []
    for s in sorted(ds.sleep, key=lambda s: s.night):
        d = daily.get(s.night + timedelta(days=1))
        if s.in_bed_only or d is None or (d.resting_hr is None and d.hrv_ms is None):
            continue
        out.append(SleepRecovery(s.night, s.asleep_s / 3600, d.resting_hr, d.hrv_ms))
    return out


def correlation(pairs: Iterable[tuple[float, float]], min_pairs: int = 10) -> float | None:
    """Pearson r, or None with too few pairs or no spread in either series."""
    pairs = list(pairs)
    if len(pairs) < min_pairs:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return pearson(xs, ys)


@dataclass(frozen=True)
class StrengthWeek:
    week_start: date  # Monday
    sessions: int
    working_sets: int
    reps: int
    volume_kg: float  # reps x weight x multiplier, warm-ups excluded


@dataclass(frozen=True)
class ExerciseTotal:
    exercise: str
    working_sets: int
    volume_kg: float
    best_weight_kg: float


def weekly_strength(sets: Iterable[StrengthSet]) -> list[StrengthWeek]:
    """One row per week from the first to the last session, including empty weeks. Warm-ups are excluded."""
    sessions: dict[date, set] = defaultdict(set)
    totals: dict[date, list[float]] = defaultdict(lambda: [0, 0, 0.0])  # sets, reps, volume
    for s in sets:
        wk = week_start(local_day(s.session_start))
        sessions[wk].add(s.session_start)
        if s.is_warmup:
            continue
        t = totals[wk]
        t[0] += 1
        t[1] += s.reps
        t[2] += s.volume_kg
    if not sessions:
        return []
    out, week, last = [], min(sessions), max(sessions)
    while week <= last:
        n_sets, reps, volume = totals.get(week, [0, 0, 0.0])
        out.append(StrengthWeek(week, len(sessions.get(week, ())), int(n_sets), int(reps), volume))
        week += timedelta(days=7)
    return out


def top_exercises(
    sets: Iterable[StrengthSet], since: date | None = None, limit: int = 10
) -> list[ExerciseTotal]:
    """Working-set totals per exercise by volume, optionally only sessions on or after ``since``."""
    acc: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for s in sets:
        if s.is_warmup or (since and local_day(s.session_start) < since):
            continue
        a = acc[s.exercise]
        a[0] += 1
        a[1] += s.volume_kg
        a[2] = max(a[2], s.weight_kg)
    ranked = sorted(acc.items(), key=lambda kv: (-kv[1][1], kv[0]))[:limit]
    return [ExerciseTotal(name, int(a[0]), a[1], a[2]) for name, a in ranked]
