"""Interactive dashboard (Streamlit) over the same figures as the static report.

Run ``wearables app <path>``. Needs the ``app`` extra: ``pip install 'wearable-analyzer[app]'``.
"""

import os
from datetime import timedelta
from pathlib import Path

import streamlit as st

from wearables import adapters, analytics, figures
from wearables.model import Dataset
from wearables.tz import TZ_LABEL

PRESETS = {"All time": None, "Last 2 years": 730, "Last year": 365, "Last 90 days": 90}


def default_path() -> str:
    """Set by ``wearables app <path>`` (argv is no use: streamlit and test runners share it)."""
    return os.environ.get("WEARABLES_PATH", "")


@st.cache_resource(show_spinner="Reading the export (a large Apple Health file takes a couple of minutes)...")
def load(path: str, mtime: float) -> Dataset:
    """Parsed once per path and file version; ``mtime`` is only the cache key."""
    return adapters.load(path)


def tiles(ds: Dataset) -> None:
    s = analytics.summarize(ds)
    cols = st.columns(5)
    cols[0].metric(
        "Workouts", f"{s['workouts']:,}", f"{s['first_workout']} to {s['last_workout']}", delta_color="off"
    )
    for col, label, key, unit in (
        (cols[1], "Resting HR, 30 days", "resting_hr", "bpm"),
        (cols[2], "HRV (SDNN), 30 days", "hrv_ms", "ms"),
    ):
        t = s[key]
        if t is None:
            col.metric(label, "n/a")
        else:
            # Lower resting HR is better, higher HRV is better; leave the colour to the reader.
            col.metric(label, f"{t.recent:.1f} {unit}", f"{t.delta:+.1f} vs prior 30 days", delta_color="off")
    sleep, steps = s["sleep_hours_avg_30d"], s["steps_avg_30d"]
    cols[3].metric(
        "Sleep, 30 days", f"{sleep:.1f} h" if sleep is not None else "n/a", help="asleep nights only"
    )
    cols[4].metric("Steps, 30 days", f"{steps:,.0f}" if steps is not None else "n/a", help="per day")


def main() -> None:
    st.set_page_config(page_title="Wearable dashboard", layout="wide")
    st.title("Wearable dashboard")
    st.caption(f"Days, weeks and nights use {TZ_LABEL}. Runs on this machine; nothing is uploaded.")

    with st.sidebar:
        path = st.text_input(
            "Data path", default_path(), help="Apple export.xml/.zip, Fitbod CSV, .gpx, or a folder"
        )
        preset = st.selectbox("Range", list(PRESETS))
        rest_hr = st.number_input("Resting HR for load (0 = median of data)", 0, 120, 0)
        max_hr = st.number_input("Max HR for load (0 = highest seen)", 0, 230, 0)

    if not path:
        st.info("Enter a data path in the sidebar, or start with `wearables app <path>`.")
        return
    target = Path(path).expanduser()
    if not target.exists():
        st.error(f"No such file or folder: {target}")
        return
    try:
        ds = load(str(target), target.stat().st_mtime)
    except (FileNotFoundError, ValueError) as exc:
        st.error(str(exc))
        return
    if not (ds.workouts or ds.daily or ds.sleep):
        st.warning("Nothing to show: the data is empty.")
        return

    days = PRESETS[preset]
    end = analytics.last_day(ds)
    since = end - timedelta(days=days) if days and end else None

    tiles(ds)
    heading = None
    for key, section, build, takes_since in figures.CHARTS:
        if key == "load":
            fig = build(ds, since=since, rest_hr=rest_hr or None, max_hr=max_hr or None)
        else:
            fig = build(ds, since=since) if takes_since else build(ds)
        if fig is None:
            continue
        if section != heading:
            st.header(section)
            heading = section
        st.plotly_chart(fig, use_container_width=True)
    with st.expander("Notes"):
        st.markdown(
            "- Training load is a rough heart-rate-reserve estimate, not medical advice.\n"
            "- Nights with only time-in-bed data (before iOS 16) overstate sleep and stay out of its average."
        )
        for w in ds.warnings:
            st.text(w)


main()
