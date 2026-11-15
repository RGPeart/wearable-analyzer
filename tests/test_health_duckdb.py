import json
import shutil
from datetime import date, datetime

import duckdb
import pytest
from duckdb_schema import SCHEMA, build

from wearables import cli
from wearables.adapters import apple_health, health_duckdb, load
from wearables.merge import reconcile


@pytest.fixture(scope="module")
def export_db(tmp_path_factory, export_xml):
    return build(export_xml, tmp_path_factory.mktemp("db") / "health.duckdb")


def make_db(tmp_path, records=(), workouts=()):
    path = tmp_path / "hand.duckdb"
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    for i, (kind, source, value, start, end) in enumerate(records, 1):
        number = value if isinstance(value, int | float) else None
        con.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)",
            [i, kind, kind, source, None, number, None if number is not None else value, start, end],
        )
    for i, w in enumerate(workouts, 1):
        con.execute("INSERT INTO workouts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [i, *w])
    con.close()
    return path


def test_matches_the_xml_parser(export_xml, export_db):
    want, got = apple_health.load(export_xml), health_duckdb.load(export_db)

    assert len(got.workouts) == len(want.workouts) > 0
    for g, w in zip(got.workouts, want.workouts, strict=True):
        assert (g.source, g.sport, g.start, g.end, g.app) == (w.source, w.sport, w.start, w.end, w.app)
        assert g.duration_s == pytest.approx(w.duration_s)
        for field in ("distance_m", "energy_kcal", "avg_hr", "max_hr"):
            assert getattr(g, field) == pytest.approx(getattr(w, field)), field

    assert [(d.day, d.steps) for d in got.daily] == [(d.day, d.steps) for d in want.daily]
    for g, w in zip(got.daily, want.daily, strict=True):
        assert g.resting_hr == pytest.approx(w.resting_hr)
        assert g.hrv_ms == pytest.approx(w.hrv_ms)

    assert [(s.night, s.in_bed_only) for s in got.sleep] == [(s.night, s.in_bed_only) for s in want.sleep]
    for g, w in zip(got.sleep, want.sleep, strict=True):
        assert g.asleep_s == pytest.approx(w.asleep_s)
    assert got.warnings == want.warnings


def test_database_path_loads_through_the_public_entry_point(export_db, truth):
    ds = load(export_db)
    assert len(ds.workouts) == len(truth.workouts)
    assert ds.sleep and ds.daily


def test_folder_prefers_the_database_and_does_not_double_count(export_xml, export_db, tmp_path, truth):
    shutil.copy(export_xml, tmp_path / "export.xml")
    shutil.copy(export_db, tmp_path / "health.duckdb")
    assert len(load(tmp_path).workouts) == len(truth.workouts)


def test_local_days_follow_new_york_time_across_dst(tmp_path):
    steps = "HKQuantityTypeIdentifierStepCount"
    path = make_db(
        tmp_path,
        records=[
            # UTC instants stored naive. DST began 2026-03-08 and ended 2026-11-01.
            (
                steps,
                "Watch",
                100,
                datetime(2026, 3, 8, 4, 30),
                datetime(2026, 3, 8, 4, 40),
            ),  # 23:30 EST, Mar 7
            (steps, "Watch", 7, datetime(2026, 3, 9, 4, 30), datetime(2026, 3, 9, 4, 40)),  # 00:30 EDT, Mar 9
            (
                steps,
                "Watch",
                50,
                datetime(2026, 11, 1, 4, 30),
                datetime(2026, 11, 1, 4, 40),
            ),  # 00:30 EDT, Nov 1
        ],
    )
    assert {d.day: d.steps for d in health_duckdb.load(path).daily} == {
        date(2026, 3, 7): 100,
        date(2026, 3, 9): 7,
        date(2026, 11, 1): 50,
    }


def test_steps_take_the_busiest_source(tmp_path):
    steps = "HKQuantityTypeIdentifierStepCount"
    t = datetime(2026, 5, 4, 15)
    path = make_db(
        tmp_path,
        records=[(steps, "Watch", 4000, t, t), (steps, "Watch", 1000, t, t), (steps, "iPhone", 3000, t, t)],
    )
    assert [d.steps for d in health_duckdb.load(path).daily] == [5000]


def test_sleep_unions_overlapping_sources_and_flags_in_bed_only(tmp_path):
    kind = "HKCategoryTypeIdentifierSleepAnalysis"
    asleep = "HKCategoryValueSleepAnalysisAsleepCore"
    in_bed = "HKCategoryValueSleepAnalysisInBed"
    path = make_db(
        tmp_path,
        records=[
            # Night of May 4 (03:00-11:00 UTC = 23:00-07:00 EDT): Watch and phone overlap by an hour
            (kind, "Watch", asleep, datetime(2026, 5, 5, 3), datetime(2026, 5, 5, 9)),
            (kind, "iPhone", asleep, datetime(2026, 5, 5, 8), datetime(2026, 5, 5, 11)),
            # In-bed only the next night
            (kind, "iPhone", in_bed, datetime(2026, 5, 6, 3), datetime(2026, 5, 6, 10)),
        ],
    )
    ds = health_duckdb.load(path)
    assert [(s.night, s.asleep_s / 3600, s.in_bed_only) for s in ds.sleep] == [
        (date(2026, 5, 4), 8.0, False),
        (date(2026, 5, 5), 7.0, True),
    ]
    assert "1 sleep nights have no asleep stages" in ds.warnings[0]


def test_heart_rate_from_samples_only_when_dense_enough(tmp_path):
    hr = "HKQuantityTypeIdentifierHeartRate"
    run = (
        "HKWorkoutActivityTypeRunning",
        "Watch",
        datetime(2026, 5, 4, 12),
        datetime(2026, 5, 4, 12, 30),
        30.0,
    )
    dense = [
        (hr, "Watch", 140 + i % 5, datetime(2026, 5, 4, 12, i), datetime(2026, 5, 4, 12, i))
        for i in range(30)
    ]
    sparse = [
        (hr, "Watch", 90, datetime(2026, 5, 5, 8, i * 10), datetime(2026, 5, 5, 8, i * 10)) for i in range(4)
    ]
    workouts = [
        (*run, None, None, None, None, None, None),
        ("HKWorkoutActivityTypeWalking", "Watch", datetime(2026, 5, 5, 8), datetime(2026, 5, 5, 8, 40), 40.0)
        + (None,) * 6,
        # its own statistics win over samples
        ("HKWorkoutActivityTypeCycling", "Watch", datetime(2026, 5, 6, 8), datetime(2026, 5, 6, 8, 30), 30.0)
        + (None, None, None, None, 120.0, 150.0),
    ]
    got = {w.sport: w for w in health_duckdb.load(make_db(tmp_path, dense + sparse, workouts)).workouts}
    assert got["running"].avg_hr == pytest.approx(142.0, abs=0.1) and got["running"].max_hr == 144
    assert got["walking"].avg_hr is None  # 4 samples in 40 minutes is background noise
    assert (got["cycling"].avg_hr, got["cycling"].max_hr) == (120.0, 150.0)


def test_units_are_converted(tmp_path):
    workouts = [
        (
            "HKWorkoutActivityTypeRunning",
            "Watch",
            datetime(2026, 5, 4, 12),
            datetime(2026, 5, 4, 12, 30),
            30.0,
            2092.0,
            "kJ",
            3.1,
            "mi",
            None,
            None,
        )
    ]
    (w,) = health_duckdb.load(make_db(tmp_path, workouts=workouts)).workouts
    assert w.distance_m == pytest.approx(3.1 * 1609.344)
    assert w.energy_kcal == pytest.approx(500.0)
    assert w.duration_s == 1800.0


def test_a_database_without_the_expected_tables_is_an_error(tmp_path):
    path = tmp_path / "other.duckdb"
    duckdb.connect(str(path)).execute("CREATE TABLE unrelated (x INTEGER)").close()
    with pytest.raises(ValueError, match="missing table"):
        health_duckdb.load(path)


def test_a_file_that_is_not_a_database_is_an_error(tmp_path):
    path = tmp_path / "broken.duckdb"
    path.write_bytes(b"not a database")
    with pytest.raises(ValueError, match="cannot open"):
        health_duckdb.load(path)


def test_the_database_is_opened_read_only(export_db):
    before = export_db.stat().st_mtime_ns
    health_duckdb.load(export_db)
    assert export_db.stat().st_mtime_ns == before


def test_cli_summary_runs_on_a_database(capsys, export_db):
    assert cli.main(["summary", str(export_db), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["workouts"] > 0


def test_reconcile_treats_database_workouts_like_xml_workouts(export_xml, export_db):
    xml_ds, db_ds = apple_health.load(export_xml), health_duckdb.load(export_db)
    assert [w.sport for w in reconcile(db_ds).workouts] == [w.sport for w in reconcile(xml_ds).workouts]
