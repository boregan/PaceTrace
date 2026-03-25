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
from datetime import datetime, timedelta
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
MAX_HR = int(os.environ.get("PACETRACE_MAX_HR", "185"))


def _effective_user(user: str) -> str:
    """Return user arg, falling back to the per-connection context user."""
    if user and user != DEFAULT_USER:
        return user
    ctx = _request_user.get()
    return ctx if ctx else user


def _resolve_athlete_id(user: str) -> int | None:
    user = _effective_user(user)
    # Local flat-file config (dev)
    u = get_user_by_name(user)
    if u:
        return int(u["STRAVA_ATHLETE_ID"])
    # DB tokens (production / self-serve users)
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
                "Get full details of a single Strava run activity including metadata "
                "(date, distance, HR, pace, elevation) and downsampled stream data showing "
                "how HR and pace evolved second-by-second. Also includes an interpretation "
                "of session type (easy/tempo/intervals/long run), pacing pattern, and "
                "cardiac drift. Use this to analyse the shape and quality of a specific run."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "activity_id": {
                        "type": "string",
                        "description": "Strava activity ID (numeric string)",
                    },
                    "user": {
                        "type": "string",
                        "description": "Athlete username (default: ben)",
                        "default": DEFAULT_USER,
                    },
                },
                "required": ["activity_id"],
            },
        ),
        Tool(
            name="get_week",
            description=(
                "Get a summary of all training activities in the 7 days ending on a given date. "
                "Returns total km, time, avg HR, HR zone distribution, and a list of each run. "
                "Use for weekly training check-ins and load analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "Athlete username",
                        "default": DEFAULT_USER,
                    },
                    "date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (default: today)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="get_recent",
            description=(
                "Get the most recent n Strava activities as a compact list. "
                "Returns date, name, distance, duration, avg HR and pace for each. "
                "Use for quick overviews of recent training."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "Athlete username",
                        "default": DEFAULT_USER,
                    },
                    "n": {
                        "type": "string",
                        "description": "Number of activities to return (default: 5, max: 20)",
                        "default": "5",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="get_athlete_summary",
            description=(
                "Get overall career stats for an athlete: total km run, total hours, "
                "number of activities, avg HR across all runs, total elevation gain. "
                "Use for big-picture fitness overview."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "Athlete username",
                        "default": DEFAULT_USER,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="search_activities",
            description=(
                "Search and filter activities by type and/or date range. "
                "Returns matching activities with metadata. "
                "Use to find specific sessions like 'all interval runs in January' "
                "or 'long runs over 20km'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "Athlete username",
                        "default": DEFAULT_USER,
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date YYYY-MM-DD (inclusive)",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date YYYY-MM-DD (inclusive)",
                    },
                    "min_distance_km": {
                        "type": "number",
                        "description": "Only return activities longer than this (km)",
                    },
                    "max_distance_km": {
                        "type": "number",
                        "description": "Only return activities shorter than this (km)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default: 20)",
                        "default": 20,
                    },
                },
                "required": [],
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
        else:
            result = f"Unknown tool: {name}"
    except Exception as e:
        result = f"Error: {e}"

    return [TextContent(type="text", text=result if isinstance(result, str) else json.dumps(result, indent=2))]


async def _get_activity(args: dict) -> str:
    activity_id = int(args["activity_id"])
    user = args.get("user", DEFAULT_USER)

    activity = get_activity(activity_id)
    if not activity:
        return f"Activity {activity_id} not found in database."

    # Use build_context for the compact stream table
    return build_context(activity_id, max_points=120, max_hr=MAX_HR)


async def _get_week(args: dict) -> str:
    user = args.get("user", DEFAULT_USER)
    date_str = args.get("date")

    athlete_id = _resolve_athlete_id(user)
    if not athlete_id:
        return f"User '{user}' not found."

    end_dt = datetime.fromisoformat(date_str) if date_str else datetime.utcnow()
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
    total_s = sum((a.get("moving_time_s") or 0) for a in runs)
    hrs = [a["avg_heartrate"] for a in runs if a.get("avg_heartrate")]
    avg_hr = round(statistics.mean(hrs), 1) if hrs else None
    elev = sum((a.get("total_elevation_gain_m") or 0) for a in runs)

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
        f"Avg HR: {avg_hr} bpm  |  Elevation: {elev:.0f}m",
    ]
    if zones:
        lines.append("Zone distribution: " + "  ".join(f"{z}: {p}%" for z, p in sorted(zones.items())))

    lines.append("\n### Activities")
    for a in runs:
        dist = (a.get("distance_m") or 0) / 1000
        lines.append(
            f"- {a.get('start_date','?')[:10]}  {a.get('name','?')}  "
            f"{dist:.1f}km  {_fmt_duration(a.get('moving_time_s'))}  "
            f"HR: {a.get('avg_heartrate','?')}  Pace: {_pace(a.get('avg_speed_ms'))}/km"
        )

    return "\n".join(lines)


async def _get_recent(args: dict) -> str:
    user = args.get("user", DEFAULT_USER)
    n = int(args.get("n", 5))

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
    user = args.get("user", DEFAULT_USER)
    date_from = args.get("date_from", "2000-01-01")
    date_to = args.get("date_to", datetime.utcnow().date().isoformat())
    min_dist = args.get("min_distance_km")
    max_dist = args.get("max_distance_km")
    limit = int(args.get("limit", 20))

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


# ── Entry points ───────────────────────────────────────────────────────────────

async def _run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


# Remote SSE mode — mount into a Starlette app for Railway deployment
def create_sse_app():
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from mcp.server.sse import SseServerTransport

    # Full path is /mcp/messages because this app is mounted at /mcp
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


# For Railway: combine webhook FastAPI app with MCP SSE on /mcp/*
def create_combined_app():
    from mcp.server.sse import SseServerTransport
    from starlette.requests import Request
    from strava_pipeline.webhook.app import app as fastapi_app

    # Register routes directly on FastAPI to avoid Starlette mount root_path
    # doubling the /mcp prefix (which would produce /mcp/mcp/messages).
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
    # Local stdio mode
    asyncio.run(_run_stdio())
