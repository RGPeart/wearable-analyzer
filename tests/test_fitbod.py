from datetime import UTC, datetime

import pytest

from wearables import synth
from wearables.adapters import fitbod, load

HEADER = "Date,Exercise,Reps,Weight(kg),Duration(s),Distance(m),Incline,Resistance,isWarmup,Note,multiplier"


def csv_file(tmp_path, *rows, name="WorkoutExport.csv"):
    path = tmp_path / name
    path.write_bytes(("\r\n".join([HEADER, *rows]) + "\r\n").encode())
    return path


def row(date, exercise, reps=" 8", kg="61.23497", dur="0.0", dist="0.0", warm="false", mult=" 1.0"):
    return f"{date},{exercise},{reps},{kg},{dur},{dist},0.0,0.0,{warm},,{mult}"


D1 = "2026-11-06 10:53:00 +0000"
D2 = "2026-11-04 11:00:00 +0000"


def test_sets_keep_file_order_duplicates_and_trim_spaces(tmp_path):
    path = csv_file(
        tmp_path,
        row(D1, "Squat", warm="true", kg="30.617485"),
        row(D1, "Squat"),
        row(D1, "Squat"),  # identical working sets must both survive
        "",  # stray blank line inside the file
        row(D1, "Curl", reps=" 12", kg="11.33981", mult=" 2.0"),
    )
    ds = fitbod.load(path)
    assert [(s.order, s.exercise, s.reps, s.is_warmup, s.multiplier) for s in ds.sets] == [
        (1, "Squat", 8, True, 1.0),
        (2, "Squat", 8, False, 1.0),
        (3, "Squat", 8, False, 1.0),
        (4, "Curl", 12, False, 2.0),
    ]
    (w,) = ds.workouts
    assert (w.source, w.sport, w.app, w.duration_s) == ("fitbod", "strength_training", "Fitbod", 0.0)
    assert w.start == datetime(2026, 11, 6, 10, 53, tzinfo=UTC)


def test_weights_snap_to_quarter_pounds_but_true_kilograms_survive():
    assert fitbod.snap_weight_kg(61.23497) == pytest.approx(135 * fitbod.KG_PER_LB)
    assert fitbod.snap_weight_kg(61.235043) == pytest.approx(135 * fitbod.KG_PER_LB)  # slightly-off factor
    assert fitbod.snap_weight_kg(61.23492) == pytest.approx(135 * fitbod.KG_PER_LB)
    assert fitbod.snap_weight_kg(12.0) == 12.0  # a 12 kg kettlebell is 26.455 lb, not a quarter
    assert fitbod.snap_weight_kg(0.0) == 0.0


def test_single_row_with_distance_is_imported_cardio(tmp_path):
    path = csv_file(
        tmp_path,
        row(D1, "Running", reps=" 0", kg="0.0", dur="1800.5", dist="5000.0", mult=" 0.0"),
        row(D2, "Squat"),
    )
    ds = fitbod.load(path)
    cardio = next(w for w in ds.workouts if w.imported)
    assert (cardio.sport, cardio.duration_s, cardio.distance_m) == ("running", 1800.5, 5000.0)
    assert (cardio.end - cardio.start).total_seconds() == pytest.approx(1800.5)
    strength = next(w for w in ds.workouts if not w.imported)
    assert strength.sport == "strength_training" and len(ds.sets) == 1  # a lone set is still a lift


def test_bad_rows_are_counted_not_fatal(tmp_path):
    path = csv_file(tmp_path, row("not a date", "Squat"), row(D1, "Squat"), row(D2, "Curl", reps=" x"))
    ds = fitbod.load(path)
    assert len(ds.sets) == 1
    assert ds.warnings == ["fitbod: skipped 2 rows with unreadable values"]


def test_sniffing_and_dispatch(tmp_path):
    path = csv_file(tmp_path, row(D1, "Squat"))
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n")
    assert fitbod.is_fitbod_csv(path) and not fitbod.is_fitbod_csv(other)
    assert len(load(path).sets) == 1
    with pytest.raises(ValueError, match="only the Fitbod export"):
        load(other)


def test_synthetic_csv_roundtrip(tmp_path):
    from datetime import date

    truth = synth.build(days=56, seed=3, end=date(2026, 10, 31))
    path = tmp_path / "WorkoutExport.csv"
    synth.write_fitbod_csv(truth, path)
    raw = path.read_bytes()
    assert b"\r\n" in raw and b"\r\n\r\n" in raw  # CRLF and blank lines like the real file
    ds = fitbod.load(path)
    assert len(ds.sets) == len(truth.sets)
    key = lambda s: (s.session_start, s.order)  # noqa: E731
    for got, want in zip(sorted(ds.sets, key=key), sorted(truth.sets, key=key), strict=True):
        assert (got.exercise, got.reps, got.multiplier, got.is_warmup) == (
            want.exercise,
            want.reps,
            want.multiplier,
            want.is_warmup,
        )
        assert got.weight_kg == pytest.approx(want.weight_kg)
        assert got.duration_s == want.duration_s
    assert sum(w.imported for w in ds.workouts) == sum(w.sport == "running" for w in truth.workouts)
