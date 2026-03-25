from __future__ import annotations

from strava_pipeline.db.client import get_client


def upsert_athlete_tokens(
    athlete_id: int,
    username: str,
    display_name: str,
    access_token: str,
    refresh_token: str,
    token_expires_at: int,
) -> None:
    db = get_client()
    db.table("athlete_tokens").upsert({
        "athlete_id": athlete_id,
        "username": username,
        "display_name": display_name,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": token_expires_at,
    }).execute()


def get_tokens_by_username(username: str) -> dict | None:
    db = get_client()
    resp = db.table("athlete_tokens").select("*").eq("username", username).limit(1).execute()
    return resp.data[0] if resp.data else None


def get_tokens_by_athlete_id(athlete_id: int) -> dict | None:
    db = get_client()
    resp = db.table("athlete_tokens").select("*").eq("athlete_id", athlete_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def username_exists(username: str) -> bool:
    db = get_client()
    resp = db.table("athlete_tokens").select("username").eq("username", username).execute()
    return bool(resp.data)


def update_tokens(athlete_id: int, access_token: str, refresh_token: str, expires_at: int) -> None:
    db = get_client()
    db.table("athlete_tokens").update({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": expires_at,
    }).eq("athlete_id", athlete_id).execute()


def get_all_athletes() -> list[dict]:
    db = get_client()
    resp = db.table("athlete_tokens").select("athlete_id,username,display_name").execute()
    return resp.data or []
