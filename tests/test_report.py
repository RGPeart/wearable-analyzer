from datetime import date

import pytest

from wearables import adapters, analytics, cli, synth
from wearables.model import Dataset

pytest.importorskip("plotly")
from wearables import figures, report  # noqa: E402


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    path = tmp_path_factory.mktemp("report")
    truth = synth.build(days=120, seed=4, end=date(2026, 10, 31))
    synth.write_apple_export(truth, path / "export.xml", seed=4)
    synth.write_fitbod_csv(truth, path / "WorkoutExport.csv")
    return path


@pytest.fixture(scope="module")
def ds(folder):
    return adapters.load(str(folder))


def test_every_chart_builds_on_a_full_dataset(ds):
    for key, _section, build, takes_since in figures.CHARTS:
        fig = build(ds, since=None) if takes_since else build(ds)
        assert fig is not None and len(fig.data) > 0, key


def test_charts_return_none_without_data():
    empty = Dataset()
    for _key, _section, build, takes_since in figures.CHARTS:
        assert (build(empty, since=None) if takes_since else build(empty)) is None


def test_since_trims_the_weekly_chart(ds):
    all_weeks = figures.weekly_activity(ds).data[0].x
    recent = figures.weekly_activity(ds, since=date(2026, 10, 1)).data[0].x
    assert 0 < len(recent) < len(all_weeks)
    assert min(recent) >= date(2026, 9, 28)  # the Monday of the week holding Oct 1... or later


def test_sleep_around_workouts_draws_before_and_after_bars_with_error_bars(ds):
    fig = figures.sleep_around_workouts(ds)
    assert [t.name for t in fig.data] == ["night before the workout day", "night after the workout day"]
    assert all(t.error_y.array is not None and len(t.error_y.array) == len(t.x) for t in fig.data)
    assert "no workout either side" in fig.layout.title.text
    assert "Observational" in fig.layout.annotations[0].text


def test_sleep_charts_honour_since_and_vanish_without_enough_nights(ds):
    assert figures.sleep_around_workouts(ds, since=date(2026, 10, 25)) is None  # about a week of nights
    assert figures.sleep_and_resting_hr(ds, since=date(2026, 10, 25)) is None


def test_sleep_and_resting_hr_scatter_reports_r_and_a_fit_line(ds):
    fig = figures.sleep_and_resting_hr(ds)
    assert [t.name for t in fig.data] == ["nights", "best-fit line"]
    assert "r = " in fig.layout.title.text and "nights)" in fig.layout.title.text


def test_time_in_bed_only_history_gives_no_sleep_comparison(ds):
    legacy = Dataset(
        workouts=ds.workouts,
        daily=ds.daily,
        sleep=[type(s)(s.night, s.asleep_s, in_bed_only=True) for s in ds.sleep],
    )
    assert figures.sleep_around_workouts(legacy) is None and figures.sleep_and_resting_hr(legacy) is None


def test_in_bed_only_nights_are_plotted_apart_from_real_sleep(ds):
    legacy = Dataset(sleep=[type(ds.sleep[0])(night=date(2020, 1, 1), asleep_s=9 * 3600, in_bed_only=True)])
    names = [t.name for t in figures.sleep(legacy).data]
    assert any("time in bed" in n for n in names)
    assert not any("mean" in n for n in names)  # nothing counted as sleep, so no average line


def test_rolling_mean_uses_calendar_days():
    series = [(date(2026, 1, 1), 10.0), (date(2026, 1, 2), 20.0), (date(2026, 1, 10), 40.0)]
    out = dict(analytics.rolling_mean(series, 3))
    assert out[date(2026, 1, 2)] == 15.0
    assert out[date(2026, 1, 10)] == 40.0  # earlier days fall outside the window


def test_load_history_agrees_with_load_summary_on_the_last_day(ds):
    hist = analytics.load_history(ds, rest_hr=55, max_hr=190)
    summary = analytics.load_summary(ds, rest_hr=55, max_hr=190)
    last = hist.points[-1]
    assert last.day == summary.as_of
    assert last.acute == pytest.approx(summary.acute)
    assert last.chronic == pytest.approx(summary.chronic)
    assert hist.sessions_counted == summary.sessions_counted


def test_report_html_has_sections_tiles_and_one_plotly_loader(ds):
    html = report.build_report(ds)
    for text in ("Activity", "Lifting", "Recovery", "Load", "Resting HR", "Notes", "use ET"):
        assert text in html, text
    assert html.count('<script charset="utf-8" src="https://cdn.plot.ly') == 1  # once, not per chart


def test_report_offline_embeds_plotly(ds):
    html = report.build_report(ds, offline=True)
    assert 'src="https://cdn.plot.ly' not in html and len(html) > 1_000_000


def test_report_escapes_titles_and_warnings(ds):
    ds2 = Dataset(workouts=ds.workouts, daily=ds.daily, sleep=ds.sleep, warnings=["<script>x</script>"])
    html = report.build_report(ds2, title="<b>t</b>")
    assert "<script>x</script>" not in html and "&lt;script&gt;x&lt;/script&gt;" in html
    assert "<title>&lt;b&gt;t&lt;/b&gt;</title>" in html


def test_report_refuses_an_empty_dataset():
    with pytest.raises(ValueError, match="empty"):
        report.build_report(Dataset())


def test_report_command_writes_a_file(capsys, folder, tmp_path):
    out = tmp_path / "r.html"
    assert cli.main(["report", str(folder), "-o", str(out), "--since", "2026-09-01"]) == 0
    assert out.read_text().startswith("<!doctype html>") and "wrote" in capsys.readouterr().out
