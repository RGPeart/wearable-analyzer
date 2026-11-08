"""One self-contained HTML report: summary tiles plus the charts from :mod:`wearables.figures`."""

from datetime import date
from html import escape

from wearables import analytics, figures
from wearables.model import Dataset
from wearables.tz import TZ_LABEL

_CSS = """
body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#fff;color:#1b1f24}
main{max-width:980px;margin:0 auto;padding:24px 20px 48px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:36px 0 4px;border-bottom:1px solid #e6e8eb;padding-bottom:4px}
.sub{color:#5b636d;margin:0 0 20px}
.tiles{display:flex;flex-wrap:wrap;gap:12px}
.tile{flex:1 1 150px;border:1px solid #e6e8eb;border-radius:8px;padding:10px 14px}
.tile b{display:block;font-size:22px}.tile span{color:#5b636d;font-size:13px}
.note{color:#5b636d;font-size:13px}
"""


def _tile(label: str, value: str, detail: str = "") -> str:
    return (
        f"<div class=tile><span>{escape(label)}</span><b>{escape(value)}</b>"
        f"<span>{escape(detail)}</span></div>"
    )


def _trend_tile(label: str, t: analytics.Trend | None, unit: str) -> str:
    if t is None:
        return _tile(label, "n/a", "not enough data")
    return _tile(label, f"{t.recent:.1f} {unit}", f"{t.delta:+.1f} vs prior 30 days")


def _tiles(ds: Dataset) -> str:
    s = analytics.summarize(ds)
    sleep, steps = s["sleep_hours_avg_30d"], s["steps_avg_30d"]
    tiles = [
        _tile("Workouts", f"{s['workouts']:,}", f"{s['first_workout']} to {s['last_workout']}"),
        _trend_tile("Resting HR, last 30 days", s["resting_hr"], "bpm"),
        _trend_tile("HRV (SDNN), last 30 days", s["hrv_ms"], "ms"),
        _tile("Sleep, last 30 days", f"{sleep:.1f} h" if sleep is not None else "n/a", "per night, asleep"),
        _tile("Steps, last 30 days", f"{steps:,.0f}" if steps is not None else "n/a", "per day"),
    ]
    return f"<div class=tiles>{''.join(tiles)}</div>"


def build_report(
    ds: Dataset, since: date | None = None, offline: bool = False, title: str = "Wearable report"
) -> str:
    """The report as an HTML string. ``offline`` embeds plotly.js (~5 MB) instead of loading it from a CDN."""
    if not (ds.workouts or ds.daily or ds.sleep):
        raise ValueError("nothing to report: the dataset is empty")
    sections: list[str] = []
    heading = None
    first = True
    for _key, section, build, takes_since in figures.CHARTS:
        fig = build(ds, since=since) if takes_since else build(ds)
        if fig is None:
            continue
        if section != heading:
            sections.append(f"<h2>{escape(section)}</h2>")
            heading = section
        js = (True if offline else "cdn") if first else False
        sections.append(fig.to_html(full_html=False, include_plotlyjs=js))
        first = False
    notes = [
        "Training load is a rough heart-rate-reserve estimate for comparing weeks, not medical advice.",
        "Nights with only time-in-bed data (before iOS 16) overstate sleep and stay out of its average.",
        "The sleep-and-training charts only use nights with sleep stages and are observational: they show "
        "patterns, not causes.",
        *ds.warnings,
    ]
    return (
        "<!doctype html><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
        f"<style>{_CSS}</style><main><h1>{escape(title)}</h1>"
        f"<p class=sub>All days, weeks and nights use {TZ_LABEL}. Generated locally; nothing is uploaded.</p>"
        f"{_tiles(ds)}{''.join(sections)}<h2>Notes</h2>"
        f"<ul class=note>{''.join(f'<li>{escape(n)}</li>' for n in notes)}</ul></main>"
    )
