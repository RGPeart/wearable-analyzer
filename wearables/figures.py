"""Plotly figures over a :class:`~wearables.model.Dataset`.

The one place that turns analytics into charts. The static ``report`` and the interactive
``app`` both call these, so a chart fixed here is fixed in both. Each function returns a
figure, or ``None`` when the data has nothing to draw. Needs the ``report`` extra
(``pip install 'wearable-analyzer[report]'``).
"""

from datetime import date, timedelta
from statistics import linear_regression

import plotly.graph_objects as go

from wearables import analytics
from wearables.model import LB_PER_KG, Dataset
from wearables.tz import TZ_LABEL, local_day

BLUE, ORANGE, GREY = "#2a6fdb", "#d95f02", "#8a8f98"
_AXIS = {"gridcolor": "#e6e8eb", "zeroline": False}


def _base(title: str, ytitle: str, height: int = 340) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        title={"text": title, "x": 0, "font": {"size": 16}},
        template="plotly_white",
        height=height,
        margin={"l": 55, "r": 20, "t": 50, "b": 40},
        legend={"orientation": "h", "y": -0.2},
        yaxis={**_AXIS, "title": ytitle},
        xaxis={"title": None},
        hovermode="x unified",
    )
    return fig


def _since(rows: list, key, since: date | None) -> list:
    return [r for r in rows if since is None or key(r) >= since]


def weekly_activity(ds: Dataset, since: date | None = None) -> go.Figure | None:
    weeks = _since(analytics.weekly_summary(ds.workouts), lambda w: w.week_start, since)
    if not weeks:
        return None
    fig = _base(f"Training hours per week (weeks start Monday, {TZ_LABEL})", "hours")
    fig.add_bar(
        x=[w.week_start for w in weeks],
        y=[w.hours for w in weeks],
        marker_color=BLUE,
        name="hours",
        customdata=[[w.workouts, w.distance_km] for w in weeks],
        hovertemplate="%{y:.1f} h, %{customdata[0]} workouts, %{customdata[1]:.0f} km<extra></extra>",
    )
    return fig


def strength_volume(ds: Dataset, since: date | None = None) -> go.Figure | None:
    weeks = _since(analytics.weekly_strength(ds.sets), lambda w: w.week_start, since)
    if not weeks:
        return None
    fig = _base("Lifting volume per week (warm-ups excluded)", "volume (lb)")
    fig.add_bar(
        x=[w.week_start for w in weeks],
        y=[w.volume_kg * LB_PER_KG for w in weeks],
        marker_color=BLUE,
        name="volume",
        customdata=[[w.sessions, w.working_sets] for w in weeks],
        hovertemplate="%{y:,.0f} lb, %{customdata[0]} sessions, %{customdata[1]} sets<extra></extra>",
    )
    return fig


def top_exercises(ds: Dataset, days: int = 90, limit: int = 10) -> go.Figure | None:
    if not ds.sets:
        return None
    since = max(local_day(s.session_start) for s in ds.sets) - timedelta(days=days)
    top = analytics.top_exercises(ds.sets, since=since, limit=limit)
    if not top:
        return None
    top = top[::-1]  # largest bar on top
    fig = _base(f"Top exercises by volume, last {days} days", "", height=max(260, 60 + 32 * len(top)))
    fig.update_layout(hovermode="closest", yaxis={"title": None}, xaxis={**_AXIS, "title": "volume (lb)"})
    fig.add_bar(
        y=[t.exercise for t in top],
        x=[t.volume_kg * LB_PER_KG for t in top],
        orientation="h",
        marker_color=BLUE,
        customdata=[[t.working_sets, t.best_weight_kg * LB_PER_KG] for t in top],
        hovertemplate="%{x:,.0f} lb, %{customdata[0]} sets, best %{customdata[1]:.0f} lb<extra></extra>",
    )
    return fig


def resting_hr(ds: Dataset, since: date | None = None, window: int = 28) -> go.Figure | None:
    series = _since(
        [(d.day, d.resting_hr) for d in ds.daily if d.resting_hr is not None], lambda r: r[0], since
    )
    if not series:
        return None
    fig = _base("Resting heart rate", "bpm")
    fig.add_scatter(
        x=[d for d, _ in series],
        y=[v for _, v in series],
        mode="markers",
        marker={"color": GREY, "size": 4, "opacity": 0.6},
        name="daily",
        hovertemplate="%{y:.0f} bpm<extra>daily</extra>",
    )
    smooth = analytics.rolling_mean(series, window)
    fig.add_scatter(
        x=[d for d, _ in smooth],
        y=[v for _, v in smooth],
        mode="lines",
        line={"color": BLUE, "width": 2.5},
        name=f"{window}-day mean",
        hovertemplate="%{y:.1f} bpm<extra>" + f"{window}-day mean</extra>",
    )
    return fig


def sleep(ds: Dataset, since: date | None = None, window: int = 14) -> go.Figure | None:
    nights = _since(ds.sleep, lambda s: s.night, since)
    if not nights:
        return None
    asleep = [(s.night, s.asleep_s / 3600) for s in nights if not s.in_bed_only]
    in_bed = [(s.night, s.asleep_s / 3600) for s in nights if s.in_bed_only]
    fig = _base("Sleep per night", "hours")
    fig.add_scatter(
        x=[d for d, _ in asleep],
        y=[v for _, v in asleep],
        mode="markers",
        marker={"color": GREY, "size": 4, "opacity": 0.6},
        name="asleep",
        hovertemplate="%{y:.1f} h<extra>asleep</extra>",
    )
    if in_bed:
        fig.add_scatter(
            x=[d for d, _ in in_bed],
            y=[v for _, v in in_bed],
            mode="markers",
            marker={"color": ORANGE, "size": 4, "opacity": 0.5, "symbol": "circle-open"},
            name="time in bed only (overstates sleep)",
            hovertemplate="%{y:.1f} h<extra>in bed only</extra>",
        )
    smooth = analytics.rolling_mean(asleep, window)
    if smooth:
        fig.add_scatter(
            x=[d for d, _ in smooth],
            y=[v for _, v in smooth],
            mode="lines",
            line={"color": BLUE, "width": 2.5},
            name=f"{window}-day mean (asleep nights)",
            hovertemplate="%{y:.1f} h<extra>" + f"{window}-day mean</extra>",
        )
    return fig


