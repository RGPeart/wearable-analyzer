"""Command line entry point: ``python -m wearables`` or ``wearables``."""

import argparse
import dataclasses
import json
import sys
from datetime import date, timedelta

from wearables import adapters, analytics, synth
from wearables.model import LB_PER_KG, Dataset
from wearables.tz import TZ_LABEL, local_day


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wearables", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def with_input(name: str, help_: str) -> argparse.ArgumentParser:
        s = sub.add_parser(name, help=help_)
        s.add_argument(
            "path",
            help="Apple Health export.xml/.zip, a Fitbod WorkoutExport.csv, a .gpx file, or a folder of them "
            "(a folder with both Apple and Fitbod data is merged)",
        )
        s.add_argument("--json", action="store_true", help="print JSON instead of a table")
        return s

    with_input("summary", "workouts by sport plus 30-day recovery and activity trends")
    weekly = with_input(
        "weekly", f"workouts, hours, distance and energy per week (weeks start Monday, {TZ_LABEL})"
    )
    weekly.add_argument("--weeks", type=int, default=12, help="most recent weeks to show (default 12)")
    load = with_input("load", "rough 7-day vs 28-day training load from heart rate")
    load.add_argument("--rest-hr", type=float, help="resting heart rate (default: median of the data)")
    load.add_argument("--max-hr", type=float, help="max heart rate (default: highest seen in a workout)")
    strength = with_input("strength", "weekly lifting volume and top exercises (needs a Fitbod export)")
    strength.add_argument("--weeks", type=int, default=12, help="most recent weeks to show (default 12)")
    strength.add_argument("--top", type=int, default=8, help="exercises to list, by volume (default 8)")
    strength.add_argument(
        "--top-days", type=int, default=90, help="look-back for the exercise list (default 90)"
    )

    gen = sub.add_parser("synth", help="write a synthetic Apple Health export.xml (and Fitbod CSV) for demos")
    gen.add_argument("out", help="output path, e.g. demo-export.xml")
    gen.add_argument(
        "--fitbod", metavar="CSV", help="also write a matching synthetic Fitbod WorkoutExport.csv"
    )
    gen.add_argument("--days", type=int, default=180)
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument("--end", type=date.fromisoformat, default=date.today(), help="last day, YYYY-MM-DD")
    return p


def table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]

    def fmt(cells: list[str]) -> str:
        return "  ".join(
            c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths, strict=True))
        )

    return "\n".join([fmt(headers), fmt(["-" * w for w in widths]), *(fmt(r) for r in rows)])


