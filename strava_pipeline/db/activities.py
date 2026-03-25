"""
Supabase operations for the activities table.
"""

from __future__ import annotations
from .client import get_client


def upsert_activity(data: dict) -> None:
    """Insert or update a single activity row."""
    get_client().table("activities").upsert(data, on_conflict="strava_id").execute()


def get_existing_strava_ids(athlete_id: int) -> set[int]:
    """Return the set of strava_ids already stored for this athlete."""
    response = (
        get_client()
        .table("activities")
        .select("strava_id")
        .eq("athlete_id", athlete_id)
        .execute()
    )
    return {row["strava_id"] for row in response.data}


def get_activity(strava_id: int) -> dict | None:
    """Fetch a single activity row by strava_id."""
    response = (
        get_client()
        .table("activities")
        .select("*")
        .eq("strava_id", strava_id)
        .limit(1)
        .execute()
    )
    if response and response.data:
        return response.data[0]
    return None


def get_activities_in_range(athlete_id: int, date_from: str, date_to: str) -> list[dict]:
    """Fetch activities between two ISO date strings (inclusive)."""
    response = (
        get_client()
        .table("activities")
        .select("*")
        .eq("athlete_id", athlete_id)
        .gte("start_date", date_from)
        .lte("start_date", date_to)
        .order("start_date", desc=False)
        .execute()
    )
    return response.data if response and response.data else []


def get_recent_activities(athlete_id: int, n: int = 5, sport_type: str = None) -> list[dict]:
    """Fetch the n most recent activities, optionally filtered by sport_type."""
    query = (
        get_client()
        .table("activities")
        .select("*")
        .eq("athlete_id", athlete_id)
        .order("start_date", desc=True)
        .limit(n)
    )
    if sport_type:
        query = query.eq("sport_type", sport_type)
    response = query.execute()
    return response.data if response and response.data else []


def get_athlete_stats(athlete_id: int) -> dict:
    """Return aggregate stats for an athlete across all stored activities."""
    response = (
        get_client()
        .table("activities")
        .select("distance_m,moving_time_s,avg_heartrate,total_elevation_gain_m,sport_type,start_date,avg_speed_ms")
        .eq("athlete_id", athlete_id)
        .execute()
    )
    rows = response.data if response and response.data else []
    runs = [r for r in rows if r.get("sport_type") in ("Run", "VirtualRun", "TrailRun")]

    total_km = sum((r.get("distance_m") or 0) for r in runs) / 1000
    total_time_h = sum((r.get("moving_time_s") or 0) for r in runs) / 3600
    hrs = [r["avg_heartrate"] for r in runs if r.get("avg_heartrate")]
    avg_hr = sum(hrs) / len(hrs) if hrs else None
    elev = sum((r.get("total_elevation_gain_m") or 0) for r in runs)

    return {
        "total_runs": len(runs),
        "total_km": round(total_km, 1),
        "total_hours": round(total_time_h, 1),
        "avg_heartrate": round(avg_hr, 1) if avg_hr else None,
        "total_elevation_gain_m": round(elev, 0),
        "activities_stored": len(rows),
    }


def upsert_enrichment(strava_id: int, data: dict) -> None:
    """Update enrichment columns on an existing activity row."""
    get_client().table("activities").update(data).eq("strava_id", strava_id).execute()


def activity_from_stravalib(activity) -> dict:
    """Map a stravalib Activity object to the activities table schema."""
    avg_speed = float(activity.average_speed) if activity.average_speed else None

    # Start location — stravalib may return LatLon namedtuple, list, or None
    start_lat = start_lng = None
    latlng = getattr(activity, "start_latlng", None)
    if latlng is not None:
        try:
            if hasattr(latlng, "lat"):
                start_lat, start_lng = float(latlng.lat), float(latlng.lon)
            elif hasattr(latlng, "__iter__"):
                coords = list(latlng)
                if len(coords) == 2:
                    start_lat, start_lng = float(coords[0]), float(coords[1])
        except Exception:
            pass

    return {
        "strava_id": activity.id,
        "athlete_id": activity.athlete.id,
        "name": activity.name,
        "sport_type": str(activity.sport_type or activity.type),
        "start_date": activity.start_date.isoformat() if activity.start_date else None,
        "distance_m": float(activity.distance) if activity.distance else None,
        "elapsed_s": int(activity.elapsed_time.total_seconds()) if activity.elapsed_time else None,
        "moving_time_s": int(activity.moving_time.total_seconds()) if activity.moving_time else None,
        "avg_heartrate": activity.average_heartrate,
        "max_heartrate": activity.max_heartrate,
        "total_elevation_gain_m": float(activity.total_elevation_gain) if activity.total_elevation_gain else None,
        "avg_speed_ms": avg_speed,
        "gear_id": activity.gear_id,
        "start_lat": start_lat,
        "start_lng": start_lng,
    }
