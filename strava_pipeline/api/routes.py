from __future__ import annotations

"""
PaceTrace query API routes.

GET /activity/{strava_id}/summary
GET /athlete/{user}/week?date=YYYY-MM-DD
GET /athlete/{user}/recent?n=5
"""

import statistics
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from strava_pipeline.api.auth import verify_token
from strava_pipeline.api.analysis import (
    detect_activity_type,
    detect_cardiac_drift,
    detect_pacing_pattern,
    interpret_activity,
    zone_distribution,
    _vel_to_pace,
    hr_zone,
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

router = APIRouter(dependencies=[Depends(verify_token)])

MAX_HR = 185  # TODO: make per-user


def _resolve_athlete_id(user: str) -> int:
    u = get_user_by_name(user)
    if not u:
        raise HTTPException(status_code=404, detail=f"User '{user}' not found")
    return int(u["STRAVA_ATHLETE_ID"])


def _pace(vel_ms) -> str:
    return _vel_to_pace(vel_ms) if vel_ms else "—"


def _fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── GET /activity/{strava_id}/summary ─────────────────────────────────────────

@router.get("/activity/{strava_id}/summary")
async def activity_summary(strava_id: int):
    activity = get_activity(strava_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    stream = get_stream(strava_id)

    # Downsample stream to one point per ~30 seconds
    samples = []
    if stream:
        time_s = stream.get("time_s") or []
        hr = stream.get("heartrate") or []
        vel = stream.get("velocity_ms") or []
        alt = stream.get("altitude_m") or []
        dist = stream.get("distance_m") or []

        n = len(time_s)
        if n > 0:
            # Aim for one sample per 30 seconds
            total_time = time_s[-1] if time_s else 0
            target_points = max(1, total_time // 30)
            step = max(1, n // target_points)
            indices = range(0, n, step)

            for i in indices:
                h = hr[i] if i < len(hr) else None
                v = vel[i] if i < len(vel) else None
                a = alt[i] if i < len(alt) else None
                d = dist[i] if i < len(dist) else None
                samples.append({
                    "time_s": time_s[i],
                    "dist_km": round(d / 1000, 2) if d is not None else None,
                    "pace": _pace(v),
                    "hr": int(h) if h is not None else None,
                    "zone": f"Z{hr_zone(h, MAX_HR)}" if h else None,
                    "alt_m": round(a, 0) if a is not None else None,
                })

    # Interpretation
    interpretation = {}
    if stream:
        interpretation = interpret_activity(activity, stream, MAX_HR)

    return {
        "activity": {
            "strava_id": activity["strava_id"],
            "name": activity.get("name"),
            "date": activity.get("start_date"),
            "distance_km": round((activity.get("distance_m") or 0) / 1000, 2),
            "duration": _fmt_duration(activity.get("elapsed_s")),
            "moving_time": _fmt_duration(activity.get("moving_time_s")),
            "avg_hr": activity.get("avg_heartrate"),
            "max_hr": activity.get("max_heartrate"),
            "avg_pace": _pace(activity.get("avg_speed_ms")),
            "elevation_gain_m": activity.get("total_elevation_gain_m"),
        },
        "stream": samples,
        "interpretation": interpretation,
    }


# ── GET /athlete/{user}/week ───────────────────────────────────────────────────

@router.get("/athlete/{user}/week")
async def athlete_week(
    user: str,
    date: Optional[str] = Query(None, description="End date YYYY-MM-DD (default: today)"),
):
    athlete_id = _resolve_athlete_id(user)

    end_dt = datetime.fromisoformat(date) if date else datetime.utcnow()
    start_dt = end_dt - timedelta(days=6)

    activities = get_activities_in_range(
        athlete_id,
        start_dt.isoformat(),
        (end_dt + timedelta(days=1)).isoformat(),
    )

    runs = [a for a in activities if a.get("sport_type") in ("Run", "VirtualRun", "TrailRun")]

    total_km = sum((a.get("distance_m") or 0) for a in runs) / 1000
    total_time_s = sum((a.get("moving_time_s") or 0) for a in runs)
    hrs = [a["avg_heartrate"] for a in runs if a.get("avg_heartrate")]
    avg_hr = round(statistics.mean(hrs), 1) if hrs else None
    elev = sum((a.get("total_elevation_gain_m") or 0) for a in runs)

    # Zone distribution across the week (from streams)
    all_hr_points = []
    for run in runs:
        s = get_stream(run["strava_id"])
        if s and s.get("heartrate"):
            all_hr_points.extend([h for h in s["heartrate"] if h])

    zones = zone_distribution(all_hr_points, MAX_HR) if all_hr_points else {}

    run_summaries = [
        {
            "strava_id": a["strava_id"],
            "name": a.get("name"),
            "date": a.get("start_date"),
            "distance_km": round((a.get("distance_m") or 0) / 1000, 2),
            "duration": _fmt_duration(a.get("moving_time_s")),
            "avg_hr": a.get("avg_heartrate"),
            "avg_pace": _pace(a.get("avg_speed_ms")),
            "elevation_m": a.get("total_elevation_gain_m"),
        }
        for a in runs
    ]

    # Notable runs
    notable = []
    if runs:
        longest = max(runs, key=lambda a: a.get("distance_m") or 0)
        if (longest.get("distance_m") or 0) > 15000:
            notable.append(f"Longest run: {longest['name']} ({(longest['distance_m']/1000):.1f}km)")
        fastest = min(
            [a for a in runs if a.get("avg_speed_ms")],
            key=lambda a: a["avg_speed_ms"],
            default=None,
        )
        if fastest:
            notable.append(f"Fastest pace: {_pace(fastest['avg_speed_ms'])}/km ({fastest['name']})")

    return {
        "week": {
            "start": start_dt.date().isoformat(),
            "end": end_dt.date().isoformat(),
            "athlete": user,
        },
        "totals": {
            "runs": len(runs),
            "distance_km": round(total_km, 1),
            "time": _fmt_duration(total_time_s),
            "avg_hr": avg_hr,
            "elevation_gain_m": round(elev, 0),
            "zone_distribution": zones,
        },
        "activities": run_summaries,
        "notable": notable,
    }


# ── GET /athlete/{user}/recent ─────────────────────────────────────────────────

@router.get("/athlete/{user}/recent")
async def athlete_recent(
    user: str,
    n: int = Query(5, ge=1, le=20),
):
    athlete_id = _resolve_athlete_id(user)
    activities = get_recent_activities(athlete_id, n=n)

    return {
        "athlete": user,
        "activities": [
            {
                "strava_id": a["strava_id"],
                "name": a.get("name"),
                "date": a.get("start_date"),
                "type": a.get("sport_type"),
                "distance_km": round((a.get("distance_m") or 0) / 1000, 2),
                "duration": _fmt_duration(a.get("moving_time_s")),
                "avg_hr": a.get("avg_heartrate"),
                "avg_pace": _pace(a.get("avg_speed_ms")),
                "elevation_m": a.get("total_elevation_gain_m"),
            }
            for a in activities
        ],
    }


# ── GET /athlete/{user}/stats ──────────────────────────────────────────────────

@router.get("/athlete/{user}/stats")
async def athlete_stats(user: str):
    athlete_id = _resolve_athlete_id(user)
    stats = get_athlete_stats(athlete_id)
    return {"athlete": user, **stats}
