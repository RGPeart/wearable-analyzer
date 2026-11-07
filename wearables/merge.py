"""Reconcile workouts that more than one app recorded.

Adapters stay independent and only produce what their source says. This step then:

1. joins each Fitbod strength session to the Apple workout Fitbod wrote to Health at the same start
   instant (the real export matches to the second), taking Apple's duration, energy and heart rate;
2. drops Fitbod's one-row cardio entries when the Apple workout they were copied from is present;
3. collapses overlapping same-sport workouts from different apps (e.g. a gym app and a Watch both
   logging one bike ride), keeping the one with heart-rate data.
"""

import dataclasses
from bisect import bisect_left

from wearables.model import Dataset, Workout

APPLE = "apple_health"
FITBOD = "fitbod"
MATCH_WINDOW_S = 120  # strength sessions: the observed offset is 0 s, so this is generous
IMPORT_WINDOW_S = 300  # imported cardio rows
OVERLAP_SHARE = 0.8  # of the shorter workout


def _nearest_distance(starts: list[float], t: float) -> float | None:
    """Seconds from ``t`` to the closest value in the sorted ``starts``."""
    if not starts:
        return None
    i = bisect_left(starts, t)
    return min(abs(starts[j] - t) for j in (i - 1, i) if 0 <= j < len(starts))


def _join_strength(workouts: list[Workout]) -> tuple[list[Workout], list[str]]:
    sessions = sorted((w for w in workouts if w.source == FITBOD and not w.imported), key=lambda w: w.start)
    apple = sorted(
        (w for w in workouts if w.source == APPLE and w.sport == "strength_training"), key=lambda w: w.start
    )
    starts = [w.start.timestamp() for w in apple]
    used: set[int] = set()
    merged: dict[int, Workout] = {}
    for fb in sessions:
        t = fb.start.timestamp()
        lo = bisect_left(starts, t - MATCH_WINDOW_S)
        in_window = [j for j in range(lo, len(apple)) if starts[j] <= t + MATCH_WINDOW_S and j not in used]
        if not in_window:
            continue
        # Prefer the workout Fitbod itself wrote, then the closest start.
        j = min(in_window, key=lambda j: (apple[j].app != "Fitbod", abs(starts[j] - t)))
        used.add(j)
        a = apple[j]
        merged[id(fb)] = dataclasses.replace(
            fb,
            source=f"{FITBOD}+{APPLE}",
            end=a.end,
            duration_s=a.duration_s,
            energy_kcal=a.energy_kcal,
            avg_hr=a.avg_hr,
            max_hr=a.max_hr,
        )
    dropped = {id(apple[j]) for j in used}
    out = [merged.get(id(w), w) for w in workouts if id(w) not in dropped]
    notes = (
        [f"fitbod: {len(merged)}/{len(sessions)} strength sessions matched to Apple workouts"]
        if sessions
        else []
    )
    return out, notes


def _drop_imported_cardio(workouts: list[Workout]) -> tuple[list[Workout], list[str]]:
    starts = sorted(w.start.timestamp() for w in workouts if w.source == APPLE)
    kept, dropped = [], 0
    for w in workouts:
        if w.imported and w.source == FITBOD:
            gap = _nearest_distance(starts, w.start.timestamp())
            if gap is not None and gap <= IMPORT_WINDOW_S:
                dropped += 1
                continue
        kept.append(w)
    return kept, (
        [f"fitbod: dropped {dropped} cardio rows already present as Apple workouts"] if dropped else []
    )


def _quality(w: Workout) -> tuple[bool, bool, bool, bool]:
    # A workout that owns lifting sets always wins, so its sets never point at a removed record.
    return (
        FITBOD in w.source,
        w.avg_hr is not None,
        w.energy_kcal is not None,
        "watch" in (w.app or "").lower(),
    )


def _dedupe_overlaps(workouts: list[Workout]) -> tuple[list[Workout], list[str]]:
    """Same sport, different app, mostly overlapping in time: keep the better record."""
    ordered = sorted(workouts, key=lambda w: w.start)
    removed: set[int] = set()
    active: list[Workout] = []
    for w in ordered:
        active = [a for a in active if a.end > w.start]
        for a in active:
            if (
                id(a) in removed
                or a.sport != w.sport
                or a.app == w.app
                or min(a.duration_s, w.duration_s) <= 0
            ):
                continue
            overlap = (min(a.end, w.end) - w.start).total_seconds()
            if overlap >= OVERLAP_SHARE * min(a.duration_s, w.duration_s):
                removed.add(id(min(a, w, key=_quality)))
        if id(w) not in removed:
            active.append(w)
    kept = [w for w in ordered if id(w) not in removed]
    return kept, (
        [f"removed {len(removed)} overlapping duplicate workouts from different apps"] if removed else []
    )


def reconcile(ds: Dataset) -> Dataset:
    """Return a new Dataset with cross-app duplicates resolved. Sets, daily and sleep pass through."""
    workouts = list(ds.workouts)
    notes: list[str] = []
    if any(w.source == FITBOD for w in workouts):
        workouts, n = _join_strength(workouts)
        notes += n
        workouts, n = _drop_imported_cardio(workouts)
        notes += n
    workouts, n = _dedupe_overlaps(workouts)
    notes += n
    return Dataset(workouts, ds.daily, ds.sleep, ds.sets, ds.warnings + notes)
