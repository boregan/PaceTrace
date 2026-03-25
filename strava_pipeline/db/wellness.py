"""Supabase operations for the athlete_wellness table."""
from __future__ import annotations
from .client import get_client


def upsert_wellness(row: dict) -> None:
    get_client().table("athlete_wellness").upsert(
        row, on_conflict="athlete_id,date"
    ).execute()


def get_wellness_range(athlete_id: int, date_from: str, date_to: str) -> list[dict]:
    resp = (
        get_client()
        .table("athlete_wellness")
        .select("*")
        .eq("athlete_id", athlete_id)
        .gte("date", date_from)
        .lte("date", date_to)
        .order("date", desc=False)
        .execute()
    )
    return resp.data if resp and resp.data else []


def get_latest_wellness(athlete_id: int, n: int = 14) -> list[dict]:
    resp = (
        get_client()
        .table("athlete_wellness")
        .select("*")
        .eq("athlete_id", athlete_id)
        .order("date", desc=True)
        .limit(n)
        .execute()
    )
    return list(reversed(resp.data)) if resp and resp.data else []