def _json(obj) -> str:
    def default(o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        if isinstance(o, date):
            return o.isoformat()
        raise TypeError(type(o))

    return json.dumps(obj, default=default, indent=2)


def _load(path: str) -> Dataset:
    ds = adapters.load(path)
    for w in ds.warnings:
        print(f"note: {w}", file=sys.stderr)
    return ds


def _trend_line(label: str, t: analytics.Trend | None, unit: str) -> str:
    if t is None:
        return f"{label}: not enough data"
    return f"{label}: {t.recent:.1f} {unit} (prior 30d {t.prior:.1f}, {t.delta:+.1f})"


def cmd_summary(args: argparse.Namespace) -> str:
    s = analytics.summarize(_load(args.path))
    if args.json:
        return _json(s)
    out = [f"{s['workouts']} workouts, {s['first_workout']} to {s['last_workout']}"]
    if s["by_sport"]:
        rows = [
            [r["sport"], str(r["count"]), f"{r['hours']:.1f}", f"{r['distance_km']:.1f}"]
            for r in s["by_sport"]
        ]
        out += ["", table(["sport", "count", "hours", "km"], rows)]
    out += ["", f"Last 30 days up to {s['as_of']}:"]
    out.append("  " + _trend_line("resting HR", s["resting_hr"], "bpm"))
    out.append("  " + _trend_line("HRV (SDNN)", s["hrv_ms"], "ms"))
    sleep, steps = s["sleep_hours_avg_30d"], s["steps_avg_30d"]
    out.append("  sleep: " + (f"{sleep:.1f} h/night" if sleep is not None else "no data"))
    out.append("  steps: " + (f"{steps:,.0f}/day" if steps is not None else "no data"))
    return "\n".join(out)


def cmd_weekly(args: argparse.Namespace) -> str:
    weeks = analytics.weekly_summary(_load(args.path).workouts)[-args.weeks :]
    if args.json:
        return _json(weeks)
    rows = [
        [str(w.week_start), str(w.workouts), f"{w.hours:.1f}", f"{w.distance_km:.1f}", f"{w.energy_kcal:.0f}"]
        for w in weeks
    ]
    return table(["week of", "workouts", "hours", "km", "kcal"], rows)


def cmd_load(args: argparse.Namespace) -> str:
    ls = analytics.load_summary(_load(args.path), rest_hr=args.rest_hr, max_hr=args.max_hr)
    if args.json:
        return _json(ls)
    ratio = f"{ls.ratio:.2f}" if ls.ratio is not None else "n/a"
    return "\n".join(
        [
            f"As of {ls.as_of} (resting {ls.rest_hr:.0f} bpm, max {ls.max_hr:.0f} bpm)",
            f"  acute load (7d avg/day):   {ls.acute:.1f}",
            f"  chronic load (28d avg/day): {ls.chronic:.1f}",
            f"  acute:chronic ratio:        {ratio}",
            f"  sessions counted {ls.sessions_counted}, skipped {ls.sessions_skipped} (no heart rate)",
            "Rough estimate for comparing weeks, not medical advice.",
        ]
    )


def cmd_strength(args: argparse.Namespace) -> str:
    ds = _load(args.path)
    if not ds.sets:
        raise ValueError("no lifting sets found: pass a Fitbod WorkoutExport.csv or a folder containing one")
    weeks = analytics.weekly_strength(ds.sets)[-args.weeks :]
    since = max(local_day(s.session_start) for s in ds.sets) - timedelta(days=args.top_days)
    top = analytics.top_exercises(ds.sets, since=since, limit=args.top)
    if args.json:
        return _json({"weeks": weeks, "top_exercises": top})
    rows = [
        [
            str(w.week_start),
            str(w.sessions),
            str(w.working_sets),
            str(w.reps),
            f"{w.volume_kg * LB_PER_KG:,.0f}",
        ]
        for w in weeks
    ]
    out = [table(["week of", "sessions", "sets", "reps", "volume lb"], rows), ""]
    out.append(f"Top exercises by volume, last {args.top_days} days (warm-ups excluded):")
    out.append(
        table(
            ["exercise", "sets", "volume lb", "best lb"],
            [
                [
                    t.exercise,
                    str(t.working_sets),
                    f"{t.volume_kg * LB_PER_KG:,.0f}",
                    f"{t.best_weight_kg * LB_PER_KG:.0f}",
                ]
                for t in top
            ],
        )
    )
    out.append(
        "Volume = reps x weight x multiplier (Fitbod's multiplier is undocumented; the usual reading)."
    )
    return "\n".join(out)


def cmd_synth(args: argparse.Namespace) -> str:
    ds = synth.build(args.days, args.seed, args.end)
    synth.write_apple_export(ds, args.out, seed=args.seed)
    msg = f"wrote {args.out}: {args.days} days, {len(ds.workouts)} workouts (synthetic)"
    if args.fitbod:
        synth.write_fitbod_csv(ds, args.fitbod)
        msg += f"; wrote {args.fitbod}: {len(ds.sets)} sets"
    return msg


COMMANDS = {
    "summary": cmd_summary,
    "weekly": cmd_weekly,
    "load": cmd_load,
    "strength": cmd_strength,
    "synth": cmd_synth,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(COMMANDS[args.command](args))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
