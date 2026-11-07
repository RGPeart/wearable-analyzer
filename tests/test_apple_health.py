import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from wearables.adapters import apple_health, load

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE HealthData [\n<!ELEMENT HealthData ANY>\n]>\n<HealthData>\n'
FOOTER = "</HealthData>\n"


def write(tmp_path, body, name="export.xml"):
    path = tmp_path / name
    path.write_text(HEADER + body + FOOTER, encoding="utf-8")
    return path


def test_parse_date_keeps_offset():
    dt = apple_health.parse_date("2024-03-01 07:12:30 -0500")
    assert dt == datetime(2024, 3, 1, 7, 12, 30, tzinfo=timezone(timedelta(hours=-5)))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HKWorkoutActivityTypeRunning", "running"),
        ("HKWorkoutActivityTypeHighIntensityIntervalTraining", "high_intensity_interval_training"),
        ("HKWorkoutActivityType", "other"),
    ],
)
def test_sport_name(raw, expected):
    assert apple_health.sport_name(raw) == expected


def test_roundtrip_matches_ground_truth(export_xml, truth):
    ds = load(export_xml)

    assert len(ds.workouts) == len(truth.workouts)
    for got, want in zip(ds.workouts, truth.workouts, strict=True):
        assert got.sport == want.sport
        assert got.start == want.start
        assert got.duration_s == pytest.approx(want.duration_s)
        assert got.distance_m == (pytest.approx(want.distance_m) if want.distance_m else None)
        assert got.energy_kcal == pytest.approx(want.energy_kcal)
        assert got.avg_hr == pytest.approx(want.avg_hr)
        assert got.max_hr == pytest.approx(want.max_hr)

    assert [(d.day, d.steps) for d in ds.daily] == [(d.day, d.steps) for d in truth.daily]
    for got, want in zip(ds.daily, truth.daily, strict=True):
        assert got.resting_hr == pytest.approx(want.resting_hr)
        assert got.hrv_ms == pytest.approx(want.hrv_ms)

    assert [s.night for s in ds.sleep] == [s.night for s in truth.sleep]
    for got, want in zip(ds.sleep, truth.sleep, strict=True):
        assert got.asleep_s == pytest.approx(want.asleep_s)
    assert ds.warnings == []


def test_zip_input_is_read_in_place(export_xml, tmp_path):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(export_xml, "apple_health_export/export.xml")
        zf.writestr("apple_health_export/export_cda.xml", "<ClinicalDocument/>")
    assert len(load(archive).workouts) == len(load(export_xml).workouts)
    assert not (tmp_path / "apple_health_export").exists()


def test_zip_without_export_xml_is_an_error(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="no export.xml"):
        load(archive)


def test_legacy_workout_totals_and_miles(tmp_path):
    path = write(
        tmp_path,
        '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" '
        'totalDistance="3.1" totalDistanceUnit="mi" totalEnergyBurned="300" totalEnergyBurnedUnit="kcal" '
        'startDate="2023-05-01 18:00:00 -0400" endDate="2023-05-01 18:30:00 -0400"/>',
    )
    (w,) = load(path).workouts
    assert w.distance_m == pytest.approx(3.1 * 1609.344)
    assert w.energy_kcal == 300
    assert w.duration_s == 1800
    assert w.avg_hr is None


def test_kilojoules_convert_to_kcal(tmp_path):
    path = write(
        tmp_path,
        '<Workout workoutActivityType="HKWorkoutActivityTypeCycling" duration="60" durationUnit="min" '
        'startDate="2023-05-01 18:00:00 -0400" endDate="2023-05-01 19:00:00 -0400">'
        '<WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="4184" unit="kJ"/>'
        "</Workout>",
    )
    assert load(path).workouts[0].energy_kcal == pytest.approx(1000)


def _hr(minute, bpm):
    t = f"2023-05-01 18:{minute:02d}:00 -0400"
    return (
        f'<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min" '
        f'startDate="{t}" endDate="{t}" value="{bpm}"/>'
    )


RUN_30MIN = (
    '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" '
    'startDate="2023-05-01 18:00:00 -0400" endDate="2023-05-01 18:30:00 -0400"/>'
)


def test_dense_heart_rate_samples_fill_a_missing_workout_average_even_if_unsorted(tmp_path):
    samples = [_hr(m, 100 + m) for m in range(29, -1, -1)]  # one per minute, newest first
    body = "".join(samples) + _hr(50, 60) + RUN_30MIN  # the last sample is outside the workout
    (w,) = load(write(tmp_path, body)).workouts
    assert w.avg_hr == pytest.approx(114.5)
    assert w.max_hr == 129


def test_sparse_heart_rate_samples_are_not_turned_into_a_workout_average(tmp_path):
    body = _hr(20, 160) + _hr(5, 100) + _hr(10, 140) + RUN_30MIN  # 3 samples in 30 minutes
    (w,) = load(write(tmp_path, body)).workouts
    assert w.avg_hr is None and w.max_hr is None


