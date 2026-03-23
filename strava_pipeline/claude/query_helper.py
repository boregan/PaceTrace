from __future__ import annotations

"""
Builds compact, LLM-readable summaries of Strava activities.

Given an activity_id, returns a string containing:
  - Activity metadata (name, date, distance, duration, avg HR)
  - Downsampled stream data (one point per 30s by default)
  - Derived metrics: HR zone, pace category at each sample point

Designed to be pasted directly into a Claude prompt without
flooding the context window with thousands of raw data points.
"""
import json
from datetime import timedelta

from strava_pipeline.db.activities import get_activity
from strava_pipeline.db.streams import get_stream


# HR zone thresholds (adjust to your max HR)
def _hr_zone(hr: int | float | None, max_hr: int = 185) -> str:
    if hr is None:
        return "?"
    pct = hr / max_hr
    if pct < 0.60:
        return "Z1"
    elif pct < 0.70:
        return "Z2"
    elif pct < 0.80:
        return "Z3"
    elif pct < 0.90:
        return "Z4"
    else:
        return "Z5"


def _pace_label(velocity_ms: float | None) -> str:
    """Convert m/s to min/km string, e.g. '5:30'"""
    if not velocity_ms or velocity_ms <= 0:
        return "—"
    sec_per_km = 1000 / velocity_ms
    mins = int(sec_per_km // 60)
    secs = int(sec_per_km % 60)
    return f"{mins}:{secs:02d}"


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return "?"
    return str(timedelta(seconds=seconds))


def build_context(
    activity_id: int,
    max_points: int = 120,
    max_hr: int = 185,
) -> str:
    """
    Return a compact text summary of an activity suitable for a Claude prompt.

    Args:
        activity_id: Strava activity ID.
        max_points: Maximum number of stream data points to include (default 120 = one per 30s for a 1hr run).
        max_hr: Athlete's max HR for zone calculation.

    Returns:
        Formatted string with activity summary + stream table.
    """
    activity = get_activity(activity_id)
    if activity is None:
        return f"Activity {activity_id} not found in database."

    stream = get_stream(activity_id)

    lines: list[str] = []

    # Header
    lines.append(f"## Activity: {activity.get('name', 'Unknown')}")
    lines.append(f"Date: {activity.get('start_date', '?')}")

    dist_km = (activity.get("distance_m") or 0) / 1000
    lines.append(f"Distance: {dist_km:.2f} km")
    lines.append(f"Duration: {_format_duration(activity.get('elapsed_s'))}")
    lines.append(f"Moving time: {_format_duration(activity.get('moving_time_s'))}")

    if activity.get("avg_heartrate"):
        lines.append(f"Avg HR: {activity['avg_heartrate']:.0f} bpm")
    if activity.get("max_heartrate"):
        lines.append(f"Max HR: {activity['max_heartrate']:.0f} bpm")
    if activity.get("avg_speed_ms"):
        lines.append(f"Avg pace: {_pace_label(activity['avg_speed_ms'])} /km")
    if activity.get("total_elevation_gain_m"):
        lines.append(f"Elevation gain: {activity['total_elevation_gain_m']:.0f} m")

    if stream is None:
        lines.append("\n(No stream data available for this activity.)")
        return "\n".join(lines)

    time_s: list[int] = stream.get("time_s") or []
    hr: list[int | None] = stream.get("heartrate") or []
    vel: list[float | None] = stream.get("velocity_ms") or []
    alt: list[float | None] = stream.get("altitude_m") or []
    dist: list[float | None] = stream.get("distance_m") or []

    n = len(time_s)
    if n == 0:
        lines.append("\n(Stream data is empty.)")
        return "\n".join(lines)

    # Downsample: pick evenly spaced indices
    if n <= max_points:
        indices = list(range(n))
    else:
        step = n / max_points
        indices = [int(i * step) for i in range(max_points)]

    lines.append(f"\n## Stream data ({len(indices)} samples from {n} total points)")
    lines.append("time(s) | dist(km) | pace(/km) | HR(bpm) | zone | alt(m)")
    lines.append("--------|----------|-----------|---------|------|-------")

    for idx in indices:
        t = time_s[idx] if idx < len(time_s) else None
        h = hr[idx] if idx < len(hr) else None
        v = vel[idx] if idx < len(vel) else None
        a = alt[idx] if idx < len(alt) else None
        d = dist[idx] if idx < len(dist) else None

        d_km = f"{d/1000:.2f}" if d is not None else "—"
        a_str = f"{a:.0f}" if a is not None else "—"
        h_str = str(int(h)) if h is not None else "—"
        zone = _hr_zone(h, max_hr)

        lines.append(f"{t} | {d_km} | {_pace_label(v)} | {h_str} | {zone} | {a_str}")

    return "\n".join(lines)


def build_context_json(
    activity_id: int,
    max_points: int = 120,
    max_hr: int = 185,
) -> dict:
    """Same as build_context but returns structured dict instead of string."""
    activity = get_activity(activity_id)
    if activity is None:
        return {"error": f"Activity {activity_id} not found"}

    stream = get_stream(activity_id)

    result = {
        "activity_id": activity_id,
        "name": activity.get("name"),
        "start_date": activity.get("start_date"),
        "distance_km": round((activity.get("distance_m") or 0) / 1000, 2),
        "elapsed_s": activity.get("elapsed_s"),
        "avg_heartrate": activity.get("avg_heartrate"),
        "max_heartrate": activity.get("max_heartrate"),
        "avg_pace": _pace_label(activity.get("avg_speed_ms")),
        "elevation_gain_m": activity.get("total_elevation_gain_m"),
        "stream_samples": [],
    }

    if stream:
        time_s = stream.get("time_s") or []
        hr = stream.get("heartrate") or []
        vel = stream.get("velocity_ms") or []
        alt = stream.get("altitude_m") or []
        dist = stream.get("distance_m") or []
        n = len(time_s)

        indices = list(range(n)) if n <= max_points else [int(i * n / max_points) for i in range(max_points)]

        for idx in indices:
            sample = {
                "t": time_s[idx] if idx < len(time_s) else None,
                "hr": hr[idx] if idx < len(hr) else None,
                "pace": _pace_label(vel[idx] if idx < len(vel) else None),
                "alt": round(alt[idx], 1) if idx < len(alt) and alt[idx] is not None else None,
                "dist_km": round(dist[idx] / 1000, 3) if idx < len(dist) and dist[idx] is not None else None,
                "zone": _hr_zone(hr[idx] if idx < len(hr) else None, max_hr),
            }
            result["stream_samples"].append(sample)

    return result
