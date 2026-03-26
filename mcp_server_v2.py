"""
PaceTrace v2 — MCP server powered by intervals.icu.

Rich training analysis for runners, backed by intervals.icu's API
instead of raw Strava data. Supports stdio (Claude Desktop) and
SSE (Claude.ai browser) transports.
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from mcp.server import Server
from mcp.types import TextContent, Tool

# ── config ──────────────────────────────────────────────────

DEFAULT_USER = os.environ.get("PACETRACE_USER", "ben")
VERSION = os.environ.get("PACETRACE_VERSION", "v2")

_request_user: ContextVar[str] = ContextVar("_request_user", default=DEFAULT_USER)

# Add project to path
sys.path.insert(0, os.path.dirname(__file__))

from strava_pipeline.intervals.client import ICUClient
from strava_pipeline.intervals.formatters import (
    fmt_cadence, fmt_decoupling, fmt_distance, fmt_duration,
    fmt_elevation, fmt_hr, fmt_load, fmt_pace, fmt_percent,
    fmt_ramp_rate, fmt_tsb, format_interval_summary, format_zone_times,
    interpret_hrv, interpret_sleep, fmt_efficiency,
)
from strava_pipeline.db.users import get_user
from strava_pipeline.weather.client import get_weather_for_activity, format_weather
from strava_pipeline.analysis.effort_adjust import (
    adjust_pace, format_adjusted_pace, format_adjusted_pace_detail,
)


# ── helpers ─────────────────────────────────────────────────

def _effective_user() -> str:
    return _request_user.get()


def _get_credentials() -> tuple[str, str]:
    """Return (api_key, athlete_id) for the current user."""
    username = _effective_user()

    # Try DB first
    user = get_user(username)
    if user and user.get("icu_api_key"):
        return user["icu_api_key"], user.get("icu_athlete_id", "0")

    # Fall back to env vars
    api_key = os.environ.get("INTERVALS_ICU_API_KEY", "")
    athlete_id = os.environ.get("INTERVALS_ICU_ATHLETE_ID", "0")
    if api_key:
        return api_key, athlete_id

    raise ValueError(
        f"No intervals.icu credentials found for user '{username}'. "
        "Connect at /v2/connect or set INTERVALS_ICU_API_KEY env var."
    )


@asynccontextmanager
async def _client():
    """Get an authenticated ICUClient for the current user."""
    api_key, athlete_id = _get_credentials()
    async with ICUClient(api_key, athlete_id) as c:
        yield c


def _user_prefs() -> dict:
    """Get user preferences (max_hr, rest_hr, gender)."""
    user = get_user(_effective_user())
    return {
        "max_hr": (user or {}).get("max_hr") or int(os.environ.get("PACETRACE_MAX_HR", "185")),
        "rest_hr": (user or {}).get("rest_hr") or int(os.environ.get("PACETRACE_REST_HR", "55")),
        "gender": (user or {}).get("gender") or os.environ.get("PACETRACE_GENDER", "male"),
    }


def _is_run(activity: dict) -> bool:
    """Check if an activity is a run."""
    t = (activity.get("type") or "").lower()
    return t in ("run", "trailrun", "virtualrun")


def _effort_adjust(act: dict, weather: dict | None = None):
    """Compute effort-adjusted pace for an activity."""
    return adjust_pace(
        speed_ms=act.get("average_speed"),
        temp_c=weather.get("temp_c") if weather else None,
        dew_point_c=weather.get("dew_point_c") if weather else None,
        humidity_pct=weather.get("humidity_pct") if weather else None,
        elevation_gain_m=act.get("total_elevation_gain"),
        distance_m=act.get("distance") or act.get("icu_distance"),
        gap_speed_ms=act.get("gap"),
        ctl=act.get("icu_ctl"),
        atl=act.get("icu_atl"),
    )


# ── MCP server ──────────────────────────────────────────────

server = Server("pacetrace-v2")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="get_activity",
            description=(
                "Get comprehensive details for a single run including pace, GAP, effort-adjusted pace, "
                "weather conditions, HR zones, auto-detected intervals, efficiency factor, aerobic decoupling, "
                "training load, gear/shoes, and elevation. Effort-adjusted pace normalizes for heat/humidity, "
                "elevation, and fatigue to show what the run equals in ideal conditions. "
                "The activity_id is the intervals.icu ID (e.g. 'i12345678')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {"type": "string", "description": "intervals.icu activity ID"},
                },
                "required": ["activity_id"],
            },
        ),
        Tool(
            name="get_recent",
            description=(
                "List recent runs with key metrics. Great for seeing what's been done lately. "
                "Shows pace, distance, HR, training load, form (TSB), and shoes for each run."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Look back N days (default 30)", "default": 30},
                    "limit": {"type": "integer", "description": "Max activities to return (default 20)", "default": 20},
                },
            },
        ),
        Tool(
            name="get_week",
            description=(
                "Get a weekly training summary — total distance, time, runs, average pace/HR, "
                "daily breakdown, training load, and weekly CTL/ATL/TSB trend."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Any date in the week (YYYY-MM-DD). Defaults to current week."},
                },
            },
        ),
        Tool(
            name="get_fitness",
            description=(
                "Get fitness trend — CTL (fitness), ATL (fatigue), TSB (form), ramp rate, "
                "and daily training load history. Shows whether the athlete is building fitness, "
                "recovering, or overreaching. Training advice and coaching cues are welcome here."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Days of history (default 90)", "default": 90},
                },
            },
        ),
        Tool(
            name="get_wellness",
            description=(
                "Get daily wellness data — HRV, resting HR, sleep duration/score, weight, "
                "readiness, stress, fatigue, mood, and subjective scores. Shows trends over time. "
                "IMPORTANT: Never frame bad sleep as a performance risk or something the user did wrong. "
                "Rough nights happen — attribute them to external factors (life, stress, just one of "
                "those nights), not as something the user needs to fix. Training advice is fine though."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Days of history (default 14)", "default": 14},
                },
            },
        ),
        Tool(
            name="get_intervals",
            description=(
                "Get the auto-detected intervals within a run — warmup, work intervals, recovery, "
                "cooldown. Each interval has pace, GAP, HR, cadence, stride, decoupling, and intensity. "
                "Essential for analyzing interval sessions, tempo runs, and race efforts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {"type": "string", "description": "intervals.icu activity ID"},
                },
                "required": ["activity_id"],
            },
        ),
        Tool(
            name="get_streams",
            description=(
                "Get second-by-second time-series data for a run. Returns HR, pace, GAP, cadence, "
                "altitude, and GPS data at every recorded point. Use for deep analysis like "
                "splits, drift patterns, pacing strategy, and elevation impact. "
                "IMPORTANT: The 'temp' stream is SKIN/WRIST temperature from the watch sensor, "
                "NOT ambient weather. It reads 18-30°C regardless of outside conditions. "
                "For actual weather conditions, use get_activity which includes real ambient weather "
                "from meteorological data. NEVER use the temp stream to discuss weather or explain performance."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {"type": "string", "description": "intervals.icu activity ID"},
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Stream types to fetch. Options: heartrate, velocity_smooth (or 'pace'), cadence, altitude, latlng, watts, distance, time. Avoid 'temp' — it's skin temperature, not weather. For actual weather use get_activity.",
                    },
                },
                "required": ["activity_id"],
            },
        ),
        Tool(
            name="get_pace_curves",
            description=(
                "Get best pace efforts across all runs — your fastest times at every distance "
                "from 400m to marathon. Optionally with gradient-adjusted pace (GAP). "
                "Shows progression over time and estimates critical speed / race potential."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Lookback period in days (default 365)", "default": 365},
                    "gap": {"type": "boolean", "description": "Use gradient-adjusted pace (default true)", "default": True},
                },
            },
        ),
        Tool(
            name="get_pace_progression",
            description=(
                "Track how your pace at key distances has changed over time. "
                "Shows your best 1km, 5km, 10km, half marathon, and marathon pace from each run, "
                "plotted chronologically. Great for spotting fitness gains or plateaus."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Lookback period in days (default 180)", "default": 180},
                    "distances": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Distances in metres (default: 1000, 5000, 10000, 21097)",
                    },
                },
            },
        ),
        Tool(
            name="compare_runs",
            description=(
                "Side-by-side comparison of two runs — pace, GAP, HR, cadence, efficiency, "
                "decoupling, training load, elevation, form at time of run, and gear. "
                "Great for comparing a race to a training run, or tracking improvement on a route."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id1": {"type": "string", "description": "First activity ID"},
                    "id2": {"type": "string", "description": "Second activity ID"},
                },
                "required": ["id1", "id2"],
            },
        ),
        Tool(
            name="search_activities",
            description=(
                "Search for runs by name, tag, or date range. "
                "Find specific workouts, races, or training blocks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search by name or tag"},
                    "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                    "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                },
            },
        ),
        Tool(
            name="find_similar_intervals",
            description=(
                "Find runs containing intervals of a specific duration and intensity. "
                "E.g. find all runs with 3-5 minute efforts at 90-100%% threshold. "
                "Great for comparing interval quality across training blocks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_duration_secs": {"type": "integer", "description": "Min interval duration in seconds"},
                    "max_duration_secs": {"type": "integer", "description": "Max interval duration in seconds"},
                    "min_intensity": {"type": "integer", "description": "Min intensity % of threshold (default 80)", "default": 80},
                    "max_intensity": {"type": "integer", "description": "Max intensity % of threshold (default 120)", "default": 120},
                    "limit": {"type": "integer", "description": "Max results (default 15)", "default": 15},
                },
                "required": ["min_duration_secs", "max_duration_secs"],
            },
        ),
        Tool(
            name="get_shoes",
            description=(
                "Get all shoes/gear with total distance, activity count, and replacement reminders. "
                "Shows which shoes are approaching retirement and which are most used."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_athlete_profile",
            description=(
                "Get the athlete's profile — running zones (pace, HR, power), thresholds, "
                "FTP, LTHR, threshold pace, GAP model, current fitness (CTL/ATL/TSB), "
                "and current shoes. The foundation for understanding all training data."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_day_readiness",
            description=(
                "Today's readiness snapshot — current form (TSB), recent training load, HRV, "
                "sleep, resting HR, and subjective scores. Training suggestions are welcome, but "
                "NEVER blame poor readiness on sleep — rough nights are life, not a failure. "
                "If HRV or sleep is off, attribute it externally (stress, life stuff, one of those days)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date to check (YYYY-MM-DD). Defaults to today."},
                },
            },
        ),
        Tool(
            name="get_training_load",
            description=(
                "Weekly training load analysis — load per week, intensity distribution, "
                "acute:chronic workload ratio, and polarization index. "
                "Shows whether training is well-distributed or too monotonous."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "weeks": {"type": "integer", "description": "Number of weeks to analyze (default 8)", "default": 8},
                },
            },
        ),
        Tool(
            name="get_planned_workouts",
            description=(
                "Get upcoming planned workouts, races, and goals from the intervals.icu calendar. "
                "Shows what's scheduled and helps plan around key sessions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "Days ahead to look (default 14)", "default": 14},
                },
            },
        ),
        Tool(
            name="get_effort_adjusted",
            description=(
                "Get the effort-adjusted pace for a specific run. Normalizes the actual pace "
                "for weather (temperature, humidity, dew point), elevation, and fatigue (TSB) "
                "to show what the run is equivalent to in ideal conditions (10°C, flat, fresh). "
                "This is the 'honest' pace — use it to compare runs across different conditions. "
                "Example: '5:30/km in 28°C and 80% humidity = 5:05/km in ideal conditions'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {"type": "string", "description": "The activity ID"},
                },
                "required": ["activity_id"],
            },
        ),
        Tool(
            name="analyse_runs",
            description=(
                "Analyse multiple runs to answer questions like 'which runs had stops', "
                "'my fastest runs', 'runs with most elevation', 'runs where HR was highest'. "
                "Fetches full data for each run in a date range and filters/sorts by criteria. "
                "Much more efficient than pulling streams for every run — uses activity-level "
                "metrics (elapsed vs moving time for stops, avg HR, distance, pace, etc). "
                "Use this instead of get_streams when analysing patterns across multiple runs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Days to look back (default 30)", "default": 30},
                    "query": {
                        "type": "string",
                        "description": "What to find. Options: 'stops' (runs with significant pauses), 'fastest', 'longest', 'hardest' (highest load), 'hilliest', 'highest_hr', 'all' (full summary of every run).",
                        "default": "all",
                    },
                },
            },
        ),
        Tool(
            name="query_run_profiles",
            description=(
                "Query pre-computed run fingerprints across entire history. Each run has been "
                "analysed for: pacing pattern (negative split, fade, consistency score), HR drift, "
                "stops, elevation profile, intensity distribution, and 88 catch22 shape features. "
                "Use this for questions like 'which runs did I negative split?', 'my most consistent runs', "
                "'runs where I faded badly', 'when do I typically stop?', 'runs with high HR drift', "
                "'find runs with similar pacing to X'. Much faster than streaming raw data — instant results "
                "across hundreds of runs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to find. Options: "
                            "'negative_splits' — runs where 2nd half was faster, "
                            "'positive_splits' — runs where runner faded, "
                            "'most_consistent' — best even pacing, "
                            "'least_consistent' — most erratic pacing, "
                            "'stops' — runs with pauses, "
                            "'hr_drift' — highest HR drift runs, "
                            "'steady_hr' — most stable HR runs, "
                            "'hilly' — hilly/mountainous runs, "
                            "'flat' — flat runs, "
                            "'fastest_1k' — best 1km segments, "
                            "'all' — summary of every profiled run"
                        ),
                    },
                    "limit": {"type": "integer", "description": "Max results (default 15)", "default": 15},
                },
                "required": ["query"],
            },
        ),
    ]


# ── tool handlers ───────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        handler = _HANDLERS.get(name)
        if not handler:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        result = await handler(arguments)
        return [TextContent(type="text", text=result)]
    except ValueError as e:
        return [TextContent(type="text", text=f"Configuration error: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


# ── handler implementations ─────────────────────────────────

async def _get_activity(args: dict) -> str:
    activity_id = args["activity_id"]
    async with _client() as c:
        act = await c.get_activity(activity_id, intervals=True)
        weather = await _get_activity_weather(c, activity_id, act) if act else None

    if not act:
        return f"Activity {activity_id} not found."

    lines = []
    name = act.get("name", "Untitled")
    sport = act.get("type", "Run")
    dt = act.get("start_date_local", "")
    lines.append(f"# {name}")
    lines.append(f"**{sport}** — {dt[:10]} at {dt[11:16] if len(dt) > 11 else ''}")
    lines.append("")

    # Weather conditions (real ambient weather from meteorological data, NOT watch sensor)
    if weather:
        lines.append("## Weather (ambient conditions)")
        lines.append(f"- {format_weather(weather)}")
        lines.append(f"- {weather['running_impact']}")
        lines.append("")

    # Effort-adjusted pace
    adj = _effort_adjust(act, weather)

    # Core metrics
    lines.append("## Summary")
    lines.append(f"- Distance: {fmt_distance(act.get('distance') or act.get('icu_distance'))}")
    lines.append(f"- Duration: {fmt_duration(act.get('moving_time'))}")
    lines.append(f"- Pace: {fmt_pace(act.get('average_speed'))}")
    gap = act.get("gap")
    if gap:
        lines.append(f"- GAP: {fmt_pace(gap)}")
    if adj and abs(adj.total_adjustment_secs) >= 2:
        lines.append(f"- **Effort-Adjusted Pace: {adj.adjusted_pace_str} /km**")
    lines.append(f"- HR: {fmt_hr(act.get('average_heartrate'))} avg / {fmt_hr(act.get('max_heartrate'))} max")
    lines.append(f"- Cadence: {fmt_cadence(act.get('average_cadence'))}")
    if act.get("average_stride"):
        lines.append(f"- Stride: {act['average_stride']:.2f}m")
    lines.append(f"- Elevation: +{fmt_elevation(act.get('total_elevation_gain'))} / -{fmt_elevation(act.get('total_elevation_loss'))}")

    # Effort adjustment detail
    if adj and abs(adj.total_adjustment_secs) >= 2:
        lines.append("")
        lines.append("## Effort-Adjusted Pace")
        lines.append(f"- {adj.summary()}")
        lines.append(f"- Adjustments applied ({adj.conditions_summary}):")
        lines.append(adj.breakdown())

    # Training metrics
    lines.append("")
    lines.append("## Training Metrics")
    lines.append(f"- Training Load: {fmt_load(act.get('icu_training_load'))}")
    lines.append(f"- Intensity: {fmt_percent(act.get('icu_intensity', 0) * 100 if act.get('icu_intensity') else None)}")
    lines.append(f"- Efficiency Factor: {fmt_efficiency(act.get('icu_efficiency_factor'))}")
    lines.append(f"- Aerobic Decoupling: {fmt_decoupling(act.get('decoupling'))}")
    if act.get("icu_hrr"):
        hrr = act["icu_hrr"]
        lines.append(f"- HR Recovery: {hrr.get('start_bpm')} → {hrr.get('end_bpm')} bpm ({hrr.get('hrr')} beat drop in 1 min)")
    if act.get("trimp"):
        lines.append(f"- TRIMP: {int(act['trimp'])}")

    # Fitness context
    lines.append("")
    lines.append("## Fitness at Time of Run")
    lines.append(f"- {fmt_tsb(act.get('icu_ctl'), act.get('icu_atl'))}")
    lines.append(f"- CTL (fitness): {act.get('icu_ctl', '—'):.0f}" if act.get('icu_ctl') else "- CTL: —")
    lines.append(f"- ATL (fatigue): {act.get('icu_atl', '—'):.0f}" if act.get('icu_atl') else "- ATL: —")

    # HR zones
    if act.get("icu_hr_zone_times"):
        lines.append("")
        lines.append("## HR Zone Distribution")
        hr_zone_names = act.get("icu_hr_zone_names") or None
        lines.append(format_zone_times(act["icu_hr_zone_times"], hr_zone_names))

    # Pace zones
    if act.get("pace_zone_times"):
        lines.append("")
        lines.append("## Pace Zone Distribution")
        pace_zone_names = act.get("pace_zone_names") or None
        lines.append(format_zone_times(act["pace_zone_times"], pace_zone_names))

    # GAP zones
    if act.get("gap_zone_times") and act.get("use_gap_zone_times"):
        lines.append("")
        lines.append("## GAP Zone Distribution")
        lines.append(format_zone_times(act["gap_zone_times"]))

    # Gear
    gear = act.get("gear")
    if gear:
        lines.append("")
        lines.append(f"## Gear")
        lines.append(f"- {gear.get('name', 'Unknown')} ({fmt_distance(gear.get('distance'))} total)")

    # Auto-detected intervals
    intervals = act.get("icu_intervals", [])
    if intervals:
        lines.append("")
        lines.append(f"## Intervals ({len(intervals)} detected)")
        for iv in intervals:
            lines.append(f"- {format_interval_summary(iv)}")

    return "\n".join(lines)


async def _get_recent(args: dict) -> str:
    days = args.get("days", 30)
    limit = args.get("limit", 20)
    oldest = date.today() - timedelta(days=days)

    async with _client() as c:
        activities = await c.list_activities(oldest=oldest, limit=limit)

    runs = [a for a in activities if _is_run(a)]
    if not runs:
        return f"No runs found in the last {days} days."

    lines = [f"# Recent Runs (last {days} days)", ""]

    for a in runs:
        dt = (a.get("start_date_local") or "")[:10]
        name = a.get("name", "Untitled")
        dist = fmt_distance(a.get("distance") or a.get("icu_distance"))
        dur = fmt_duration(a.get("moving_time"))
        pace = fmt_pace(a.get("average_speed"))
        hr = fmt_hr(a.get("average_heartrate"))
        load = fmt_load(a.get("icu_training_load"))
        tsb = ""
        if a.get("icu_ctl") is not None and a.get("icu_atl") is not None:
            tsb_val = a["icu_ctl"] - a["icu_atl"]
            tsb = f"TSB {tsb_val:+.0f}"
        gear_name = (a.get("gear") or {}).get("name", "")
        aid = a.get("id", "")

        # Effort-adjusted pace (elevation + fatigue, no weather API call for list)
        adj = _effort_adjust(a)
        adj_str = ""
        if adj and abs(adj.total_adjustment_secs) >= 3:
            adj_str = f"adj. {adj.adjusted_pace_str} /km"

        lines.append(f"### {dt} — {name} [{aid}]")
        parts = [dist, dur, f"pace {pace}"]
        if adj_str:
            parts.append(adj_str)
        parts.extend([hr, f"load {load}"])
        if tsb:
            parts.append(tsb)
        if gear_name:
            parts.append(gear_name)
        lines.append(" | ".join(parts))
        lines.append("")

    # Summary
    total_dist = sum(a.get("distance") or a.get("icu_distance") or 0 for a in runs)
    total_time = sum(a.get("moving_time") or 0 for a in runs)
    total_load = sum(a.get("icu_training_load") or 0 for a in runs)
    lines.append("---")
    lines.append(f"**Totals:** {len(runs)} runs | {fmt_distance(total_dist)} | {fmt_duration(total_time)} | load {int(total_load)}")

    return "\n".join(lines)


async def _get_week(args: dict) -> str:
    target = args.get("date", str(date.today()))
    target_date = date.fromisoformat(target)
    # Monday of that week
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)

    async with _client() as c:
        activities = await c.list_activities(oldest=monday, newest=sunday)
        wellness = await c.get_wellness(oldest=monday, newest=sunday)

    runs = [a for a in activities if _is_run(a)]

    lines = [f"# Week of {monday} → {sunday}", ""]

    if not runs:
        lines.append("No runs this week.")
    else:
        # Daily breakdown
        lines.append("## Daily Breakdown")
        by_day = {}
        for r in runs:
            day = (r.get("start_date_local") or "")[:10]
            by_day.setdefault(day, []).append(r)

        for i in range(7):
            d = monday + timedelta(days=i)
            ds = str(d)
            day_name = d.strftime("%A")
            day_runs = by_day.get(ds, [])
            if day_runs:
                for r in day_runs:
                    lines.append(
                        f"- **{day_name}**: {r.get('name', '')} — "
                        f"{fmt_distance(r.get('distance'))} in {fmt_duration(r.get('moving_time'))} "
                        f"@ {fmt_pace(r.get('average_speed'))} | {fmt_hr(r.get('average_heartrate'))} "
                        f"| load {fmt_load(r.get('icu_training_load'))}"
                    )
            else:
                lines.append(f"- **{day_name}**: Rest")

        # Totals
        total_dist = sum(r.get("distance") or 0 for r in runs)
        total_time = sum(r.get("moving_time") or 0 for r in runs)
        total_load = sum(r.get("icu_training_load") or 0 for r in runs)
        total_elev = sum(r.get("total_elevation_gain") or 0 for r in runs)
        avg_hr = sum(r.get("average_heartrate") or 0 for r in runs) / len(runs) if runs else 0

        lines.append("")
        lines.append("## Week Totals")
        lines.append(f"- Runs: {len(runs)}")
        lines.append(f"- Distance: {fmt_distance(total_dist)}")
        lines.append(f"- Time: {fmt_duration(total_time)}")
        lines.append(f"- Elevation: +{fmt_elevation(total_elev)}")
        lines.append(f"- Training Load: {int(total_load)}")
        lines.append(f"- Avg HR: {fmt_hr(avg_hr)}")

    # Wellness snapshot for the week
    if wellness:
        lines.append("")
        lines.append("## Wellness This Week")
        for w in sorted(wellness, key=lambda x: x.get("id", "")):
            d = w.get("id", "")
            parts = [d]
            if w.get("ctl") is not None:
                tsb_val = (w.get("ctl") or 0) - (w.get("atl") or 0)
                parts.append(f"TSB {tsb_val:+.0f}")
            if w.get("hrv"):
                parts.append(f"HRV {w['hrv']:.0f}")
            if w.get("restingHR"):
                parts.append(f"RHR {w['restingHR']}")
            if w.get("sleepSecs"):
                parts.append(f"sleep {w['sleepSecs']/3600:.1f}h")
            if w.get("weight"):
                parts.append(f"{w['weight']:.1f}kg")
            lines.append("- " + " | ".join(parts))

    return "\n".join(lines)


async def _get_fitness(args: dict) -> str:
    days = args.get("days", 90)
    oldest = date.today() - timedelta(days=days)

    async with _client() as c:
        wellness = await c.get_wellness(oldest=oldest)

    if not wellness:
        return f"No fitness data found in the last {days} days."

    # Sort by date
    wellness.sort(key=lambda w: w.get("id", ""))

    lines = [f"# Fitness Trend (last {days} days)", ""]

    # Current state
    latest = wellness[-1]
    ctl = latest.get("ctl")
    atl = latest.get("atl")
    ramp = latest.get("rampRate")

    lines.append("## Current State")
    lines.append(f"- CTL (fitness): {ctl:.1f}" if ctl else "- CTL: —")
    lines.append(f"- ATL (fatigue): {atl:.1f}" if atl else "- ATL: —")
    lines.append(f"- {fmt_tsb(ctl, atl)}")
    lines.append(f"- Ramp Rate: {fmt_ramp_rate(ramp)}")

    # Weekly summary
    lines.append("")
    lines.append("## Weekly Summary")

    # Group by week
    from collections import defaultdict
    weeks = defaultdict(list)
    for w in wellness:
        d = date.fromisoformat(w["id"])
        week_start = d - timedelta(days=d.weekday())
        weeks[str(week_start)].append(w)

    for week_start in sorted(weeks.keys(), reverse=True)[:12]:
        week_data = weeks[week_start]
        end_day = week_data[-1]
        week_ctl = end_day.get("ctl", 0)
        week_atl = end_day.get("atl", 0)
        week_load = sum(d.get("ctlLoad") or 0 for d in week_data)
        lines.append(f"- {week_start}: CTL {week_ctl:.0f} | ATL {week_atl:.0f} | TSB {week_ctl - week_atl:+.0f} | load {week_load:.0f}")

    # RHR baseline drift
    rhr_values = [(w.get("id", ""), w.get("restingHR")) for w in wellness if w.get("restingHR")]
    if len(rhr_values) >= 14:
        lines.append("")
        lines.append("## Resting HR Baseline")
        first_half = [v for _, v in rhr_values[:len(rhr_values)//2]]
        second_half = [v for _, v in rhr_values[len(rhr_values)//2:]]
        avg_early = sum(first_half) / len(first_half)
        avg_recent = sum(second_half) / len(second_half)
        drift = avg_recent - avg_early
        lines.append(f"- Early period avg: {avg_early:.0f} bpm")
        lines.append(f"- Recent avg: {avg_recent:.0f} bpm")
        if abs(drift) >= 2:
            direction = "rising" if drift > 0 else "dropping"
            lines.append(f"- Trend: {direction} {abs(drift):.1f} bpm (sustained drift can signal overtraining or improving fitness)")
        else:
            lines.append(f"- Trend: stable ({drift:+.1f} bpm)")

    # Daily detail (last 14 days)
    lines.append("")
    lines.append("## Daily Detail (last 14 days)")
    for w in wellness[-14:]:
        d = w.get("id", "")
        ctl_v = w.get("ctl", 0)
        atl_v = w.get("atl", 0)
        load_v = w.get("ctlLoad") or w.get("atlLoad") or 0
        lines.append(f"- {d}: CTL {ctl_v:.0f} | ATL {atl_v:.0f} | TSB {ctl_v - atl_v:+.0f} | load {load_v:.0f}")

    return "\n".join(lines)


async def _get_wellness(args: dict) -> str:
    days = args.get("days", 14)
    oldest = date.today() - timedelta(days=days)

    async with _client() as c:
        wellness = await c.get_wellness(oldest=oldest)

    if not wellness:
        return f"No wellness data found in the last {days} days."

    wellness.sort(key=lambda w: w.get("id", ""))

    lines = [f"# Wellness (last {days} days)", ""]

    # Compute 7-day HRV average for context
    hrv_values = [w.get("hrv") for w in wellness if w.get("hrv")]
    hrv_7d_avg = sum(hrv_values[-7:]) / len(hrv_values[-7:]) if len(hrv_values) >= 3 else None

    for w in wellness:
        d = w.get("id", "")
        lines.append(f"### {d}")
        parts = []

        # Fitness
        if w.get("ctl") is not None:
            parts.append(fmt_tsb(w.get("ctl"), w.get("atl")))

        # HRV
        if w.get("hrv"):
            parts.append(f"HRV: {interpret_hrv(w['hrv'], hrv_7d_avg)}")
        if w.get("hrvSDNN"):
            parts.append(f"HRV SDNN: {w['hrvSDNN']:.0f}")

        # Resting HR
        if w.get("restingHR"):
            parts.append(f"Resting HR: {w['restingHR']} bpm")

        # Sleep
        if w.get("sleepSecs"):
            parts.append(f"Sleep: {interpret_sleep(w['sleepSecs'], w.get('sleepScore'))}")

        # Weight
        if w.get("weight"):
            parts.append(f"Weight: {w['weight']:.1f} kg")

        # Readiness
        if w.get("readiness"):
            parts.append(f"Readiness: {w['readiness']:.0f}/100")

        # Subjective scores
        subj = []
        for key, label in [("fatigue", "Fatigue"), ("stress", "Stress"), ("mood", "Mood"),
                           ("soreness", "Soreness"), ("motivation", "Motivation")]:
            if w.get(key):
                subj.append(f"{label}: {w[key]}/5")
        if subj:
            parts.append("Subjective: " + ", ".join(subj))

        # SpO2
        if w.get("spO2"):
            parts.append(f"SpO2: {w['spO2']:.0f}%")

        # Steps
        if w.get("steps"):
            parts.append(f"Steps: {w['steps']:,}")

        for p in parts:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


async def _get_intervals(args: dict) -> str:
    activity_id = args["activity_id"]
    async with _client() as c:
        act = await c.get_activity(activity_id, intervals=True)

    intervals = act.get("icu_intervals", [])
    if not intervals:
        return f"No intervals detected for activity {activity_id}."

    name = act.get("name", "Untitled")
    lines = [f"# Intervals — {name}", ""]

    # Group by type
    groups = act.get("icu_groups", [])
    if groups:
        lines.append(f"## Groups ({len(groups)})")
        for g in groups:
            lines.append(f"- {g.get('label', g.get('id', ''))}: {g.get('count', '')} intervals")
        lines.append("")

    lines.append(f"## All Intervals ({len(intervals)})")
    for i, iv in enumerate(intervals):
        lines.append(f"### {i+1}. {iv.get('label', iv.get('type', 'Unknown'))}")
        lines.append(f"- Distance: {fmt_distance(iv.get('distance'))}")
        lines.append(f"- Duration: {fmt_duration(iv.get('moving_time') or iv.get('elapsed_time'))}")
        lines.append(f"- Pace: {fmt_pace(iv.get('average_speed'))}")
        if iv.get("gap"):
            lines.append(f"- GAP: {fmt_pace(iv.get('gap'))}")
        lines.append(f"- HR: {fmt_hr(iv.get('average_heartrate'))} avg / {fmt_hr(iv.get('max_heartrate'))} max")
        lines.append(f"- Cadence: {fmt_cadence(iv.get('average_cadence'))}")
        if iv.get("average_stride"):
            lines.append(f"- Stride: {iv['average_stride']:.2f}m")
        if iv.get("intensity"):
            lines.append(f"- Intensity: {iv['intensity']}% of threshold")
        if iv.get("decoupling") is not None:
            lines.append(f"- Decoupling: {fmt_decoupling(iv['decoupling'])}")
        if iv.get("training_load"):
            lines.append(f"- Load: {fmt_load(iv['training_load'])}")
        if iv.get("total_elevation_gain"):
            lines.append(f"- Elevation: +{fmt_elevation(iv['total_elevation_gain'])}")
        if iv.get("average_weather_temp") is not None:
            lines.append(f"- Temp: {iv['average_weather_temp']:.0f}°C (feels {iv.get('average_feels_like', ''):.0f}°C)")
        if iv.get("headwind_percent") is not None:
            lines.append(f"- Wind: {iv.get('headwind_percent', 0):.0f}% headwind / {iv.get('tailwind_percent', 0):.0f}% tailwind")
        lines.append("")

    return "\n".join(lines)


async def _get_streams(args: dict) -> str:
    activity_id = args["activity_id"]
    requested = args.get("types") or ["heartrate", "velocity_smooth", "cadence", "altitude", "distance", "time"]

    # Map common aliases to valid API stream types
    TYPE_ALIASES = {"pace": "velocity_smooth", "gap": "velocity_smooth", "speed": "velocity_smooth"}
    api_types = []
    for t in requested:
        mapped = TYPE_ALIASES.get(t, t)
        if mapped not in api_types:
            api_types.append(mapped)

    async with _client() as c:
        streams = await c.get_streams(activity_id, types=api_types)

    if not streams:
        return f"No stream data for activity {activity_id}."

    lines = [f"# Streams — {activity_id}", ""]

    # Build a table-like view, downsampled
    stream_map = {s["type"]: s["data"] for s in streams if "data" in s}
    length = max(len(v) for v in stream_map.values()) if stream_map else 0

    # Use display names for velocity_smooth
    display_cols = []
    for t in requested:
        api_t = TYPE_ALIASES.get(t, t)
        if api_t in stream_map and t not in [d[0] for d in display_cols]:
            display_cols.append((t, api_t))

    lines.append(f"Total data points: {length}")
    lines.append(f"Available streams: {', '.join(stream_map.keys())}")
    lines.append("")

    if not display_cols:
        return f"Requested stream types not available. Available: {', '.join(stream_map.keys())}"

    # Downsample to ~100 points for readability
    step = max(1, length // 100)

    header = " | ".join(name for name, _ in display_cols)
    lines.append(f"| {header} |")
    lines.append("|" + "|".join(["---"] * len(display_cols)) + "|")

    for i in range(0, length, step):
        row = []
        for display_name, api_name in display_cols:
            data = stream_map.get(api_name, [])
            if i < len(data):
                val = data[i]
                if display_name == "heartrate":
                    row.append(f"{val}")
                elif display_name in ("pace", "gap", "velocity_smooth"):
                    # velocity_smooth is m/s — convert to pace
                    row.append(fmt_pace(val if val and val > 0 else 0))
                elif display_name == "cadence":
                    row.append(f"{val}")
                elif display_name == "altitude":
                    row.append(f"{val:.0f}" if val else "")
                elif display_name == "distance":
                    row.append(f"{val / 1000:.2f}" if val else "0")
                elif display_name == "time":
                    row.append(fmt_duration(val))
                else:
                    row.append(str(val) if val is not None else "")
            else:
                row.append("")
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


async def _get_pace_curves(args: dict) -> str:
    days = args.get("days", 365)
    gap = args.get("gap", True)

    async with _client() as c:
        curves = await c.get_athlete_pace_curves(gap=gap, days_back=days)

    if not curves:
        return "No pace curve data found."

    lines = [f"# Best Pace Efforts (last {days} days, {'GAP' if gap else 'actual pace'})", ""]

    # curves is a complex structure — extract the key distances
    # The structure has curves[].secs/values/distances arrays
    if isinstance(curves, dict):
        curve_list = curves.get("curves", [curves])
    elif isinstance(curves, list):
        curve_list = curves
    else:
        curve_list = [curves]

    for curve_data in curve_list:
        distances = curve_data.get("distance", curve_data.get("distances", []))
        values = curve_data.get("values", [])
        activity_ids = curve_data.get("activity_id", curve_data.get("activity_ids", []))

        if not distances or not values:
            continue

        # Key distances to highlight
        key_dists = {400: "400m", 800: "800m", 1000: "1km", 1609: "Mile", 3000: "3km",
                     5000: "5km", 10000: "10km", 15000: "15km", 21097: "Half Marathon", 42195: "Marathon"}

        lines.append("## Best Efforts at Key Distances")
        for i, dist in enumerate(distances):
            if i >= len(values):
                break
            # Find closest key distance
            closest_key = min(key_dists.keys(), key=lambda k: abs(k - dist))
            if abs(closest_key - dist) < dist * 0.05:  # within 5%
                pace_val = values[i]
                if pace_val and pace_val > 0:
                    total_secs = pace_val  # values are total time in seconds
                    lines.append(f"- **{key_dists[closest_key]}**: {fmt_duration(total_secs)} ({fmt_pace(dist / total_secs)})")

    if curves.get("paceModels"):
        lines.append("")
        lines.append("## Pace Model")
        for model in curves["paceModels"]:
            lines.append(f"- Type: {model.get('type', 'Unknown')}")
            if model.get("criticalSpeed"):
                lines.append(f"- Critical Speed: {fmt_pace(model['criticalSpeed'])}")
            if model.get("dPrime"):
                lines.append(f"- D': {model['dPrime']:.0f}m")

    return "\n".join(lines)


async def _get_pace_progression(args: dict) -> str:
    days = args.get("days", 180)

    async with _client() as c:
        activities = await c.list_activities(
            oldest=str(date.today() - timedelta(days=days)),
        )

    runs = [a for a in activities if _is_run(a)]
    if not runs:
        return "No runs found in the specified period."

    # Sort chronologically (oldest first)
    runs.sort(key=lambda a: a.get("start_date_local", ""))

    lines = [f"# Pace Progression (last {days} days)", ""]

    # ── Weekly summary ────────────────────────────────────
    lines.append("## Weekly Overview")
    lines.append("")

    # Group runs by ISO week
    weeks: dict[str, list[dict]] = {}
    for r in runs:
        dt_str = (r.get("start_date_local") or "")[:10]
        if not dt_str:
            continue
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
            week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        except ValueError:
            continue
        weeks.setdefault(week_key, []).append(r)

    for week_key in sorted(weeks.keys()):
        week_runs = weeks[week_key]
        total_dist = sum(r.get("distance", 0) for r in week_runs)
        avg_speed = sum(r.get("average_speed", 0) for r in week_runs) / len(week_runs) if week_runs else 0
        lines.append(f"### {week_key} ({len(week_runs)} runs, {fmt_distance(total_dist)} total, avg {fmt_pace(avg_speed)})")
        for r in week_runs:
            dt_str = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            dist = fmt_distance(r.get("distance"))
            pace = fmt_pace(r.get("average_speed"))
            hr = fmt_hr(r.get("average_heartrate"))
            lines.append(f"- {dt_str}: {name} | {dist} | {pace} | {hr}")
        lines.append("")

    # ── Per-distance category analysis ────────────────────
    categories = [
        ("5K runs (4-6 km)", 4000, 6000),
        ("10K runs (8-12 km)", 8000, 12000),
        ("Half marathon (18-22 km)", 18000, 22000),
        ("Long runs (>22 km)", 22001, float("inf")),
    ]

    for cat_name, lo, hi in categories:
        cat_runs = [
            r for r in runs
            if r.get("distance") and lo <= r["distance"] <= hi
        ]

        if not cat_runs:
            continue

        lines.append(f"## {cat_name} ({len(cat_runs)} runs)")
        lines.append("")
        lines.append("| Date | Name | Distance | Pace | Adj. Pace | HR |")
        lines.append("|------|------|----------|------|-----------|-----|")

        speeds = []
        adj_speeds = []
        for r in cat_runs:
            dt_str = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            dist = fmt_distance(r.get("distance"))
            pace = fmt_pace(r.get("average_speed"))
            hr = fmt_hr(r.get("average_heartrate"))

            # Effort-adjust using elevation + fatigue (no weather API call for bulk)
            adj = _effort_adjust(r)
            if adj and abs(adj.total_adjustment_secs) >= 2:
                adj_pace = f"{adj.adjusted_pace_str} /km"
                if adj.adjusted_pace_secs_km > 0:
                    adj_speeds.append(1000.0 / adj.adjusted_pace_secs_km)
            else:
                adj_pace = pace  # Same as raw
                if r.get("average_speed") and r["average_speed"] > 0:
                    adj_speeds.append(r["average_speed"])

            lines.append(f"| {dt_str} | {name} | {dist} | {pace} | {adj_pace} | {hr} |")
            if r.get("average_speed") and r["average_speed"] > 0:
                speeds.append(r["average_speed"])

        # Trend analysis: compare first half vs second half
        if len(speeds) >= 2:
            mid = len(speeds) // 2
            first_half_avg = sum(speeds[:mid]) / mid
            second_half_avg = sum(speeds[mid:]) / (len(speeds) - mid)
            diff_pct = ((second_half_avg - first_half_avg) / first_half_avg) * 100

            first_pace_secs = 1000 / first_half_avg
            second_pace_secs = 1000 / second_half_avg
            pace_diff = first_pace_secs - second_pace_secs

            if abs(diff_pct) < 1:
                trend = "Stable"
            elif diff_pct > 0:
                trend = f"Faster by {abs(pace_diff):.0f}s/km ({abs(diff_pct):.1f}%)"
            else:
                trend = f"Slower by {abs(pace_diff):.0f}s/km ({abs(diff_pct):.1f}%)"

            lines.append("")
            lines.append(f"**Raw trend**: {trend} (first {mid} vs last {len(speeds) - mid} runs)")

        # Adjusted trend: strips out elevation + fatigue effects
        if len(adj_speeds) >= 2:
            mid = len(adj_speeds) // 2
            first_half_avg = sum(adj_speeds[:mid]) / mid
            second_half_avg = sum(adj_speeds[mid:]) / (len(adj_speeds) - mid)
            diff_pct = ((second_half_avg - first_half_avg) / first_half_avg) * 100

            first_pace_secs = 1000 / first_half_avg
            second_pace_secs = 1000 / second_half_avg
            pace_diff = first_pace_secs - second_pace_secs

            if abs(diff_pct) < 1:
                adj_trend = "Stable"
            elif diff_pct > 0:
                adj_trend = f"Faster by {abs(pace_diff):.0f}s/km ({abs(diff_pct):.1f}%)"
            else:
                adj_trend = f"Slower by {abs(pace_diff):.0f}s/km ({abs(diff_pct):.1f}%)"

            lines.append(f"**Adjusted trend**: {adj_trend} (effort-normalized, strips elevation + fatigue)")

        lines.append("")

    return "\n".join(lines)


async def _analyse_runs(args: dict) -> str:
    """Analyse multiple runs — much more efficient than pulling streams for each."""
    days = args.get("days", 30)
    query = args.get("query", "all").lower()
    oldest = date.today() - timedelta(days=days)

    async with _client() as c:
        activity_list = await c.list_activities(oldest=oldest)
        # Filter to runs only
        run_ids = [a["id"] for a in activity_list if _is_run(a) or a.get("source")]

        # Fetch full data for each run (in parallel via asyncio.gather)
        async def _fetch(aid):
            try:
                return await c.get_activity(str(aid))
            except Exception:
                return None

        full_runs = await asyncio.gather(*[_fetch(aid) for aid in run_ids])

    runs = [r for r in full_runs if r and _is_run(r)]
    if not runs:
        return f"No runs found in the last {days} days."

    runs.sort(key=lambda a: a.get("start_date_local", ""))

    if query == "stops":
        # Find runs with significant pauses (>30s stopped)
        lines = [f"# Runs with Stops (last {days} days)", ""]
        found = []
        for r in runs:
            elapsed = r.get("elapsed_time") or 0
            moving = r.get("moving_time") or 0
            stopped = elapsed - moving
            if stopped >= 30:
                found.append((r, stopped))

        if not found:
            lines.append("No runs with significant stops found.")
        else:
            found.sort(key=lambda x: x[1], reverse=True)
            lines.append(f"Found {len(found)} runs with pauses:\n")
            for r, stopped in found:
                dt = (r.get("start_date_local") or "")[:10]
                name = r.get("name", "Untitled")
                dist = fmt_distance(r.get("distance"))
                pace = fmt_pace(r.get("average_speed"))
                aid = r.get("id", "")
                stop_min = stopped / 60
                lines.append(f"### {dt} — {name} [{aid}]")
                lines.append(f"  {dist} @ {pace} | **{stop_min:.1f} min stopped** (elapsed {fmt_duration(r.get('elapsed_time'))} vs moving {fmt_duration(r.get('moving_time'))})")
                lines.append("")

        return "\n".join(lines)

    elif query == "fastest":
        runs_with_speed = [r for r in runs if r.get("average_speed") and r["average_speed"] > 0]
        runs_with_speed.sort(key=lambda r: r["average_speed"], reverse=True)
        lines = [f"# Fastest Runs (last {days} days)", ""]
        for r in runs_with_speed[:10]:
            dt = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            dist = fmt_distance(r.get("distance"))
            pace = fmt_pace(r.get("average_speed"))
            hr = fmt_hr(r.get("average_heartrate"))
            aid = r.get("id", "")
            lines.append(f"- **{pace}** — {dt} {name} [{aid}] ({dist}, {hr})")
        return "\n".join(lines)

    elif query == "longest":
        runs_with_dist = [r for r in runs if r.get("distance")]
        runs_with_dist.sort(key=lambda r: r["distance"], reverse=True)
        lines = [f"# Longest Runs (last {days} days)", ""]
        for r in runs_with_dist[:10]:
            dt = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            dist = fmt_distance(r.get("distance"))
            pace = fmt_pace(r.get("average_speed"))
            aid = r.get("id", "")
            lines.append(f"- **{dist}** — {dt} {name} [{aid}] ({pace})")
        return "\n".join(lines)

    elif query == "hardest":
        runs_with_load = [r for r in runs if r.get("icu_training_load")]
        runs_with_load.sort(key=lambda r: r["icu_training_load"], reverse=True)
        lines = [f"# Hardest Runs by Training Load (last {days} days)", ""]
        for r in runs_with_load[:10]:
            dt = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            load = fmt_load(r.get("icu_training_load"))
            dist = fmt_distance(r.get("distance"))
            pace = fmt_pace(r.get("average_speed"))
            aid = r.get("id", "")
            lines.append(f"- **Load {load}** — {dt} {name} [{aid}] ({dist} @ {pace})")
        return "\n".join(lines)

    elif query == "hilliest":
        runs_with_elev = [r for r in runs if r.get("total_elevation_gain")]
        runs_with_elev.sort(key=lambda r: r["total_elevation_gain"], reverse=True)
        lines = [f"# Hilliest Runs (last {days} days)", ""]
        for r in runs_with_elev[:10]:
            dt = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            elev = fmt_elevation(r.get("total_elevation_gain"))
            dist = fmt_distance(r.get("distance"))
            aid = r.get("id", "")
            lines.append(f"- **+{elev}** — {dt} {name} [{aid}] ({dist})")
        return "\n".join(lines)

    elif query == "highest_hr":
        runs_with_hr = [r for r in runs if r.get("max_heartrate")]
        runs_with_hr.sort(key=lambda r: r["max_heartrate"], reverse=True)
        lines = [f"# Highest HR Runs (last {days} days)", ""]
        for r in runs_with_hr[:10]:
            dt = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            max_hr = fmt_hr(r.get("max_heartrate"))
            avg_hr = fmt_hr(r.get("average_heartrate"))
            dist = fmt_distance(r.get("distance"))
            aid = r.get("id", "")
            lines.append(f"- **{max_hr} max** ({avg_hr} avg) — {dt} {name} [{aid}] ({dist})")
        return "\n".join(lines)

    else:
        # "all" — full summary
        lines = [f"# All Runs Summary (last {days} days, {len(runs)} runs)", ""]
        for r in runs:
            dt = (r.get("start_date_local") or "")[:10]
            name = r.get("name", "Untitled")
            dist = fmt_distance(r.get("distance"))
            pace = fmt_pace(r.get("average_speed"))
            hr = fmt_hr(r.get("average_heartrate"))
            load = fmt_load(r.get("icu_training_load"))
            elapsed = r.get("elapsed_time") or 0
            moving = r.get("moving_time") or 0
            stopped = elapsed - moving
            aid = r.get("id", "")
            stop_str = f" | ⏸ {stopped / 60:.0f}min stopped" if stopped >= 30 else ""
            adj = _effort_adjust(r)
            adj_str = f" | adj. {adj.adjusted_pace_str}/km" if adj and abs(adj.total_adjustment_secs) >= 3 else ""
            lines.append(f"- {dt} — {name} [{aid}]: {dist} @ {pace}{adj_str} | {hr} | load {load}{stop_str}")
        return "\n".join(lines)


async def _query_run_profiles(args: dict) -> str:
    """Query pre-computed run profiles from the database."""
    query = args.get("query", "all").lower()
    limit = args.get("limit", 15)

    # Get athlete ID for current user
    _, athlete_id = _get_credentials()

    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    if not sb_url or not sb_key:
        return "Database not configured. Cannot query run profiles."

    headers = {"apikey": sb_key, "Authorization": f"Bearer {sb_key}"}

    # Build query params based on what we're looking for
    params: dict = {
        "athlete_id": f"eq.{athlete_id}",
        "select": "*",
        "limit": str(limit),
    }

    if query == "negative_splits":
        params["negative_split_ratio"] = "lt.0.97"
        params["order"] = "negative_split_ratio.asc"
    elif query == "positive_splits":
        params["fade_index"] = "gt.1.05"
        params["order"] = "fade_index.desc"
    elif query == "most_consistent":
        params["pace_cv"] = "not.is.null"
        params["order"] = "pace_cv.asc"
    elif query == "least_consistent":
        params["pace_cv"] = "not.is.null"
        params["order"] = "pace_cv.desc"
    elif query == "stops":
        params["stop_count"] = "gt.0"
        params["order"] = "total_stopped_secs.desc"
    elif query == "hr_drift":
        params["hr_drift_pct"] = "not.is.null"
        params["order"] = "hr_drift_pct.desc"
    elif query == "steady_hr":
        params["hr_cv"] = "not.is.null"
        params["order"] = "hr_cv.asc"
    elif query == "hilly":
        params["elevation_profile"] = "in.(hilly,mountainous)"
        params["order"] = "climb_score.desc"
    elif query == "flat":
        params["elevation_profile"] = "eq.flat"
        params["order"] = "pace_cv.asc"
    elif query == "fastest_1k":
        params["best_1k_pace_secs"] = "not.is.null"
        params["order"] = "best_1k_pace_secs.asc"
    else:  # "all"
        params["order"] = "activity_id.desc"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{sb_url}/rest/v1/run_profiles", params=params, headers=headers)

    if resp.status_code != 200:
        return f"Error querying profiles: {resp.text[:200]}"

    profiles = resp.json()
    if not profiles:
        return f"No profiled runs found matching '{query}'. Run the profile computation first."

    # Also fetch activity names for these IDs
    activity_ids = [p["activity_id"] for p in profiles]
    activity_names = {}
    try:
        _, athlete_id = _get_credentials()
        async with _client() as c:
            for aid in activity_ids[:limit]:
                try:
                    act = await c.get_activity(aid, intervals=False)
                    dt = (act.get("start_date_local") or "")[:10]
                    name = act.get("name", "Untitled")
                    dist = act.get("distance", 0)
                    pace = act.get("average_speed")
                    activity_names[aid] = {"date": dt, "name": name, "distance": dist, "pace": pace}
                except Exception:
                    pass
    except Exception:
        pass

    # Format results based on query type
    title_map = {
        "negative_splits": "Negative Split Runs",
        "positive_splits": "Runs Where You Faded",
        "most_consistent": "Most Consistent Pacing",
        "least_consistent": "Most Erratic Pacing",
        "stops": "Runs with Stops",
        "hr_drift": "Highest HR Drift",
        "steady_hr": "Steadiest HR",
        "hilly": "Hilly Runs",
        "flat": "Flat Runs",
        "fastest_1k": "Fastest 1K Segments",
        "all": "All Profiled Runs",
    }
    title = title_map.get(query, f"Run Profiles: {query}")
    lines = [f"# {title} ({len(profiles)} results)", ""]

    for p in profiles:
        aid = p["activity_id"]
        info = activity_names.get(aid, {})
        dt = info.get("date", "?")
        name = info.get("name", aid)
        dist = fmt_distance(info.get("distance")) if info.get("distance") else ""
        pace = fmt_pace(info.get("pace")) if info.get("pace") else ""

        header = f"### {dt} — {name} [{aid}]"
        if dist:
            header += f" ({dist} @ {pace})"
        lines.append(header)

        # Show relevant metrics based on query
        details = []

        if query in ("negative_splits", "positive_splits", "most_consistent", "least_consistent", "all"):
            if p.get("negative_split_ratio") is not None:
                ratio = p["negative_split_ratio"]
                if ratio < 0.97:
                    details.append(f"Negative split (ratio {ratio:.3f})")
                elif ratio > 1.05:
                    details.append(f"Positive split — faded (ratio {ratio:.3f})")
                else:
                    details.append(f"Even pacing (ratio {ratio:.3f})")
            if p.get("even_pace_score") is not None:
                details.append(f"Consistency: {p['even_pace_score']:.0f}/100")
            if p.get("fade_index") is not None:
                details.append(f"Fade index: {p['fade_index']:.3f}")

        if query in ("stops", "all"):
            if p.get("stop_count", 0) > 0:
                details.append(f"{p['stop_count']} stops ({p.get('total_stopped_secs', 0):.0f}s total)")

        if query in ("hr_drift", "steady_hr", "all"):
            if p.get("hr_drift_pct") is not None:
                details.append(f"HR drift: {p['hr_drift_pct']:+.1f}%")
            if p.get("hr_cv") is not None:
                details.append(f"HR consistency (CV): {p['hr_cv']:.4f}")

        if query in ("hilly", "flat", "all"):
            if p.get("elevation_profile"):
                details.append(f"Profile: {p['elevation_profile']}")
            if p.get("climb_score") is not None:
                details.append(f"Climb: {p['climb_score']:.1f}m/km")

        if query == "fastest_1k":
            if p.get("best_1k_pace_secs") is not None:
                m, s = divmod(int(p["best_1k_pace_secs"]), 60)
                details.append(f"Best 1K: {m}:{s:02d} /km")

        if p.get("intensity_distribution"):
            details.append(f"Intensity: {p['intensity_distribution']}")

        if p.get("hr_above_90pct_secs") and p["hr_above_90pct_secs"] > 30:
            details.append(f"Time above 90% max HR: {fmt_duration(p['hr_above_90pct_secs'])}")

        # Per-km splits if available
        if query in ("negative_splits", "positive_splits", "most_consistent", "least_consistent"):
            splits = p.get("km_splits", [])
            if splits and isinstance(splits, list):
                split_strs = []
                for i, s in enumerate(splits):
                    m, sec = divmod(int(s), 60)
                    split_strs.append(f"K{i+1}: {m}:{sec:02d}")
                if len(split_strs) <= 15:
                    details.append(f"Splits: {' | '.join(split_strs)}")

        for d in details:
            lines.append(f"  - {d}")
        lines.append("")

    return "\n".join(lines)


async def _get_effort_adjusted(args: dict) -> str:
    """Full effort-adjusted pace with weather for a single activity."""
    activity_id = args["activity_id"]
    async with _client() as c:
        act = await c.get_activity(activity_id)
        weather = await _get_activity_weather(c, activity_id, act) if act else None

    if not act:
        return f"Activity {activity_id} not found."

    adj = _effort_adjust(act, weather)

    name = act.get("name", "Untitled")
    dt = (act.get("start_date_local") or "")[:10]
    pace = fmt_pace(act.get("average_speed"))

    lines = [f"# Effort-Adjusted Pace: {name}", f"*{dt}*", ""]

    lines.append(f"**Raw pace:** {pace}")
    if act.get("gap"):
        lines.append(f"**GAP:** {fmt_pace(act['gap'])}")

    if adj and abs(adj.total_adjustment_secs) >= 2:
        lines.append(f"**Effort-adjusted:** {adj.adjusted_pace_str} /km")
        lines.append("")
        lines.append("## Adjustment Breakdown")
        lines.append(f"Total adjustment: {abs(adj.total_adjustment_secs):.0f}s/km")
        lines.append(adj.breakdown())
        lines.append("")
        lines.append(f"*{adj.conditions_summary}*")
    else:
        lines.append("")
        lines.append("Near-ideal conditions — no significant adjustment needed.")

    # Context
    lines.append("")
    lines.append("## Conditions")
    if weather:
        lines.append(f"- {format_weather(weather)}")
        lines.append(f"- {weather['running_impact']}")
    else:
        lines.append("- Weather data not available for this run")

    lines.append(f"- Elevation: +{fmt_elevation(act.get('total_elevation_gain'))} / -{fmt_elevation(act.get('total_elevation_loss'))}")

    if act.get("icu_ctl") is not None and act.get("icu_atl") is not None:
        lines.append(f"- {fmt_tsb(act.get('icu_ctl'), act.get('icu_atl'))}")

    return "\n".join(lines)


async def _get_activity_weather(c, activity_id: str, act: dict) -> dict | None:
    """Helper: fetch weather for an activity using its latlng stream."""
    try:
        streams = await c.get_streams(activity_id, types=["latlng"])
        lat, lon = None, None
        if streams and isinstance(streams, list):
            for s in streams:
                if isinstance(s, dict) and s.get("type") == "latlng":
                    data = s.get("data", [])
                    if data and isinstance(data[0], list) and len(data[0]) == 2:
                        lat, lon = data[0][0], data[0][1]
                    break
            if lat is None and isinstance(streams[0], list) and len(streams[0]) == 2:
                lat, lon = streams[0][0], streams[0][1]
        if lat and lon:
            return await get_weather_for_activity(
                lat, lon, act.get("start_date_local", ""),
                act.get("moving_time") or 3600,
            )
    except Exception:
        pass
    return None


async def _compare_runs(args: dict) -> str:
    async with _client() as c:
        a1 = await c.get_activity(args["id1"], intervals=True)
        a2 = await c.get_activity(args["id2"], intervals=True)
        # Fetch weather for both runs
        w1 = await _get_activity_weather(c, args["id1"], a1)
        w2 = await _get_activity_weather(c, args["id2"], a2)

    lines = [f"# Run Comparison", ""]

    # Weather context first — this can explain pace differences
    if w1 or w2:
        lines.append("## Conditions")
        if w1:
            lines.append(f"- **{a1.get('name', 'Run 1')}**: {format_weather(w1)} — {w1['running_impact']}")
        if w2:
            lines.append(f"- **{a2.get('name', 'Run 2')}**: {format_weather(w2)} — {w2['running_impact']}")

        # Flag if conditions were very different
        if w1 and w2:
            temp_diff = abs(w1["temp_c"] - w2["temp_c"])
            if temp_diff >= 10:
                lines.append(f"- ⚡ {temp_diff:.0f}°C temperature difference — pace comparison needs context")
            elif w1.get("dew_point_c") and w2.get("dew_point_c"):
                dp_diff = abs(w1["dew_point_c"] - w2["dew_point_c"])
                if dp_diff >= 8:
                    lines.append(f"- ⚡ Very different humidity conditions — pace comparison needs context")
        lines.append("")

    # Effort-adjusted paces
    adj1 = _effort_adjust(a1, w1)
    adj2 = _effort_adjust(a2, w2)

    # Show effort-adjusted comparison if either run had significant adjustments
    if (adj1 and abs(adj1.total_adjustment_secs) >= 2) or (adj2 and abs(adj2.total_adjustment_secs) >= 2):
        lines.append("## Effort-Adjusted Pace")
        lines.append("*What each run is equivalent to in ideal conditions (10°C, flat, fresh):*")
        if adj1:
            lines.append(f"- **{a1.get('name', 'Run 1')}**: {adj1.summary()}")
        if adj2:
            lines.append(f"- **{a2.get('name', 'Run 2')}**: {adj2.summary()}")
        lines.append("")

    def _col(a, adj=None):
        cols = {
            "Name": a.get("name", "—"),
            "Date": (a.get("start_date_local") or "")[:10],
            "Distance": fmt_distance(a.get("distance")),
            "Duration": fmt_duration(a.get("moving_time")),
            "Pace": fmt_pace(a.get("average_speed")),
        }
        if adj and abs(adj.total_adjustment_secs) >= 2:
            cols["Adj. Pace"] = f"{adj.adjusted_pace_str} /km"
        else:
            cols["Adj. Pace"] = "—"
        cols.update({
            "GAP": fmt_pace(a.get("gap")) if a.get("gap") else "—",
            "Avg HR": fmt_hr(a.get("average_heartrate")),
            "Max HR": fmt_hr(a.get("max_heartrate")),
            "Cadence": fmt_cadence(a.get("average_cadence")),
            "Stride": f"{a['average_stride']:.2f}m" if a.get("average_stride") else "—",
            "Elevation": f"+{fmt_elevation(a.get('total_elevation_gain'))}",
            "Load": fmt_load(a.get("icu_training_load")),
            "Intensity": fmt_percent(a.get("icu_intensity", 0) * 100) if a.get("icu_intensity") else "—",
            "Efficiency": fmt_efficiency(a.get("icu_efficiency_factor")),
            "Decoupling": fmt_decoupling(a.get("decoupling")),
            "CTL": f"{a.get('icu_ctl', 0):.0f}" if a.get("icu_ctl") else "—",
            "ATL": f"{a.get('icu_atl', 0):.0f}" if a.get("icu_atl") else "—",
            "TSB": f"{a.get('icu_ctl', 0) - a.get('icu_atl', 0):+.0f}" if a.get("icu_ctl") else "—",
            "Gear": (a.get("gear") or {}).get("name", "—"),
        })
        return cols

    c1 = _col(a1, adj1)
    c2 = _col(a2, adj2)

    lines.append(f"| Metric | {c1['Name']} | {c2['Name']} |")
    lines.append("|---|---|---|")
    for key in c1:
        if key == "Name":
            continue
        lines.append(f"| {key} | {c1[key]} | {c2[key]} |")

    return "\n".join(lines)


async def _search_activities(args: dict) -> str:
    async with _client() as c:
        if args.get("query"):
            activities = await c.search_activities(args["query"], args.get("limit", 20))
        elif args.get("date_from"):
            activities = await c.list_activities(
                oldest=args.get("date_from"),
                newest=args.get("date_to"),
                limit=args.get("limit", 20),
            )
        else:
            activities = await c.list_activities(limit=args.get("limit", 20))

    runs = [a for a in activities if _is_run(a)]
    if not runs:
        return "No matching runs found."

    lines = [f"# Search Results ({len(runs)} runs)", ""]
    for a in runs:
        dt = (a.get("start_date_local") or "")[:10]
        name = a.get("name", "Untitled")
        aid = a.get("id", "")
        dist = fmt_distance(a.get("distance"))
        pace = fmt_pace(a.get("average_speed"))
        hr = fmt_hr(a.get("average_heartrate"))
        lines.append(f"- **{dt}** — {name} [{aid}]: {dist} @ {pace} | {hr}")

    return "\n".join(lines)


async def _find_similar_intervals(args: dict) -> str:
    async with _client() as c:
        results = await c.search_intervals(
            min_secs=args["min_duration_secs"],
            max_secs=args["max_duration_secs"],
            min_intensity=args.get("min_intensity", 80),
            max_intensity=args.get("max_intensity", 120),
            limit=args.get("limit", 15),
        )

    if not results:
        return "No matching interval sessions found."

    lines = [
        f"# Interval Search: {fmt_duration(args['min_duration_secs'])}–{fmt_duration(args['max_duration_secs'])} "
        f"@ {args.get('min_intensity', 80)}–{args.get('max_intensity', 120)}% intensity",
        "",
    ]

    for a in results:
        dt = (a.get("start_date_local") or "")[:10]
        name = a.get("name", "Untitled")
        aid = a.get("id", "")
        dist = fmt_distance(a.get("distance"))
        lines.append(f"- **{dt}** — {name} [{aid}]: {dist}")

    return "\n".join(lines)


async def _get_shoes(args: dict) -> str:
    async with _client() as c:
        gear = await c.get_gear()

    shoes = [g for g in gear if (g.get("type") or "").lower() in ("shoes", "runningshoes")]
    if not shoes:
        # Show all gear if no shoes specifically
        shoes = gear

    lines = ["# Shoes / Gear", ""]

    for s in shoes:
        name = s.get("name", "Unknown")
        dist = fmt_distance(s.get("distance"))
        activities = s.get("activities", 0)
        retired = s.get("retired")
        status = " (retired)" if retired else ""
        lines.append(f"### {name}{status}")
        lines.append(f"- Distance: {dist}")
        lines.append(f"- Activities: {activities}")
        if s.get("purchased"):
            lines.append(f"- Purchased: {s['purchased']}")
        if s.get("notes"):
            lines.append(f"- Notes: {s['notes']}")

        # Reminders
        reminders = s.get("reminders", [])
        for r in reminders:
            text = r.get("text", "")
            dist_alert = r.get("distance")
            if dist_alert:
                lines.append(f"- Reminder: {text} at {fmt_distance(dist_alert)}")
        lines.append("")

    return "\n".join(lines)


async def _get_athlete_profile(args: dict) -> str:
    async with _client() as c:
        athlete = await c.get_athlete()
        wellness_today = None
        try:
            wellness_today = await c.get_wellness_for_date(date.today())
        except Exception:
            pass

    lines = ["# Athlete Profile", ""]

    lines.append(f"- Name: {athlete.get('name', '—')}")
    if athlete.get("sex"):
        lines.append(f"- Sex: {athlete['sex']}")
    if athlete.get("icu_date_of_birth"):
        lines.append(f"- DOB: {athlete['icu_date_of_birth']}")
    if athlete.get("icu_weight"):
        lines.append(f"- Weight: {athlete['icu_weight']:.1f} kg")
    if athlete.get("height"):
        lines.append(f"- Height: {athlete['height']:.0f} cm")
    if athlete.get("icu_resting_hr"):
        lines.append(f"- Resting HR: {athlete['icu_resting_hr']} bpm")

    # Current fitness
    if wellness_today:
        lines.append("")
        lines.append("## Current Fitness")
        lines.append(f"- {fmt_tsb(wellness_today.get('ctl'), wellness_today.get('atl'))}")
        lines.append(f"- Ramp Rate: {fmt_ramp_rate(wellness_today.get('rampRate'))}")

    # Sport settings for running
    sport_settings = athlete.get("sportSettings", [])
    run_settings = None
    for ss in sport_settings:
        types = ss.get("types", [])
        if "Run" in types:
            run_settings = ss
            break

    if run_settings:
        lines.append("")
        lines.append("## Running Settings")
        if run_settings.get("threshold_pace"):
            lines.append(f"- Threshold Pace: {fmt_pace(run_settings['threshold_pace'])}")
        if run_settings.get("lthr"):
            lines.append(f"- LTHR: {run_settings['lthr']} bpm")
        if run_settings.get("max_hr"):
            lines.append(f"- Max HR: {run_settings['max_hr']} bpm")
        if run_settings.get("ftp"):
            lines.append(f"- Running FTP: {run_settings['ftp']}W")

        # Pace zones
        pz = run_settings.get("pace_zones", [])
        pzn = run_settings.get("pace_zone_names", [])
        if pz:
            lines.append("")
            lines.append("### Pace Zones")
            for i, boundary in enumerate(pz):
                name = pzn[i] if i < len(pzn) else f"Z{i + 1}"
                lines.append(f"- {name}: {fmt_pace(boundary)}")

        # HR zones
        hz = run_settings.get("hr_zones", [])
        hzn = run_settings.get("hr_zone_names", [])
        if hz:
            lines.append("")
            lines.append("### HR Zones")
            for i, boundary in enumerate(hz):
                name = hzn[i] if i < len(hzn) else f"Z{i + 1}"
                lines.append(f"- {name}: {boundary} bpm")

        if run_settings.get("gap_model"):
            lines.append(f"\nGAP Model: {run_settings['gap_model']}")

    # Shoes
    shoes = athlete.get("shoes", [])
    if shoes:
        lines.append("")
        lines.append("## Shoes")
        for s in shoes:
            name = s.get("name", "Unknown")
            dist = fmt_distance(s.get("distance"))
            primary = " (primary)" if s.get("primary") else ""
            lines.append(f"- {name}: {dist}{primary}")

    return "\n".join(lines)


async def _get_day_readiness(args: dict) -> str:
    target = args.get("date", str(date.today()))
    target_date = date.fromisoformat(target)
    oldest = target_date - timedelta(days=7)

    async with _client() as c:
        wellness = await c.get_wellness(oldest=oldest, newest=target_date)
        recent = await c.list_activities(oldest=target_date - timedelta(days=5), newest=target_date)
        # Also get 28-day wellness for RHR baseline drift
        wellness_28d = await c.get_wellness(oldest=target_date - timedelta(days=28), newest=target_date)

    today_data = None
    for w in wellness:
        if w.get("id") == target:
            today_data = w
            break

    lines = [f"# Readiness Check — {target}", ""]

    if not today_data:
        lines.append("No wellness data recorded for this day yet.")
    else:
        # Form + WHY you're fatigued
        ctl = today_data.get("ctl")
        atl = today_data.get("atl")
        if ctl is not None:
            tsb = ctl - atl
            lines.append(f"## Form")
            lines.append(f"- {fmt_tsb(ctl, atl)}")
            lines.append(f"- Ramp Rate: {fmt_ramp_rate(today_data.get('rampRate'))}")

            # Explain the fatigue by pointing at the hardest recent sessions
            if tsb < -5:
                hard_runs = sorted(
                    [a for a in recent if _is_run(a) and (a.get("icu_training_load") or 0) > 0],
                    key=lambda a: a.get("icu_training_load", 0),
                    reverse=True,
                )
                if hard_runs:
                    top = hard_runs[:3]
                    lines.append(f"- **Why:** mostly from " + ", ".join(
                        f"{(r.get('start_date_local') or '')[:10]} {r.get('name', 'run')} "
                        f"(load {r.get('icu_training_load', 0):.0f})"
                        for r in top
                    ))
            lines.append("")

        # HRV trend
        hrv_values = [w.get("hrv") for w in wellness if w.get("hrv")]
        hrv_today = today_data.get("hrv")
        hrv_7d = sum(hrv_values) / len(hrv_values) if hrv_values else None

        if hrv_today:
            lines.append("## HRV")
            lines.append(f"- Today: {interpret_hrv(hrv_today, hrv_7d)}")
            lines.append("")

        # Resting HR with baseline drift detection
        rhr_values_7d = [w.get("restingHR") for w in wellness if w.get("restingHR")]
        rhr_values_28d = [w.get("restingHR") for w in wellness_28d if w.get("restingHR")]
        rhr_today = today_data.get("restingHR")
        if rhr_today and rhr_values_7d:
            rhr_avg_7d = sum(rhr_values_7d) / len(rhr_values_7d)
            rhr_diff = rhr_today - rhr_avg_7d
            lines.append("## Resting HR")
            lines.append(f"- Today: {rhr_today} bpm ({rhr_diff:+.0f} vs 7-day avg)")
            if rhr_diff > 5:
                lines.append(f"- (could be anything — stress, caffeine, poor sleep, fighting something off)")

            # Baseline drift: compare first 14 days avg to last 7 days avg
            if len(rhr_values_28d) >= 14:
                first_half = rhr_values_28d[:len(rhr_values_28d)//2]
                second_half = rhr_values_28d[len(rhr_values_28d)//2:]
                drift = (sum(second_half) / len(second_half)) - (sum(first_half) / len(first_half))
                if abs(drift) >= 2:
                    direction = "up" if drift > 0 else "down"
                    lines.append(f"- Baseline trend: drifting {direction} {abs(drift):.1f} bpm over 4 weeks")
            lines.append("")

        # Sleep
        if today_data.get("sleepSecs"):
            lines.append("## Sleep")
            lines.append(f"- {interpret_sleep(today_data['sleepSecs'], today_data.get('sleepScore'))}")
            lines.append("")

        # Subjective
        subj_items = []
        for key, label in [("fatigue", "Fatigue"), ("soreness", "Soreness"),
                           ("stress", "Stress"), ("mood", "Mood"), ("motivation", "Motivation")]:
            if today_data.get(key):
                subj_items.append(f"{label}: {today_data[key]}/5")
        if subj_items:
            lines.append("## How You Feel")
            for s in subj_items:
                lines.append(f"- {s}")
            lines.append("")

        # Readiness score
        if today_data.get("readiness"):
            lines.append(f"## Readiness Score: {today_data['readiness']:.0f}/100")
            lines.append("")

    # Recent training context — full 5 day view with load attribution
    runs = [a for a in recent if _is_run(a)]
    if runs:
        total_load = sum(r.get("icu_training_load") or 0 for r in runs)
        lines.append(f"## Recent Training (5 days, total load: {total_load:.0f})")
        for r in sorted(runs, key=lambda x: x.get("start_date_local", ""), reverse=True):
            dt = (r.get("start_date_local") or "")[:10]
            load = r.get("icu_training_load") or 0
            lines.append(
                f"- {dt}: {r.get('name', '')} — {fmt_distance(r.get('distance'))} "
                f"@ {fmt_pace(r.get('average_speed'))} | load {fmt_load(load)}"
            )

    return "\n".join(lines)


async def _get_training_load(args: dict) -> str:
    weeks = args.get("weeks", 8)
    days = weeks * 7
    oldest = date.today() - timedelta(days=days)

    async with _client() as c:
        activities = await c.list_activities(oldest=oldest)
        wellness = await c.get_wellness(oldest=oldest)

    runs = [a for a in activities if _is_run(a)]

    lines = [f"# Training Load Analysis ({weeks} weeks)", ""]

    # Group runs by week
    from collections import defaultdict
    weekly = defaultdict(list)
    for r in runs:
        d = date.fromisoformat((r.get("start_date_local") or "")[:10])
        week_start = d - timedelta(days=d.weekday())
        weekly[str(week_start)].append(r)

    lines.append("## Weekly Summary")
    weekly_loads = []
    for week_start in sorted(weekly.keys()):
        week_runs = weekly[week_start]
        total_dist = sum(r.get("distance") or 0 for r in week_runs)
        total_load = sum(r.get("icu_training_load") or 0 for r in week_runs)
        total_time = sum(r.get("moving_time") or 0 for r in week_runs)
        weekly_loads.append(total_load)

        lines.append(
            f"- {week_start}: {len(week_runs)} runs | {fmt_distance(total_dist)} | "
            f"{fmt_duration(total_time)} | load {total_load:.0f}"
        )

    # ACWR (Acute:Chronic Workload Ratio)
    if len(weekly_loads) >= 4:
        lines.append("")
        lines.append("## Workload Ratio (ACWR)")
        acute = sum(weekly_loads[-1:]) if weekly_loads else 0
        chronic = sum(weekly_loads[-4:]) / min(4, len(weekly_loads)) if weekly_loads else 1
        acwr = acute / chronic if chronic > 0 else 0
        if acwr < 0.8:
            status = "undertraining — could do more"
        elif acwr <= 1.3:
            status = "sweet spot — optimal adaptation"
        elif acwr <= 1.5:
            status = "caution — high load"
        else:
            status = "danger zone — injury risk"
        lines.append(f"- ACWR: {acwr:.2f} ({status})")
        lines.append(f"- This week load: {acute:.0f}")
        lines.append(f"- 4-week avg: {chronic:.0f}")

    # Km change week over week
    if len(weekly_loads) >= 2:
        lines.append("")
        lines.append("## Week-over-Week Change")
        sorted_weeks = sorted(weekly.keys())
        for i in range(1, len(sorted_weeks)):
            prev_dist = sum(r.get("distance") or 0 for r in weekly[sorted_weeks[i - 1]])
            curr_dist = sum(r.get("distance") or 0 for r in weekly[sorted_weeks[i]])
            if prev_dist > 0:
                pct = ((curr_dist - prev_dist) / prev_dist) * 100
                lines.append(f"- {sorted_weeks[i]}: {pct:+.0f}% ({fmt_distance(curr_dist)} vs {fmt_distance(prev_dist)})")

    return "\n".join(lines)


async def _get_planned_workouts(args: dict) -> str:
    days_ahead = args.get("days_ahead", 14)
    today = date.today()

    async with _client() as c:
        events = await c.get_events(oldest=today, newest=today + timedelta(days=days_ahead))

    if not events:
        return f"No planned workouts in the next {days_ahead} days."

    lines = [f"# Upcoming Plan (next {days_ahead} days)", ""]
    for e in events:
        dt = (e.get("start_date_local") or e.get("start_date") or "")[:10]
        name = e.get("name", "Untitled")
        cat = e.get("category", "")
        desc = e.get("description", "")
        lines.append(f"### {dt} — {name}")
        if cat:
            lines.append(f"- Type: {cat}")
        if desc:
            lines.append(f"- {desc}")
        lines.append("")

    return "\n".join(lines)


# ── handler dispatch ────────────────────────────────────────

_HANDLERS = {
    "get_activity": _get_activity,
    "get_recent": _get_recent,
    "get_week": _get_week,
    "get_fitness": _get_fitness,
    "get_wellness": _get_wellness,
    "get_intervals": _get_intervals,
    "get_streams": _get_streams,
    "get_pace_curves": _get_pace_curves,
    "get_pace_progression": _get_pace_progression,
    "compare_runs": _compare_runs,
    "search_activities": _search_activities,
    "find_similar_intervals": _find_similar_intervals,
    "get_shoes": _get_shoes,
    "get_athlete_profile": _get_athlete_profile,
    "get_day_readiness": _get_day_readiness,
    "get_training_load": _get_training_load,
    "get_planned_workouts": _get_planned_workouts,
    "get_effort_adjusted": _get_effort_adjusted,
    "analyse_runs": _analyse_runs,
    "query_run_profiles": _query_run_profiles,
}


# ── SSE transport (for Claude.ai browser) ───────────────────

def create_sse_app():
    """Create FastAPI app with SSE MCP transport for browser connections."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from mcp.server.sse import SseServerTransport
    from starlette.routing import Route, Mount

    from strava_pipeline.web.icu_onboarding import router as icu_router

    fastapi_app = FastAPI(title="PaceTrace v2")
    fastapi_app.include_router(icu_router)

    sse_transport = SseServerTransport("/v2/mcp/messages")

    @fastapi_app.get("/v2/mcp/sse")
    async def handle_sse(request: Request):
        user = request.query_params.get("user", DEFAULT_USER)
        _request_user.set(user)
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )

    @fastapi_app.post("/v2/mcp/messages")
    async def handle_messages(request: Request):
        await sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )

    @fastapi_app.get("/v2/health")
    async def health():
        return {"status": "ok", "version": "v2", "engine": "intervals.icu"}

    return fastapi_app


# ── entry points ────────────────────────────────────────────

# Note: sse_app is NOT created at import time.
# On Railway, v2 routes are registered via mcp_server.create_combined_app().
# For standalone use: python mcp_server_v2.py --sse

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PaceTrace v2 MCP Server")
    parser.add_argument("--sse", action="store_true", help="Run in SSE mode for browser")
    parser.add_argument("--port", type=int, default=8001, help="Port for SSE mode")
    parser.add_argument("--user", type=str, default=DEFAULT_USER, help="Default user")
    args = parser.parse_args()

    if args.user:
        _request_user.set(args.user)

    if args.sse:
        import uvicorn
        sse_app = create_sse_app()
        uvicorn.run(sse_app, host="0.0.0.0", port=args.port)
    else:
        # stdio mode for Claude Desktop
        async def main():
            from mcp.server.stdio import stdio_server
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
        asyncio.run(main())
