from datetime import date

import pytest

from wearables import synth

END = date(2026, 10, 31)


@pytest.fixture(scope="session")
def truth():
    return synth.build(days=56, seed=3, end=END)


@pytest.fixture(scope="session")
def export_xml(tmp_path_factory, truth):
    path = tmp_path_factory.mktemp("synth") / "export.xml"
    synth.write_apple_export(truth, path, seed=3)
    return path
