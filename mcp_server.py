#!/usr/bin/env python3
"""
PaceTrace MCP Server

Exposes Strava training data to Claude as native tools.

Local mode (Claude Desktop):
    python mcp_server.py

Remote mode (Railway — SSE transport via FastAPI):
    uvicorn mcp_server:sse_app --host 0.0.0.0 --port $PORT
"""

import asyncio
import json
import os
import statistics
import sys
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_request_user: ContextVar[str] = ContextVar("request_user", default="")

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from strava_pipeline.api.analysis import interpret_activity, zone_distribution, _vel_to_pace
from strava_pipeline.api.metrics import (
    trimp_from_stream,
    trimp_from_avg_hr,
    compute_fitness_metrics,
    aerobic_decoupling,
    grade_adjusted_pace,
    find_best_efforts,
    cadence_analysis,
    karvonen_zone_distribution,
    efficiency_factor,
    interpret_tsb,
    interpret_ctl,
    interpret_decoupling,
    EFFORT_DISTANCES,
)
from strava_pipeline.claude.query_helper import build_context
from strava_pipeline.db.activities import (
    get_activity,
    get_activities_in_range,
    get_recent_activities,
    get_athlete_stats,
)
from strava_pipeline.db.streams import get_stream
from strava_pipeline.utils.user_loader import get_user_by_name


# ── Server setup ───────────────────────────────────────────────────────────────

server = Server("pacetrace")

DEFAULT_USER = os.environ.get("PACETRACE_USER", "ben")
MAX_HR    = int(os.environ.get("PACETRACE_MAX_HR",   "185"))
REST_HR   = int(os.environ.get("PACETRACE_REST_HR",  "55"))
GENDER    = os.environ.get("PACETRACE_GENDER", "male")


def _effective_user(user: str) -> str:
    if user and user != DEFAULT_USER:
        return user
    ctx = _request_user.get()
    return ctx if ctx else user


def _resolve_athlete_id(user: str) -> int | None:
    user = _effective_user(user)
    u = get_user_by_name(user)
    if u:
        return int(u["STRAVA_ATHLETE_ID"])
    try:
        from strava_pipeline.db.tokens import get_tokens_by_username
        tokens = get_tokens_by_username(user)
        if tokens:
            return int(tokens["athlete_id"])
    except Exception:
        pass
    return None


def _fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _pace(vel_ms) -> str:
    return _vel_to_pace(vel_ms) if vel_ms else "—"


