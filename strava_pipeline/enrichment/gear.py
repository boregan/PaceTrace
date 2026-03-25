"""
Shoe / gear enrichment from Strava API.

Fetches gear name from Strava and calculates kilometres on the shoe
at the time of each run by summing activities in our own DB.
"""
from __future__ import annotations


def get_gear_name(strava_client, gear_id: str) -> str | None:
    """
    Fetch shoe/gear name from Strava API.
    Returns e.g. "Nike Pegasus 40" or None.
    """
    if not gear_id:
        return None
    try:
        gear = strava_client.get_gear(gear_id)
        parts = [p for p in [
            getattr(gear, "brand_name", None),
            getattr(gear, "model_name", None) or getattr(gear, "name", None),
        ] if p]
        return " ".join(parts) if parts else str(gear_id)
    except Exception as e:
        print(f"[gear] failed to fetch {gear_id}: {e}")
        return None


def shoe_km_at_run(athlete_id: int, gear_id: str, run_date: str) -> float | None:
    """
    Estimate km on the shoe at the time of this run by summing all activities
    with the same gear_id logged on or before run_date in our DB.

    This uses our own Supabase data — no extra Strava API calls.
    """
    if not gear_id:
        return None
    try:
        from strava_pipeline.db.client import get_client
        resp = (
            get_client()
            .table("activities")
            .select("distance_m")
            .eq("athlete_id", athlete_id)
            .eq("gear_id", gear_id)
            .lte("start_date", run_date)
            .execute()
        )
        rows = resp.data if resp and resp.data else []
        total_m = sum((r.get("distance_m") or 0) for r in rows)
        return round(total_m / 1000, 1)
    except Exception as e:
        print(f"[gear] km calc failed: {e}")
        return None
