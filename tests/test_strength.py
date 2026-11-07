import json
from datetime import UTC, date, datetime

import pytest

from wearables import analytics, cli, synth
from wearables.model import LB_PER_KG, StrengthSet

MON = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)  # Monday 11:00 New York
NEXT_MON = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)  # two weeks later, so the week between is empty


def s(start, order, exercise, reps, kg, mult=1.0, warm=False):
    return StrengthSet(start, order, exercise, reps, kg, mult, is_warmup=warm)


SETS = [
    s(MON, 1, "Squat", 5, 40.0, warm=True),  # warm-ups never count
    s(MON, 2, "Squat", 5, 80.0),
    s(MON, 3, "Squat", 5, 80.0),
    s(MON, 4, "Curl", 10, 12.0, mult=2.0),
    s(NEXT_MON, 1, "Squat", 3, 100.0),
]


def test_weekly_strength_counts_working_sets_and_fills_empty_weeks():
    weeks = analytics.weekly_strength(SETS)
    assert [w.week_start for w in weeks] == [date(2026, 10, 5), date(2026, 10, 12), date(2026, 10, 19)]
    first, empty, last = weeks
    assert (first.sessions, first.working_sets, first.reps) == (1, 3, 20)
    assert first.volume_kg == pytest.approx(5 * 80 * 2 + 10 * 12 * 2)
    assert (empty.sessions, empty.working_sets, empty.volume_kg) == (0, 0, 0)
    assert (last.sessions, last.volume_kg) == (1, 300.0)
    assert analytics.weekly_strength([]) == []


def test_a_late_evening_lift_lands_on_the_new_york_day_it_happened():
    sunday_night = datetime(2026, 10, 12, 1, 30, tzinfo=UTC)  # 21:30 Sunday EDT, still the week of Oct 5
    (week,) = analytics.weekly_strength([s(sunday_night, 1, "Squat", 5, 80.0)])
    assert week.week_start == date(2026, 10, 5)


def test_top_exercises_ranks_by_volume_and_respects_since():
    top = analytics.top_exercises(SETS)
    assert [(t.exercise, t.working_sets) for t in top] == [("Squat", 3), ("Curl", 1)]
    assert top[0].best_weight_kg == 100.0
    recent = analytics.top_exercises(SETS, since=date(2026, 10, 19))
    assert [t.exercise for t in recent] == ["Squat"] and recent[0].working_sets == 1
    assert len(analytics.top_exercises(SETS, limit=1)) == 1


def test_set_volume_and_pounds():
    lift = s(MON, 1, "Curl", 10, 11.33981, mult=2.0)
    assert lift.volume_kg == pytest.approx(226.7962)
    assert lift.weight_lb == pytest.approx(25.0, abs=1e-3)
    assert 1 / LB_PER_KG == pytest.approx(0.45359237)


@pytest.fixture
def folder(tmp_path):
    truth = synth.build(days=70, seed=2, end=date(2026, 10, 31))
    synth.write_apple_export(truth, tmp_path / "export.xml", seed=2)
    synth.write_fitbod_csv(truth, tmp_path / "WorkoutExport.csv")
    return tmp_path


def test_strength_command_table_and_json(capsys, folder):
    assert cli.main(["strength", str(folder), "--weeks", "4", "--top", "3"]) == 0
    out = capsys.readouterr().out
    assert "volume lb" in out and "Top exercises" in out and "multiplier" in out
    assert cli.main(["strength", str(folder), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["weeks"] and len(data["top_exercises"]) <= 8


def test_strength_command_without_sets_explains_itself(capsys, export_xml):
    assert cli.main(["strength", str(export_xml)]) == 1
    assert "Fitbod" in capsys.readouterr().err


def test_merge_notes_go_to_stderr_and_summary_counts_each_lift_once(capsys, folder):
    assert cli.main(["summary", str(folder), "--json"]) == 0
    captured = capsys.readouterr()
    assert "strength sessions matched" in captured.err
    sports = {r["sport"]: r["count"] for r in json.loads(captured.out)["by_sport"]}
    assert "functional_strength_training" not in sports and sports["strength_training"] > 0


def test_synth_command_can_write_the_fitbod_csv_too(capsys, tmp_path):
    xml, csv_path = tmp_path / "demo.xml", tmp_path / "fitbod.csv"
    code = cli.main(["synth", str(xml), "--fitbod", str(csv_path), "--days", "28", "--end", "2026-10-31"])
    assert code == 0 and "sets" in capsys.readouterr().out
    assert cli.main(["strength", str(csv_path)]) == 0
