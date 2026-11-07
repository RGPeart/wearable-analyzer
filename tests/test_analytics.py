from datetime import UTC, date, datetime, time, timedelta

import pytest

from wearables import analytics
from wearables.model import DailyMetric, Dataset, Workout


def workout(day, minutes=60, avg_hr=150.0, max_hr=180.0, km=None):
    # 15:00 UTC is 10:00/11:00 in New York, so the New York day equals ``day``.
    start = datetime.combine(day, time(15), tzinfo=UTC)
    return Workout(
        "test", "running", start, start + timedelta(minutes=minutes), minutes * 60,
        km * 1000 if km else None, None, avg_hr, max_hr,
    )  # fmt: skip


def test_week_start_is_monday():
    assert analytics.week_start(date(2026, 11, 7)) == date(2026, 11, 2)  # Saturday -> Monday
    assert analytics.week_start(date(2026, 11, 2)) == date(2026, 11, 2)


def test_weekly_summary_fills_empty_weeks():
    ws = [
        workout(date(2026, 10, 5), 60, km=10),
        workout(date(2026, 10, 7), 30, km=5),
        workout(date(2026, 10, 20)),
    ]
    weeks = analytics.weekly_summary(ws)
    assert [w.week_start for w in weeks] == [date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19)]
    assert (weeks[0].workouts, weeks[0].hours, weeks[0].distance_km) == (2, 1.5, 15)
    assert weeks[1].workouts == 0
    assert analytics.weekly_summary([]) == []


def test_trend_compares_recent_to_prior_window():
    as_of = date(2026, 10, 31)
    series = [(as_of - timedelta(days=i), 60.0 if i < 30 else 56.0) for i in range(60)]
    t = analytics.trend(series, as_of)
    assert (t.recent, t.prior, t.delta) == (60.0, 56.0, 4.0)
    assert analytics.trend(series[:30], as_of) is None  # no prior window


def test_session_load_scales_with_heart_rate_reserve():
    full = analytics.session_load(workout(date(2026, 1, 1), 60, avg_hr=180), rest_hr=60, max_hr=180)
    half = analytics.session_load(workout(date(2026, 1, 1), 60, avg_hr=120), rest_hr=60, max_hr=180)
    over = analytics.session_load(workout(date(2026, 1, 1), 60, avg_hr=200), rest_hr=60, max_hr=180)
    assert (full, half, over) == (60, 30, 60)  # reserve is clamped to 1
    assert analytics.session_load(workout(date(2026, 1, 1), avg_hr=None), 60, 180) is None
    assert analytics.session_load(workout(date(2026, 1, 1)), rest_hr=180, max_hr=180) is None


def test_load_summary_acute_vs_chronic():
    as_of = date(2026, 10, 31)
    # One 60-min session at 50% reserve (load 30) every 7th day for 28 days: 4 sessions.
    ds = Dataset(
        workouts=[workout(as_of - timedelta(days=i), 60, avg_hr=120) for i in (0, 7, 14, 21)]
        + [workout(as_of, avg_hr=None)],
        daily=[DailyMetric(as_of, resting_hr=60.0)],
    )
    ls = analytics.load_summary(ds, max_hr=180)
    assert ls.sessions_counted == 4 and ls.sessions_skipped == 1
    assert ls.acute == pytest.approx(30 / 7)
    assert ls.chronic == pytest.approx(120 / 28)
    assert ls.ratio == pytest.approx(1.0)
    assert ls.as_of == as_of


def test_load_summary_needs_heart_rates():
    with pytest.raises(ValueError, match="--rest-hr"):
        analytics.load_summary(Dataset(workouts=[workout(date(2026, 1, 1), avg_hr=None, max_hr=None)]))


def test_summarize_on_synthetic_data(export_xml, truth):
    from wearables.adapters import load

    s = analytics.summarize(load(export_xml))
    assert s["workouts"] == len(truth.workouts)
    assert s["as_of"] == date(2026, 10, 31)
    assert {r["sport"] for r in s["by_sport"]} >= {"running"}
    assert 6 < s["sleep_hours_avg_30d"] < 8.5
    assert s["resting_hr"].recent == pytest.approx(60, abs=6)


def test_summarize_empty_dataset():
    s = analytics.summarize(Dataset())
    assert s["workouts"] == 0 and s["as_of"] is None and s["resting_hr"] is None
