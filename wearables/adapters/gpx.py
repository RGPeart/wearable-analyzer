"""GPX adapter: one workout per timed track (Apple Health workout routes, Strava/Garmin exports)."""

from collections.abc import Iterable
from pathlib import Path

import gpxpy
import gpxpy.gpx

from wearables.model import Dataset, Workout

SOURCE = "gpx"


def load_many(paths: Iterable[Path]) -> Dataset:
    ds = Dataset()
    for path in paths:
        try:
            with open(path, "rb") as fh:
                gpx = gpxpy.parse(fh)
        except gpxpy.gpx.GPXException as exc:
            ds.warnings.append(f"{Path(path).name}: not valid GPX ({exc})")
            continue
        for i, track in enumerate(gpx.tracks, start=1):
            bounds = track.get_time_bounds()
            if bounds.start_time is None or bounds.end_time is None:
                ds.warnings.append(f"{Path(path).name}: track {i} has no timestamps, skipped")
                continue
            ds.workouts.append(
                Workout(
                    source=SOURCE,
                    sport=(track.type or "unknown").lower().replace(" ", "_"),
                    start=bounds.start_time,
                    end=bounds.end_time,
                    duration_s=(bounds.end_time - bounds.start_time).total_seconds(),
                    distance_m=track.length_3d(),
                    elevation_gain_m=track.get_uphill_downhill().uphill,
                )
            )
    ds.workouts.sort(key=lambda w: w.start)
    return ds
