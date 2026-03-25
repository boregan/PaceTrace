"""Supabase operations for the terra_users table."""
from __future__ import annotations

from .client import get_client


def upsert_terra_user(
    athlete_id: int,
    terra_user_id: str,
    provider: str,
    reference_id: str,
) -> None:
    """Insert or update a Terra connection for an athlete."""
    get_client().table("terra_users").upsert(
        {
            "athlete_id":    athlete_id,
            "terra_user_id": terra_user_id,
            "provider":      provider,
            "reference_id":  reference_id,
            "active":        True,
        },
        on_conflict="terra_user_id",
    ).execute()


def get_terra_users(athlete_id: int) -> list[dict]:
    """Return all active Terra connections for an athlete."""
    resp = (
        get_client()
        .table("terra_users")
        .select("*")
        .eq("athlete_id", athlete_id)
        .eq("active", True)
        .execute()
    )
    return resp.data if resp and resp.data else []


def get_athlete_id_by_terra_user(terra_user_id: str) -> int | None:
    """Look up athlete_id from a Terra user_id."""
    resp = (
        get_client()
        .table("terra_users")
        .select("athlete_id")
        .eq("terra_user_id", terra_user_id)
        .limit(1)
        .execute()
    )
    if resp and resp.data:
        return int(resp.data[0]["athlete_id"])
    return None


def get_athlete_id_by_reference(reference_id: str) -> int | None:
    """
    Resolve a Terra reference_id (athlete username) to an athlete_id.
    Tries user config files first, then DB tokens table.
    """
    from strava_pipeline.utils.user_loader import get_user_by_name
    from strava_pipeline.db.tokens import get_tokens_by_username

    u = get_user_by_name(reference_id)
    if u:
        return int(u["STRAVA_ATHLETE_ID"])

    tokens = get_tokens_by_username(reference_id)
    if tokens:
        return int(tokens["athlete_id"])

    return None


def deactivate_terra_user(terra_user_id: str) -> None:
    """Mark a Terra connection as inactive (user disconnected)."""
    get_client().table("terra_users").update(
        {"active": False}
    ).eq("terra_user_id", terra_user_id).execute()


def list_all_terra_users() -> list[dict]:
    """Return all active Terra connections (for bulk sync)."""
    resp = (
        get_client()
        .table("terra_users")
        .select("*")
        .eq("active", True)
        .execute()
    )
    return resp.data if resp and resp.data else []