def training_load(
    ds: Dataset,
    since: date | None = None,
    rest_hr: float | None = None,
    max_hr: float | None = None,
) -> go.Figure | None:
    try:
        history = analytics.load_history(ds, rest_hr=rest_hr, max_hr=max_hr)
    except ValueError:
        return None
    points = _since(history.points, lambda p: p.day, since)
    if not points:
        return None
    total = history.sessions_counted + history.sessions_skipped
    fig = _base(
        f"Training load ({history.sessions_skipped} of {total} workouts have no heart rate and are left out)",
        "load per day",
    )
    fig.add_scatter(
        x=[p.day for p in points],
        y=[p.chronic for p in points],
        mode="lines",
        line={"color": GREY, "width": 2},
        name="28-day (chronic)",
    )
    fig.add_scatter(
        x=[p.day for p in points],
        y=[p.acute for p in points],
        mode="lines",
        line={"color": ORANGE, "width": 2},
        name="7-day (acute)",
    )
    fig.update_layout(title={"font": {"size": 14}})
    return fig


def _sleep_since(ds: Dataset, since: date | None) -> Dataset:
    """Only the nights from ``since`` on; workouts stay whole because a night looks at the next day."""
    if since is None:
        return ds
    return Dataset(
        workouts=ds.workouts, daily=ds.daily, sleep=[s for s in ds.sleep if s.night >= since], sets=ds.sets
    )


def sleep_around_workouts(ds: Dataset, since: date | None = None) -> go.Figure | None:
    effects = analytics.sleep_vs_workouts(_sleep_since(ds, since))
    if not effects:
        return None
    first = effects[0]
    fig = _base(
        f"Sleep around workout days, against {first.baseline_nights} nights with no workout either side "
        f"({first.baseline_h:.1f} h on average)",
        "hours of sleep vs those nights",
    )
    fig.update_layout(hovermode="closest", barmode="group", title={"font": {"size": 14}})
    fig.add_hline(y=0, line_color=GREY, line_width=1)
    for when, color in (("before", BLUE), ("after", ORANGE)):
        rows = [e for e in effects if e.when == when]
        fig.add_bar(
            x=[e.group for e in rows],
            y=[e.diff_h for e in rows],
            error_y={"type": "data", "array": [e.ci_h for e in rows], "color": "#444"},
            marker_color=color,
            name=f"night {when} the workout day",
            customdata=[[e.nights, e.mean_h, e.ci_h] for e in rows],
            hovertemplate="%{y:+.2f} h (±%{customdata[2]:.2f}), %{customdata[0]} nights, "
            "%{customdata[1]:.2f} h average<extra></extra>",
        )
    fig.add_annotation(
        text="Bars show hours more or less sleep than a night with no workout nearby; whiskers are a rough "
        "95% range. Observational: an early alarm also shortens the night before a morning session.",
        xref="paper", yref="paper", x=0, y=-0.32, showarrow=False, align="left", font={"size": 11},
    )  # fmt: skip
    fig.update_layout(margin={"l": 55, "r": 20, "t": 60, "b": 90})
    return fig


def sleep_and_resting_hr(ds: Dataset, since: date | None = None) -> go.Figure | None:
    rows = [r for r in analytics.sleep_vs_recovery(_sleep_since(ds, since)) if r.resting_hr is not None]
    pairs = [(r.sleep_h, r.resting_hr) for r in rows]
    r = analytics.correlation(pairs)
    if r is None:
        return None
    fig = _base(f"Sleep vs next morning's resting heart rate (r = {r:+.2f}, {len(rows)} nights)", "bpm")
    fig.update_layout(hovermode="closest", xaxis={**_AXIS, "title": "hours slept"})
    fig.add_scatter(
        x=[p[0] for p in pairs],
        y=[p[1] for p in pairs],
        mode="markers",
        marker={"color": BLUE, "size": 5, "opacity": 0.5},
        name="nights",
        customdata=[[str(x.night)] for x in rows],
        hovertemplate="%{customdata[0]}: %{x:.1f} h, %{y:.0f} bpm<extra></extra>",
    )
    slope, intercept = linear_regression([p[0] for p in pairs], [p[1] for p in pairs])
    lo, hi = min(p[0] for p in pairs), max(p[0] for p in pairs)
    fig.add_scatter(
        x=[lo, hi], y=[slope * lo + intercept, slope * hi + intercept], mode="lines",
        line={"color": ORANGE, "width": 2}, name="best-fit line", hoverinfo="skip",
    )  # fmt: skip
    return fig


# (key, section heading, builder, takes a ``since`` date)
CHARTS = [
    ("activity", "Activity", weekly_activity, True),
    ("strength", "Lifting", strength_volume, True),
    ("exercises", "Lifting", top_exercises, False),
    ("rhr", "Recovery", resting_hr, True),
    ("sleep", "Recovery", sleep, True),
    ("load", "Load", training_load, True),
    ("sleepwork", "Sleep and training", sleep_around_workouts, True),
    ("sleeprhr", "Sleep and training", sleep_and_resting_hr, True),
]
