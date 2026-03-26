"""
PaceTrace user management — unified identity across Strava + intervals.icu.
"""

from .client import get_client


def upsert_user(
    username: str,
    display_name: str | None = None,
    email: str | None = None,
    icu_athlete_id: str | None = None,
    icu_api_key: str | None = None,
    strava_athlete_id: int | None = None,
    max_hr: int | None = None,
    rest_hr: int | None = None,
    gender: str | None = None,
) -> dict:
    """Create or update a PaceTrace user."""
    sb = get_client()
    data = {"username": username, "updated_at": "now()"}
    if display_name:
        data["display_name"] = display_name
    if email:
        data["email"] = email
    if icu_athlete_id:
        data["icu_athlete_id"] = icu_athlete_id
    if icu_api_key:
        data["icu_api_key"] = icu_api_key
    if strava_athlete_id:
        data["strava_athlete_id"] = strava_athlete_id
    if max_hr:
        data["max_hr"] = max_hr
    if rest_hr:
        data["rest_hr"] = rest_hr
    if gender:
        data["gender"] = gender

    result = (
        sb.table("pacetrace_users")
        .upsert(data, on_conflict="username")
        .execute()
    )
    return result.data[0] if result.data else {}


def get_user(username: str) -> dict | None:
    """Get a user by username."""
    sb = get_client()
    result = (
        sb.table("pacetrace_users")
        .select("*")
        .eq("username", username)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_user_by_icu_id(icu_athlete_id: str) -> dict | None:
    sb = get_client()
    result = (
        sb.table("pacetrace_users")
        .select("*")
        .eq("icu_athlete_id", icu_athlete_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def list_users() -> list[dict]:
    sb = get_client()
    result = sb.table("pacetrace_users").select("*").execute()
    return result.data or []
