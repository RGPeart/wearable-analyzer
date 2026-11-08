import json
from datetime import UTC, date, datetime, time, timedelta

import pytest

from wearables import analytics, cli, synth
from wearables.model import DailyMetric, Dataset, SleepNight, Workout

START = date(2026, 1, 1)


def workout(day, minutes=60, sport="running"):
    # 15:00 UTC is 10:00/11:00 in New York, so the New York day equals ``day``.
    start = datetime.combine(day, time(15), tzinfo=UTC)
    return Workout("test", sport, start, start + timedelta(minutes=minutes), minutes * 60)


def patterned(n=60, hours_before=6.0, hours_else=7.0, jitter=0.0):
    """A workout every third day. The night before each workout is shorter; every other night is not."""
    ds = Dataset()
    for i in range(0, n + 1, 3):  # one day past the last night, so that night's morning-after is known
        ds.workouts.append(workout(START + timedelta(days=i)))
    for i in range(n):
        day = START + timedelta(days=i)
        h = hours_before if (i + 1) % 3 == 0 else hours_else
        h += jitter if i % 2 else -jitter
        ds.sleep.append(SleepNight(day, h * 3600))
    return ds


def effect(effects, group, when):
    return next(e for e in effects if e.group == group and e.when == when)


def test_a_short_night_before_workouts_shows_up_and_the_night_after_does_not():
    effects = analytics.sleep_vs_workouts(patterned())
    before, after = effect(effects, "any workout", "before"), effect(effects, "any workout", "after")
    assert before.diff_h == pytest.approx(-1.0) and before.mean_h == pytest.approx(6.0)
    assert after.diff_h == pytest.approx(0.0) and after.baseline_h == pytest.approx(7.0)
    assert before.nights == 20 and before.baseline_nights == 20  # nights flanked by two rest days


def test_baseline_leaves_out_the_nights_around_workouts():
    # Every non-workout night is next to a workout here, so there is no clean baseline to compare with.
    ds = Dataset(workouts=[workout(START + timedelta(days=i)) for i in range(0, 40, 2)])
    ds.sleep = [SleepNight(START + timedelta(days=i), 7 * 3600.0) for i in range(40)]
    assert analytics.sleep_vs_workouts(ds) == []


def test_interval_widens_with_noise_and_is_zero_without():
    clean = effect(analytics.sleep_vs_workouts(patterned()), "any workout", "before")
    noisy = effect(analytics.sleep_vs_workouts(patterned(jitter=0.3)), "any workout", "before")
    assert clean.ci_h == 0.0 and noisy.ci_h > 0.05


def test_sports_are_compared_separately():
    ds = patterned()
    ds.workouts = [
        workout(w.start.date(), sport="strength_training" if i % 2 else "running")
        for i, w in enumerate(ds.workouts)
    ]
    groups = {e.group for e in analytics.sleep_vs_workouts(ds)}
    assert groups == {"any workout", "running", "strength_training"}


def test_short_workouts_and_time_in_bed_nights_do_not_count():
    ds = patterned()
    ds.workouts = [workout(w.start.date(), minutes=10) for w in ds.workouts]  # all under 20 min
    assert analytics.sleep_vs_workouts(ds) == []
    ds = patterned()
    ds.sleep = [SleepNight(s.night, s.asleep_s, in_bed_only=True) for s in ds.sleep]
    assert analytics.sleep_vs_workouts(ds) == []


def test_too_little_data_gives_nothing_instead_of_a_noisy_number():
    assert analytics.sleep_vs_workouts(patterned(n=12)) == []
    assert analytics.sleep_vs_workouts(Dataset()) == []


def test_recovery_joins_each_night_to_the_next_morning():
    ds = Dataset(
        sleep=[
            SleepNight(date(2026, 3, 1), 7 * 3600.0),
            SleepNight(date(2026, 3, 2), 6 * 3600.0),
            SleepNight(date(2026, 3, 3), 9 * 3600.0, in_bed_only=True),
            SleepNight(date(2026, 3, 9), 8 * 3600.0),  # no daily row the next day
        ],
        daily=[
            DailyMetric(date(2026, 3, 2), resting_hr=55.0, hrv_ms=50.0),
            DailyMetric(date(2026, 3, 3), resting_hr=58.0),
            DailyMetric(date(2026, 3, 4), resting_hr=60.0),  # follows the in-bed-only night: skipped
        ],
    )
    rows = analytics.sleep_vs_recovery(ds)
    assert [(r.night, r.sleep_h, r.resting_hr, r.hrv_ms) for r in rows] == [
        (date(2026, 3, 1), 7.0, 55.0, 50.0),
        (date(2026, 3, 2), 6.0, 58.0, None),
    ]


def test_correlation_needs_enough_varied_pairs():
    line = [(float(x), 2.0 * x + 1) for x in range(12)]
    assert analytics.correlation(line) == pytest.approx(1.0)
    assert analytics.correlation([(x, -y) for x, y in line]) == pytest.approx(-1.0)
    assert analytics.correlation(line[:5]) is None
    assert analytics.correlation([(1.0, float(y)) for y in range(12)]) is None  # x never varies


@pytest.fixture
def folder(tmp_path):
    truth = synth.build(days=120, seed=6, end=date(2026, 10, 31))
    synth.write_apple_export(truth, tmp_path / "export.xml", seed=6)
    return tmp_path


def test_sleep_command_table_and_json(capsys, folder):
    assert cli.main(["sleep", str(folder)]) == 0
    out = capsys.readouterr().out
    assert "any workout" in out and "before" in out and "Observational only" in out
    assert cli.main(["sleep", str(folder), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data and {"group", "when", "nights", "diff_h", "ci_h"} <= data[0].keys()


def test_sleep_command_explains_when_there_is_too_little_sleep_data(capsys, tmp_path):
    truth = synth.build(days=6, seed=1, end=date(2026, 10, 31))
    synth.write_apple_export(truth, tmp_path / "export.xml", seed=1)
    assert cli.main(["sleep", str(tmp_path)]) == 1
    assert "not enough nights" in capsys.readouterr().err