# ── Tool definitions ───────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_activity",
            description=(
                "Get full details of a single run including metadata and rich analytics: "
                "TRIMP training load, aerobic decoupling %, Grade Adjusted Pace (GAP), "
                "Karvonen HR zones, cadence analysis, efficiency factor, best efforts "
                "achieved in the run, and a downsampled pace/HR stream table. "
                "Use this to deeply analyse the quality and effort of a specific session."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {"type": "string", "description": "Strava activity ID"},
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                },
                "required": ["activity_id"],
            },
        ),
        Tool(
            name="get_week",
            description=(
                "Get a summary of all training in the 7 days ending on a given date. "
                "Returns total km, time, avg HR, HR zone distribution, and each run listed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                    "date": {"type": "string", "description": "End date YYYY-MM-DD (default: today)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_recent",
            description=(
                "Get the most recent n activities as a compact list with date, name, "
                "distance, duration, avg HR and pace. Use for quick overviews."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                    "n": {"type": "string", "description": "Number of activities (default: 5, max: 20)", "default": "5"},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_athlete_summary",
            description=(
                "Get overall career stats: total km, hours, number of runs, avg HR, elevation gain."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                },
                "required": [],
            },
        ),
        Tool(
            name="search_activities",
            description=(
                "Search and filter activities by date range or distance. "
                "Use to find sessions like 'all runs in January' or 'long runs over 20km'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                    "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
                    "min_distance_km": {"type": "number", "description": "Min distance filter (km)"},
                    "max_distance_km": {"type": "number", "description": "Max distance filter (km)"},
                    "limit": {"type": "integer", "description": "Max results (default: 20)", "default": 20},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_fitness_trend",
            description=(
                "Compute CTL (fitness), ATL (fatigue), and TSB (form/freshness) using "
                "Banister's TRIMP model with exponential weighted averages over a date range. "
                "CTL = 42-day training load average (aerobic base). "
                "ATL = 7-day training load average (current fatigue). "
                "TSB = CTL - ATL (positive = fresh, negative = fatigued). "
                "Also returns weekly TRIMP load for last 10 weeks and recent daily history. "
                "Use to assess current training state, fatigue, and readiness."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                    "days": {"type": "integer", "description": "Days of history to analyse (default: 120)", "default": 120},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_best_efforts",
            description=(
                "Find all-time or recent personal bests at standard distances "
                "(1km, 5km, 10km, half marathon, full marathon) by scanning every run's "
                "stream data with a sliding window. Returns best time, pace, and which run it was set on. "
                "Use to track PRs, assess race fitness, or find breakthrough performances."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                    "since": {"type": "string", "description": "Only search from this date YYYY-MM-DD (default: all time)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="compare_runs",
            description=(
                "Compare two runs side by side with normalised metrics: "
                "distance, pace, avg HR, TRIMP, aerobic decoupling, Grade Adjusted Pace, "
                "efficiency factor, cadence, and HR zone distributions. "
                "Use to compare similar workouts, track progress, or contrast race vs training efforts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id_1": {"type": "string", "description": "First Strava activity ID"},
                    "activity_id_2": {"type": "string", "description": "Second Strava activity ID"},
                    "user": {"type": "string", "description": "Athlete username", "default": DEFAULT_USER},
                },
                "required": ["activity_id_1", "activity_id_2"],
            },
        ),
    ]


# ── Tool handlers ──────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "get_activity":
            result = await _get_activity(arguments)
        elif name == "get_week":
            result = await _get_week(arguments)
        elif name == "get_recent":
            result = await _get_recent(arguments)
        elif name == "get_athlete_summary":
            result = await _get_athlete_summary(arguments)
        elif name == "search_activities":
            result = await _search_activities(arguments)
        elif name == "get_fitness_trend":
            result = await _get_fitness_trend(arguments)
        elif name == "get_best_efforts":
            result = await _get_best_efforts(arguments)
        elif name == "compare_runs":
            result = await _compare_runs(arguments)
        else:
            result = f"Unknown tool: {name}"
    except Exception as e:
        import traceback
        result = f"Error: {e}\n{traceback.format_exc()}"

    return [TextContent(type="text", text=result if isinstance(result, str) else json.dumps(result, indent=2))]


# ── Individual handlers ────────────────────────────────────────────────────────

async def _get_activity(args: dict) -> str:
    activity_id = int(args["activity_id"])

    activity = get_activity(activity_id)
    if not activity:
        return f"Activity {activity_id} not found in database."

    # Base stream table (existing)
    base = build_context(activity_id, max_points=120, max_hr=MAX_HR)

    stream = get_stream(activity_id)
    if not stream:
        return base

    hr   = stream.get("heartrate") or []
    vel  = stream.get("velocity_ms") or []
    alt  = stream.get("altitude_m") or []
    dist = stream.get("distance_m") or []
    t_s  = stream.get("time_s") or []
    cad  = stream.get("cadence") or []

    duration_s = activity.get("moving_time_s") or (max(t_s) if t_s else 0)
    avg_vel    = activity.get("avg_speed_ms")

    lines = [base, "\n## Advanced Metrics"]

    # TRIMP
    trimp = trimp_from_stream(hr, duration_s, MAX_HR, REST_HR, GENDER)
    lines.append(f"TRIMP: {trimp:.1f}  (training load for this session)")

    # Aerobic decoupling
    dc = aerobic_decoupling(hr, vel)
    if dc is not None:
        lines.append(f"Aerobic decoupling: {dc:.1f}% — {interpret_decoupling(dc)}")

    # Grade Adjusted Pace
    gap_vel = grade_adjusted_pace(vel, alt, dist)
    if gap_vel and avg_vel:
        lines.append(
            f"Grade Adjusted Pace (GAP): {_pace(gap_vel)}/km  "
            f"(actual avg: {_pace(avg_vel)}/km — "
            f"{'uphill run, GAP faster' if gap_vel > avg_vel else 'downhill run, GAP slower'})"
        )

    # Karvonen HR zones
    kzones = karvonen_zone_distribution(hr, MAX_HR, REST_HR)
    if kzones:
        zone_str = "  ".join(
            f"{z.split()[0]}{z.split()[1]}: {p}%" for z, p in kzones.items()
        )
        lines.append(f"HR zones (Karvonen HRR): {zone_str}")

    # Cadence
    cad_result = cadence_analysis(cad)
    if cad_result:
        lines.append(
            f"Cadence: {cad_result['avg_spm']} spm avg  "
            f"(consistency CV {cad_result['consistency_cv_pct']}%) — {cad_result['rating']}"
        )

    # Efficiency Factor
    ef = efficiency_factor(hr, vel)
    if ef:
        lines.append(f"Efficiency Factor: {ef:.3f}  (speed÷HR×1000 — higher = better economy, track over time)")

    # Best efforts in this run
    efforts = find_best_efforts(dist, t_s)
    if efforts:
        parts = [f"{k}: {v['time_fmt']} ({v['pace']}/km)" for k, v in efforts.items()]
        lines.append(f"Best efforts in this run: {', '.join(parts)}")

    return "\n".join(lines)


async def _get_week(args: dict) -> str:
    user = args.get("user", DEFAULT_USER)
    date_str = args.get("date")

    athlete_id = _resolve_athlete_id(user)
    if not athlete_id:
        return f"User '{user}' not found."

    end_dt   = datetime.fromisoformat(date_str) if date_str else datetime.utcnow()
    start_dt = end_dt - timedelta(days=6)

    activities = get_activities_in_range(
        athlete_id,
        start_dt.isoformat(),
        (end_dt + timedelta(days=1)).isoformat(),
    )
    runs = [a for a in activities if a.get("sport_type") in ("Run", "VirtualRun", "TrailRun")]

    if not runs:
        return f"No runs found for {user} in the week ending {end_dt.date()}."

    total_km = sum((a.get("distance_m") or 0) for a in runs) / 1000
    total_s  = sum((a.get("moving_time_s") or 0) for a in runs)
    hrs      = [a["avg_heartrate"] for a in runs if a.get("avg_heartrate")]
    avg_hr   = round(statistics.mean(hrs), 1) if hrs else None
    elev     = sum((a.get("total_elevation_gain_m") or 0) for a in runs)

    # TRIMP per run
    weekly_trimp = sum(
        trimp_from_avg_hr(a.get("avg_heartrate") or 0, a.get("moving_time_s") or 0, MAX_HR, REST_HR, GENDER)
        for a in runs
    )

    # Zone distribution from streams
    all_hr_points = []
    for run in runs:
        s = get_stream(run["strava_id"])
        if s and s.get("heartrate"):
            all_hr_points.extend([h for h in s["heartrate"] if h])
    zones = zone_distribution(all_hr_points, MAX_HR) if all_hr_points else {}

    lines = [
        f"## Week ending {end_dt.date()} — {user}",
        f"Runs: {len(runs)}  |  Distance: {total_km:.1f}km  |  Time: {_fmt_duration(total_s)}",
        f"Avg HR: {avg_hr} bpm  |  Elevation: {elev:.0f}m  |  Weekly TRIMP: {weekly_trimp:.0f}",
    ]
    if zones:
        lines.append("Zone distribution: " + "  ".join(f"{z}: {p}%" for z, p in sorted(zones.items())))

    lines.append("\n### Activities")
    for a in runs:
        dist = (a.get("distance_m") or 0) / 1000
        t = trimp_from_avg_hr(a.get("avg_heartrate") or 0, a.get("moving_time_s") or 0, MAX_HR, REST_HR, GENDER)
        lines.append(
            f"- [{a['strava_id']}]  {a.get('start_date','?')[:10]}  {a.get('name','?')}  "
            f"{dist:.1f}km  {_fmt_duration(a.get('moving_time_s'))}  "
            f"HR: {a.get('avg_heartrate','?')}  Pace: {_pace(a.get('avg_speed_ms'))}/km  "
            f"TRIMP: {t:.0f}"
        )

    return "\n".join(lines)


async def _get_recent(args: dict) -> str:
    user = args.get("user", DEFAULT_USER)
    n    = int(args.get("n", 5))

    athlete_id = _resolve_athlete_id(user)
    if not athlete_id:
        return f"User '{user}' not found."

    activities = get_recent_activities(athlete_id, n=n)
    if not activities:
        return f"No activities found for {user}."

    lines = [f"## Last {len(activities)} activities — {user}", ""]
    for a in activities:
        dist = (a.get("distance_m") or 0) / 1000
        lines.append(
            f"{a.get('start_date','?')[:10]}  [{a.get('strava_id')}]  "
            f"{a.get('name','?')}  {a.get('sport_type','')}  "
            f"{dist:.1f}km  {_fmt_duration(a.get('moving_time_s'))}  "
            f"HR: {a.get('avg_heartrate','?')}  Pace: {_pace(a.get('avg_speed_ms'))}/km"
        )

    return "\n".join(lines)


async def _get_athlete_summary(args: dict) -> str:
    user = args.get("user", DEFAULT_USER)
    athlete_id = _resolve_athlete_id(user)
    if not athlete_id:
        return f"User '{user}' not found."

    stats = get_athlete_stats(athlete_id)
    lines = [
        f"## Athlete summary — {user}",
        f"Total runs: {stats['total_runs']}",
        f"Total distance: {stats['total_km']} km",
        f"Total time: {stats['total_hours']} hours",
        f"Avg HR: {stats['avg_heartrate']} bpm",
        f"Total elevation: {stats['total_elevation_gain_m']} m",
        f"Activities stored: {stats['activities_stored']}",
    ]
    return "\n".join(lines)


async def _search_activities(args: dict) -> str:
    user       = args.get("user", DEFAULT_USER)
    date_from  = args.get("date_from", "2000-01-01")
    date_to    = args.get("date_to", datetime.utcnow().date().isoformat())
    min_dist   = args.get("min_distance_km")
    max_dist   = args.get("max_distance_km")
    limit      = int(args.get("limit", 20))

    athlete_id = _resolve_athlete_id(user)
    if not athlete_id:
        return f"User '{user}' not found."

    activities = get_activities_in_range(athlete_id, date_from, date_to + "T23:59:59")
    runs = [a for a in activities if a.get("sport_type") in ("Run", "VirtualRun", "TrailRun")]

    if min_dist:
        runs = [a for a in runs if (a.get("distance_m") or 0) / 1000 >= min_dist]
    if max_dist:
        runs = [a for a in runs if (a.get("distance_m") or 0) / 1000 <= max_dist]

    runs = runs[:limit]

    if not runs:
        return f"No matching activities found for {user}."

    lines = [f"## Search results — {user} ({len(runs)} activities)"]
    for a in runs:
        dist = (a.get("distance_m") or 0) / 1000
        lines.append(
            f"[{a['strava_id']}]  {a.get('start_date','?')[:10]}  {a.get('name','?')}  "
            f"{dist:.1f}km  HR: {a.get('avg_heartrate','?')}  {_pace(a.get('avg_speed_ms'))}/km"
        )

    return "\n".join(lines)


async def _get_fitness_trend(args: dict) -> str:
    user = args.get("user", DEFAULT_USER)
    days = int(args.get("days", 120))

    athlete_id = _resolve_athlete_id(user)
    if not athlete_id:
        return f"User '{user}' not found."

    # Fetch enough history for CTL to converge (add 42 days warmup)
    end   = datetime.utcnow()
    start = end - timedelta(days=days + 42)

    activities = get_activities_in_range(athlete_id, start.isoformat(), end.isoformat())
    runs = [a for a in activities if a.get("sport_type") in ("Run", "VirtualRun", "TrailRun")]

    if not runs:
        return f"No runs found for {user} in the last {days} days."

    # Build daily TRIMP map (avg HR method — fast, one query)
    daily_loads: dict = {}
    daily_km: dict    = {}
    for run in runs:
        if not run.get("start_date"):
            continue
        day = date.fromisoformat(run["start_date"][:10])
        trimp = trimp_from_avg_hr(
            run.get("avg_heartrate") or 0,
            run.get("moving_time_s") or 0,
            MAX_HR, REST_HR, GENDER,
        )
        km = (run.get("distance_m") or 0) / 1000
        daily_loads[day] = daily_loads.get(day, 0.0) + trimp
        daily_km[day]    = daily_km.get(day, 0.0) + km

    metrics = compute_fitness_metrics(daily_loads)

    ctl = metrics["ctl"]
    atl = metrics["atl"]
    tsb = metrics["tsb"]

    lines = [
        f"## Fitness Trend — {user} (last {days} days)",
        "",
        f"Fitness (CTL):  {ctl:.1f} — {interpret_ctl(ctl)}",
        f"Fatigue (ATL):  {atl:.1f}",
        f"Form   (TSB):   {tsb:.1f} — {interpret_tsb(tsb)}",
        "",
        "CTL = 42-day exponential avg of daily TRIMP  →  aerobic fitness base",
        "ATL = 7-day exponential avg of daily TRIMP   →  current fatigue",
        "TSB = CTL - ATL  (positive = fresh, negative = fatigued)",
        "",
        "### Weekly load (TRIMP + km) — last 10 weeks",
    ]

    # Build km per week
    weekly_km: dict = {}
    for d, km in daily_km.items():
        days_until_sunday = (6 - d.weekday()) % 7
        week_end = d + timedelta(days=days_until_sunday)
        weekly_km[week_end] = weekly_km.get(week_end, 0.0) + km

    trimp_by_week = {w: v for w, v in metrics["weekly_loads"]}

    all_weeks = sorted(set(list(trimp_by_week.keys()) + [str(w) for w in weekly_km.keys()]))[-10:]
    for w_str in all_weeks:
        t = trimp_by_week.get(w_str, 0.0)
        try:
            w_date = date.fromisoformat(w_str)
            km = weekly_km.get(w_date, 0.0)
        except Exception:
            km = 0.0
        bar = "█" * min(int(t / 10), 25)
        lines.append(f"  w/e {w_str}:  {t:5.0f} TRIMP  {km:5.1f}km  {bar}")

    # Recent daily breakdown
    if metrics["daily_history"]:
        lines.append("\n### Last 14 days")
        lines.append(f"{'Date':<12} {'TRIMP':>6} {'ATL':>6} {'CTL':>6} {'TSB':>6}")
        lines.append("-" * 40)
        for d in metrics["daily_history"]:
            lines.append(
                f"{d['date']:<12} {d['trimp']:>6.1f} {d['atl']:>6.1f} "
                f"{d['ctl']:>6.1f} {d['tsb']:>+6.1f}"
            )

    return "\n".join(lines)


async def _get_best_efforts(args: dict) -> str:
    user  = args.get("user", DEFAULT_USER)
    since = args.get("since", "2000-01-01")

    athlete_id = _resolve_athlete_id(user)
    if not athlete_id:
        return f"User '{user}' not found."

    activities = get_activities_in_range(
        athlete_id, since, datetime.utcnow().isoformat()
    )
    runs = [
        a for a in activities
        if a.get("sport_type") in ("Run", "VirtualRun", "TrailRun")
    ]

    if not runs:
        return f"No runs found for {user}."

    # Best efforts: {label → {time_s, time_fmt, pace, activity_id, date, name}}
    best: dict = {}

    for run in runs:
        dist_m = run.get("distance_m") or 0

        # Only load stream if this run is long enough for at least one target distance
        min_target = min(EFFORT_DISTANCES.values())
        if dist_m < min_target * 0.9:
            continue

        stream = get_stream(run["strava_id"])
        if not stream:
            continue

        dist_series = stream.get("distance_m") or []
        time_series = stream.get("time_s") or []

        efforts = find_best_efforts(dist_series, time_series)
        for label, data in efforts.items():
            if label not in best or data["time_s"] < best[label]["time_s"]:
                best[label] = {
                    **data,
                    "activity_id": run["strava_id"],
                    "date": (run.get("start_date") or "?")[:10],
                    "name": run.get("name", "?"),
                }

    if not best:
        return f"No best efforts found for {user} (stream data may not be available for older runs)."

    since_str = f"since {since}" if since != "2000-01-01" else "all time"
    lines = [f"## Best Efforts ({since_str}) — {user}", ""]

    for label in ["1km", "5km", "10km", "half", "full"]:
        if label in best:
            b = best[label]
            lines.append(
                f"{label:<6}  {b['time_fmt']:<9} @ {b['pace']}/km"
                f"  —  {b['date']}  \"{b['name']}\"  [{b['activity_id']}]"
            )

    return "\n".join(lines)


async def _compare_runs(args: dict) -> str:
    id1 = int(args["activity_id_1"])
    id2 = int(args["activity_id_2"])

    a1 = get_activity(id1)
    a2 = get_activity(id2)
    if not a1:
        return f"Activity {id1} not found."
    if not a2:
        return f"Activity {id2} not found."

    s1 = get_stream(id1)
    s2 = get_stream(id2)

    def analyse(activity, stream):
        dur = activity.get("moving_time_s") or 0
        vel = activity.get("avg_speed_ms")
        hr_avg = activity.get("avg_heartrate")

        result = {
            "date":         (activity.get("start_date") or "?")[:10],
            "name":         activity.get("name", "?"),
            "distance_km":  round((activity.get("distance_m") or 0) / 1000, 2),
            "duration":     _fmt_duration(dur),
            "avg_pace":     _pace(vel),
            "avg_hr":       f"{hr_avg:.0f}" if hr_avg else "—",
            "elevation_m":  f"{(activity.get('total_elevation_gain_m') or 0):.0f}",
            "trimp":        "—",
            "decoupling":   "—",
            "gap_pace":     "—",
            "ef":           "—",
            "cadence":      "—",
            "zones":        {},
        }

        if not stream:
            return result

        hr_s  = stream.get("heartrate") or []
        vel_s = stream.get("velocity_ms") or []
        alt_s = stream.get("altitude_m") or []
        dst_s = stream.get("distance_m") or []
        t_s   = stream.get("time_s") or []
        cad_s = stream.get("cadence") or []

        t = trimp_from_stream(hr_s, dur, MAX_HR, REST_HR, GENDER)
        result["trimp"] = f"{t:.1f}"

        dc = aerobic_decoupling(hr_s, vel_s)
        result["decoupling"] = f"{dc:.1f}%" if dc is not None else "—"

        gap = grade_adjusted_pace(vel_s, alt_s, dst_s)
        result["gap_pace"] = _pace(gap) if gap else "—"

        ef = efficiency_factor(hr_s, vel_s)
        result["ef"] = f"{ef:.3f}" if ef else "—"

        cad = cadence_analysis(cad_s)
        result["cadence"] = f"{cad['avg_spm']} spm" if cad else "—"

        result["zones"] = zone_distribution(hr_s, MAX_HR)
        return result

    r1 = analyse(a1, s1)
    r2 = analyse(a2, s2)

    col = 22
    lines = [
        "## Run Comparison",
        "",
        f"{'Metric':<24}  {'Run 1':<{col}}  {'Run 2':<{col}}",
        f"{'─'*24}  {'─'*col}  {'─'*col}",
    ]

    rows = [
        ("Date",           "date"),
        ("Name",           "name"),
        ("Distance (km)",  "distance_km"),
        ("Duration",       "duration"),
        ("Avg Pace",       "avg_pace"),
        ("Avg HR (bpm)",   "avg_hr"),
        ("Elevation (m)",  "elevation_m"),
        ("TRIMP",          "trimp"),
        ("Decoupling",     "decoupling"),
        ("GAP",            "gap_pace"),
        ("Efficiency Factor", "ef"),
        ("Cadence",        "cadence"),
    ]
    for label, key in rows:
        v1 = str(r1.get(key, "—"))
        v2 = str(r2.get(key, "—"))
        lines.append(f"{label:<24}  {v1:<{col}}  {v2:<{col}}")

    # Zone comparison
    z1, z2 = r1["zones"], r2["zones"]
    if z1 or z2:
        lines.append(f"\n{'Zone':<24}  {'Run 1':<{col}}  {'Run 2':<{col}}")
        lines.append(f"{'─'*24}  {'─'*col}  {'─'*col}")
        for z in ["Z1", "Z2", "Z3", "Z4", "Z5"]:
            lines.append(
                f"{z:<24}  {str(z1.get(z, 0))+'%':<{col}}  {str(z2.get(z, 0))+'%':<{col}}"
            )

    return "\n".join(lines)


# ── Entry points ───────────────────────────────────────────────────────────────

async def _run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def create_sse_app():
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from mcp.server.sse import SseServerTransport

    sse_transport = SseServerTransport("/mcp/messages")

    async def handle_sse(request):
        user = request.query_params.get("user", DEFAULT_USER)
        token = _request_user.set(user)
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())
        finally:
            _request_user.reset(token)

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages", app=sse_transport.handle_post_message),
        ]
    )


def create_combined_app():
    """
    Railway deployment: combine webhook FastAPI app with MCP SSE on /mcp/*.
    Routes registered directly on FastAPI (no sub-app mount) to avoid
    double-prefix /mcp/mcp/messages bug.
    """
    from mcp.server.sse import SseServerTransport
    from starlette.requests import Request
    from strava_pipeline.webhook.app import app as fastapi_app

    sse_transport = SseServerTransport("/mcp/messages")

    @fastapi_app.get("/mcp/sse")
    async def handle_sse(request: Request):
        user = request.query_params.get("user", DEFAULT_USER)
        token = _request_user.set(user)
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())
        finally:
            _request_user.reset(token)

    @fastapi_app.post("/mcp/messages")
    async def handle_messages(request: Request):
        await sse_transport.handle_post_message(request.scope, request.receive, request._send)

    return fastapi_app


if __name__ == "__main__":
    asyncio.run(_run_stdio())
