"""
Supabase operations for the streams table.
"""

from __future__ import annotations
from .client import get_client


def upsert_stream(activity_id: int, stream_data: dict) -> None:
    """Insert or update a stream row for an activity."""
    row = {"activity_id": activity_id, **stream_data}
    get_client().table("streams").upsert(row, on_conflict="activity_id").execute()


def has_stream(activity_id: int) -> bool:
    """Return True if a stream already exists for this activity."""
    response = (
        get_client()
        .table("streams")
        .select("activity_id")
        .eq("activity_id", activity_id)
        .limit(1)
        .execute()
    )
    return bool(response and response.data)


def get_stream(activity_id: int) -> dict | None:
    """Fetch the raw stream row for an activity."""
    response = (
        get_client()
        .table("streams")
        .select("*")
        .eq("activity_id", activity_id)
        .limit(1)
        .execute()
    )
    if response and response.data:
        return response.data[0]
    return None


def streams_from_stravalib(stream_set) -> dict:
    """
    Convert a stravalib StreamSet to the streams table schema.
    Returns a dict of column_name -> list of values.
    """

    def _data(key: str) -> list | None:
        stream = stream_set.get(key) if isinstance(stream_set, dict) else getattr(stream_set, key, None)
        if stream is None:
            return None
        return list(stream.data)

    return {
        "time_s": _data("time"),
        "heartrate": _data("heartrate"),
        "velocity_ms": _data("velocity_smooth"),
        "altitude_m": _data("altitude"),
        "distance_m": _data("distance"),
        "cadence": _data("cadence"),
    }
