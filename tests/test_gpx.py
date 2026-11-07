from datetime import UTC, datetime

import pytest

from wearables.adapters import load

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><type>running</type><trkseg>
    <trkpt lat="40.0000" lon="-74.0000"><ele>10</ele><time>2024-05-01T12:00:00Z</time></trkpt>
    <trkpt lat="40.0050" lon="-74.0000"><ele>25</ele><time>2024-05-01T12:03:00Z</time></trkpt>
    <trkpt lat="40.0100" lon="-74.0000"><ele>15</ele><time>2024-05-01T12:06:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""

UNTIMED = (
    GPX.replace("<time>2024-05-01T12:00:00Z</time>", "")
    .replace("<time>2024-05-01T12:03:00Z</time>", "")
    .replace("<time>2024-05-01T12:06:00Z</time>", "")
)


def test_track_becomes_workout(tmp_path):
    path = tmp_path / "run.gpx"
    path.write_text(GPX)
    (w,) = load(path).workouts
    assert w.sport == "running"
    assert w.start == datetime(2024, 5, 1, 12, 0, tzinfo=UTC)
    assert w.duration_s == 360
    assert w.distance_m == pytest.approx(1112, rel=0.01)  # 0.01 degrees of latitude
    assert 0 < w.elevation_gain_m <= 15  # gpxpy smooths elevation, so less than the raw 15 m climb


def test_folder_of_gpx_and_bad_files(tmp_path):
    (tmp_path / "a.gpx").write_text(GPX)
    (tmp_path / "b.gpx").write_text(UNTIMED)
    (tmp_path / "c.gpx").write_text("not xml at all")
    ds = load(tmp_path)
    assert len(ds.workouts) == 1
    assert any("b.gpx" in w and "no timestamps" in w for w in ds.warnings)
    assert any("c.gpx" in w and "not valid GPX" in w for w in ds.warnings)


def test_folder_with_apple_export_prefers_it(export_xml):
    assert load(export_xml.parent).workouts[0].source == "apple_health"


def test_unsupported_and_missing_inputs(tmp_path):
    (tmp_path / "x.csv").write_text("a,b")
    with pytest.raises(ValueError, match="unsupported"):
        load(tmp_path / "x.csv")
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.gpx")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no export.xml"):
        load(empty)
