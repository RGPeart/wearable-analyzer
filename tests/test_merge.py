from datetime import UTC, date, datetime, timedelta

import pytest

from wearables import analytics, merge, synth
from wearables.adapters import load
from wearables.model import Dataset, StrengthSet, Workout

T0 = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)


def apple(start=T0, sport="strength_training", app="Fitbod", minutes=50, hr=118.0, kcal=300.0):
    return Workout(
        "apple_health", sport, start, start + timedelta(minutes=minutes), minutes * 60,
        None, kcal, hr, hr + 20 if hr else None, app=app,
    )  # fmt: skip


def fitbod_session(start=T0):
    return Workout("fitbod", "strength_training", start, start, 0.0, app="Fitbod")


def cardio_copy(start=T0, sport="running"):
    return Workout(
        "fitbod", sport, start, start + timedelta(minutes=30), 1800.0, 5000.0, app="Fitbod", imported=True
    )


def reconcile(*workouts):
    return merge.reconcile(Dataset(list(workouts)))


def test_strength_session_takes_duration_energy_and_hr_from_the_apple_workout():
    ds = reconcile(fitbod_session(), apple())
    (w,) = ds.workouts
    assert w.source == "fitbod+apple_health" and w.app == "Fitbod"
    assert (w.duration_s, w.energy_kcal, w.avg_hr, w.max_hr) == (3000, 300.0, 118.0, 138.0)
    assert w.end == T0 + timedelta(minutes=50)
    assert "fitbod: 1/1 strength sessions matched to Apple workouts" in ds.warnings


@pytest.mark.parametrize(("offset_s", "matched"), [(0, True), (90, True), (-90, True), (300, False)])
def test_match_window(offset_s, matched):
    ds = reconcile(fitbod_session(), apple(T0 + timedelta(seconds=offset_s)))
    assert len(ds.workouts) == (1 if matched else 2)
    assert any(w.source == "fitbod+apple_health" for w in ds.workouts) == matched


def test_unmatched_session_is_kept_with_unknown_duration():
    ds = reconcile(fitbod_session())
    (w,) = ds.workouts
    assert w.source == "fitbod" and w.duration_s == 0.0


def test_prefers_the_fitbod_written_workout_and_never_matches_one_apple_workout_twice():
    hevy = apple(T0 + timedelta(seconds=10), app="Hevy", hr=99.0)  # closer in time, but not Fitbod's
    ds = reconcile(fitbod_session(), hevy, apple(T0 + timedelta(seconds=50), app="Fitbod"))
    (merged,) = ds.workouts  # the overlapping Hevy duplicate is collapsed into the session that owns the sets
    assert merged.source == "fitbod+apple_health" and merged.avg_hr == 118.0

    two = reconcile(fitbod_session(T0), fitbod_session(T0 + timedelta(seconds=30)), apple())
    assert sorted(w.source for w in two.workouts) == ["fitbod", "fitbod+apple_health"]


def test_strength_workouts_from_other_apps_without_a_csv_are_untouched():
    ds = reconcile(apple(app="Hevy"))
    assert [w.source for w in ds.workouts] == ["apple_health"]


def test_imported_cardio_is_dropped_only_when_the_apple_original_exists():
    kept = reconcile(cardio_copy())
    assert len(kept.workouts) == 1  # nothing to compare with, so it is the only record

    both = reconcile(cardio_copy(), apple(T0 + timedelta(seconds=40), "running", "Apple Watch"))
    assert [w.source for w in both.workouts] == ["apple_health"]
    assert "fitbod: dropped 1 cardio rows already present as Apple workouts" in both.warnings

    far = reconcile(cardio_copy(), apple(T0 + timedelta(minutes=20), "running", "Apple Watch"))
    assert len(far.workouts) == 2  # 20 minutes apart is a different workout


def test_overlapping_same_sport_workouts_from_different_apps_keep_the_one_with_heart_rate():
    gym = apple(sport="cycling", app="Equinox+", hr=None, kcal=None)
    watch = apple(T0 + timedelta(seconds=20), sport="cycling", app="Apple Watch")
    for order in ((gym, watch), (watch, gym)):
        ds = reconcile(*order)
        assert [w.app for w in ds.workouts] == ["Apple Watch"]
        assert "removed 1 overlapping duplicate workouts from different apps" in ds.warnings


def test_overlap_rule_leaves_different_sports_same_app_and_barely_overlapping_alone():
    run_inside_lift = apple(T0 + timedelta(minutes=10), sport="running", app="Apple Watch", minutes=20)
    lift = apple(app="Fitbod")
    assert len(reconcile(lift, run_inside_lift).workouts) == 2  # different sport
    assert len(reconcile(apple(app="A"), apple(app="A")).workouts) == 2  # same app
    late = apple(T0 + timedelta(minutes=45), app="B")  # 5 of 50 minutes overlap
    assert len(reconcile(apple(app="A"), late).workouts) == 2


def test_reconcile_passes_sets_daily_and_sleep_through():
    s = StrengthSet(T0, 1, "Squat", 5, 60.0, 1.0)
    ds = merge.reconcile(Dataset([fitbod_session(), apple()], sets=[s], warnings=["earlier"]))
    assert ds.sets == [s] and ds.warnings[0] == "earlier"


def test_end_to_end_on_a_folder_with_apple_and_fitbod_exports(tmp_path):
    truth = synth.build(days=84, seed=5, end=date(2026, 10, 31))
    synth.write_apple_export(truth, tmp_path / "export.xml", seed=5)
    synth.write_fitbod_csv(truth, tmp_path / "WorkoutExport.csv", drop_every=4)
    ds = load(tmp_path)

    lifts_truth = [w for w in truth.workouts if w.sport == "strength_training"]
    in_csv = len(lifts_truth) - len(lifts_truth) // 4
    sessions = {s.session_start for s in ds.sets}
    assert len(sessions) == in_csv

    lifts = [w for w in ds.workouts if w.sport == "strength_training"]
    assert len(lifts) == len(lifts_truth)  # every lift once: merged, or the plain Apple record
    merged = [w for w in lifts if w.source == "fitbod+apple_health"]
    assert len(merged) == in_csv
    by_start = {w.start: w for w in lifts_truth}
    for w in merged:
        assert w.duration_s == pytest.approx(by_start[w.start].duration_s)
        assert w.energy_kcal == pytest.approx(by_start[w.start].energy_kcal)
    assert sum(w.imported for w in ds.workouts) == 0  # the cardio copies were dropped
    assert sum(w.sport == "running" for w in ds.workouts) == sum(w.sport == "running" for w in truth.workouts)
    assert any(f"fitbod: {in_csv}/{in_csv} strength sessions matched" in n for n in ds.warnings)

    weeks = analytics.weekly_strength(ds.sets)
    assert sum(w.sessions for w in weeks) == in_csv
    working = [s for s in truth.sets if not s.is_warmup and s.session_start in sessions]
    assert sum(w.working_sets for w in weeks) == len(working)
    assert sum(w.volume_kg for w in weeks) == pytest.approx(sum(s.volume_kg for s in working))
