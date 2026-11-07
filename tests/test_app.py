from datetime import date
from pathlib import Path

import pytest

from wearables import cli, synth

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).parents[1] / "wearables" / "app.py")


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    path = tmp_path_factory.mktemp("app")
    truth = synth.build(days=120, seed=5, end=date(2026, 10, 31))
    synth.write_apple_export(truth, path / "export.xml", seed=5)
    synth.write_fitbod_csv(truth, path / "WorkoutExport.csv")
    return path


def run(path: str = "") -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if path:
        at.sidebar.text_input[0].set_value(path).run()
    return at


def test_asks_for_a_path_when_none_given():
    at = run()
    assert not at.exception and "Enter a data path" in at.info[0].value


def test_missing_path_is_an_error_not_a_crash(tmp_path):
    at = run(str(tmp_path / "nope"))
    assert not at.exception and "No such file" in at.error[0].value


def test_renders_tiles_and_sections(folder):
    at = run(str(folder))
    assert not at.exception
    assert [m.label for m in at.metric][:2] == ["Workouts", "Resting HR, 30 days"]
    assert [h.value for h in at.header] == ["Activity", "Lifting", "Recovery", "Load"]


def test_range_preset_changes_the_view_without_error(folder):
    at = run(str(folder))
    at.sidebar.selectbox[0].set_value("Last 90 days").run()
    assert not at.exception and len(at.header) == 4


def test_app_command_without_streamlit_installed_explains_itself(capsys, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "streamlit.web.cli", None)  # makes the import fail
    assert cli.main(["app"]) == 1
    assert "wearable-analyzer[app]" in capsys.readouterr().err