def test_workout_keeps_its_source_app_and_strength_types_share_a_name(tmp_path):
    def lift(kind, hour):
        return (
            f'<Workout workoutActivityType="HKWorkoutActivityType{kind}" duration="40" durationUnit="min" '
            f'sourceName="Fitbod" startDate="2023-05-01 {hour}:00:00 -0400" endDate="2023-05-01 {hour}:40:00 -0400"/>'
        )

    ws = load(
        write(tmp_path, lift("FunctionalStrengthTraining", 17) + lift("TraditionalStrengthTraining", 19))
    ).workouts
    assert [(w.sport, w.app) for w in ws] == [("strength_training", "Fitbod")] * 2


def test_days_use_new_york_time_not_the_printed_offset(tmp_path):
    # Printed -0500 all year (the exporting phone's zone). 2025-07-01 23:30 -0500 is 00:30 EDT on July 2.
    body = (
        '<Record type="HKQuantityTypeIdentifierRestingHeartRate" startDate="2025-07-01 23:30:00 -0500" value="52"/>'
        '<Record type="HKQuantityTypeIdentifierRestingHeartRate" startDate="2025-12-01 23:30:00 -0500" value="54"/>'
    )
    days = {d.day: d.resting_hr for d in load(write(tmp_path, body)).daily}
    assert days == {date(2025, 7, 2): 52, date(2025, 12, 1): 54}  # winter is EST, so no shift


def test_in_bed_only_nights_fall_back_to_time_in_bed_and_are_flagged(tmp_path):
    def sleep(value, day, start, end):
        return (
            f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="iPhone" '
            f'startDate="2021-03-{day:02d} {start} -0500" endDate="2021-03-{day + 1:02d} {end} -0500" '
            f'value="HKCategoryValueSleepAnalysis{value}"/>'
        )

    body = sleep("InBed", 1, "23:00:00", "07:00:00") + sleep("InBed", 2, "23:00:00", "06:00:00")
    body += sleep("AsleepCore", 2, "23:30:00", "05:30:00")  # night 2 has real asleep data
    ds = load(write(tmp_path, body))
    assert [(s.night, s.asleep_s / 3600, s.in_bed_only) for s in ds.sleep] == [
        (date(2021, 3, 1), 8.0, True),
        (date(2021, 3, 2), 6.0, False),
    ]
    assert any("time in bed" in w for w in ds.warnings)


def test_sleep_union_counts_overlapping_sources_once(tmp_path):
    def sleep(value, start, end, source):
        return (
            f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{source}" '
            f'startDate="2023-05-01 {start} -0400" endDate="2023-05-02 {end} -0400" value="HKCategoryValueSleepAnalysis{value}"/>'
        )

    body = (
        sleep("AsleepCore", "23:00:00", "03:00:00", "Watch")
        + sleep("Asleep", "23:30:00", "04:00:00", "iPhone")  # overlaps; extends the end
        + sleep("InBed", "22:30:00", "07:00:00", "iPhone")  # not sleep
        + sleep("Awake", "04:00:00", "04:30:00", "Watch")
    )  # fmt: skip
    (night,) = load(write(tmp_path, body)).sleep
    assert night.night == date(2023, 5, 1)
    assert night.asleep_s == 5 * 3600  # 23:00 -> 04:00


def test_post_midnight_segment_joins_previous_night(tmp_path):
    body = (
        '<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Watch" '
        'startDate="2023-05-02 01:00:00 -0400" endDate="2023-05-02 02:00:00 -0400" '
        'value="HKCategoryValueSleepAnalysisAsleepDeep"/>'
    )
    (night,) = load(write(tmp_path, body)).sleep
    assert night.night == date(2023, 5, 1)


def test_steps_use_busiest_source_per_day(tmp_path):
    def steps(source, value):
        return (
            f'<Record type="HKQuantityTypeIdentifierStepCount" sourceName="{source}" '
            f'startDate="2023-05-01 09:00:00 -0400" endDate="2023-05-01 10:00:00 -0400" value="{value}"/>'
        )

    body = steps("Watch", 3000) + steps("Watch", 2000) + steps("iPhone", 4000)
    (day,) = load(write(tmp_path, body)).daily
    assert day.steps == 5000


def test_bad_records_are_counted_not_fatal(tmp_path):
    body = (
        '<Record type="HKQuantityTypeIdentifierRestingHeartRate" startDate="garbage" value="55"/>'
        '<Record type="HKQuantityTypeIdentifierRestingHeartRate" startDate="2023-05-01 07:00:00 -0400" value="n/a"/>'
        '<Record type="HKQuantityTypeIdentifierRestingHeartRate" startDate="2023-05-01 07:00:00 -0400" value="55"/>'
    )
    ds = load(write(tmp_path, body))
    assert ds.daily[0].resting_hr == 55
    assert ds.warnings == ["skipped 2 records with unreadable dates or values"]


def test_unrelated_record_types_are_ignored(tmp_path):
    body = (
        '<Record type="HKQuantityTypeIdentifierBodyMass" startDate="2023-05-01 07:00:00 -0400" value="80"/>'
    )
    ds = load(write(tmp_path, body))
    assert ds.daily == [] and ds.workouts == [] and ds.sleep == []
