"""Adapters turn a source's files into a :class:`~wearables.model.Dataset`.

Adding a source means writing a module with ``load(path) -> Dataset`` and
teaching :func:`load` when to use it. Nothing else in the package knows where
data came from. After loading, :func:`wearables.merge.reconcile` resolves
workouts that more than one app recorded.
"""

from pathlib import Path

from wearables import merge
from wearables.adapters import apple_health, fitbod, gpx
from wearables.model import Dataset

_HINT = (
    "expected an Apple Health export.xml/.zip, a Fitbod WorkoutExport.csv, a .gpx file or a folder of them"
)


def _load_folder(p: Path) -> Dataset:
    ds = Dataset()
    found = False
    exports = sorted(p.rglob("export.xml")) or sorted(p.rglob("export.zip"))
    if exports:
        ds.extend(apple_health.load(exports[0]))
        found = True
    for csv_file in sorted(p.rglob("*.csv")):
        if fitbod.is_fitbod_csv(csv_file):
            ds.extend(fitbod.load(csv_file))
            found = True
    if not exports and (tracks := sorted(p.rglob("*.gpx"))):  # an Apple export already contains its routes
        ds.extend(gpx.load_many(tracks))
        found = True
    if not found:
        raise ValueError(f"no export.xml, Fitbod CSV or .gpx files found under {p}")
    return ds


def _load_raw(p: Path) -> Dataset:
    if p.is_dir():
        return _load_folder(p)
    suffix = p.suffix.lower()
    if suffix in {".xml", ".zip"}:
        return apple_health.load(p)
    if suffix == ".gpx":
        return gpx.load_many([p])
    if suffix == ".csv":
        if not fitbod.is_fitbod_csv(p):
            raise ValueError(f"unsupported CSV {p.name!r}: only the Fitbod export is understood ({_HINT})")
        return fitbod.load(p)
    raise ValueError(f"unsupported input {p.name!r}: {_HINT}")


def load(path: str | Path) -> Dataset:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    return merge.reconcile(_load_raw(p))
