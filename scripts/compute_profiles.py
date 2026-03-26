#!/usr/bin/env python3
"""
Compute run profiles (fingerprints) for all activities.

Fetches stream data for each run, extracts ~130 features
(domain metrics + catch22 shape features), and stores them
in the run_profiles table for instant cross-history queries.

Usage:
    # Process all unprocessed runs:
    python scripts/compute_profiles.py --user ben

    # Reprocess everything:
    python scripts/compute_profiles.py --user ben --force

    # Process last N days only:
    python scripts/compute_profiles.py --user ben --days 30
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from strava_pipeline.analysis.run_profile import compute_profile, profile_summary


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ICU_API = "https://intervals.icu/api/v1"
STREAM_TYPES = "heartrate,velocity_smooth,cadence,altitude,distance,time"

# Rate limiting
DELAY_BETWEEN_ACTIVITIES = 0.5  # seconds


def _get_credentials(username: str) -> tuple[str, str]:
    """Get intervals.icu credentials from Supabase."""
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/pacetrace_users",
        params={"username": f"eq.{username}", "select": "icu_api_key,icu_athlete_id"},
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=10.0,
    )
    data = resp.json()
    if data and data[0].get("icu_api_key"):
        return data[0]["icu_api_key"], data[0].get("icu_athlete_id", "0")
    print(f"ERROR: No intervals.icu credentials for user '{username}'")
    sys.exit(1)


def _get_existing_profiles(athlete_id: str) -> set[str]:
    """Get set of activity IDs that already have profiles."""
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/run_profiles",
        params={
            "athlete_id": f"eq.{athlete_id}",
            "select": "activity_id",
        },
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=10.0,
    )
    if resp.status_code == 200:
        return {r["activity_id"] for r in resp.json()}
    return set()


def _save_profile(profile_data: dict):
    """Upsert a profile to Supabase."""
    # Clean NaN/Inf values
    for k, v in list(profile_data.items()):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            profile_data[k] = None
        elif isinstance(v, dict):
            for dk, dv in list(v.items()):
                if isinstance(dv, float) and (math.isnan(dv) or math.isinf(dv)):
                    v[dk] = None

    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/run_profiles",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        json=profile_data,
        timeout=10.0,
    )
    return resp.status_code in (200, 201)


async def process_activities(username: str, days: int = 365, force: bool = False):
    """Main processing loop."""
    api_key, athlete_id = _get_credentials(username)

    print(f"Processing runs for {username} (athlete {athlete_id})")
    print(f"Looking back {days} days...")

    # Get existing profiles (skip if not forcing)
    existing = set() if force else _get_existing_profiles(athlete_id)
    if existing:
        print(f"  {len(existing)} profiles already computed (use --force to reprocess)")

    # List all activities
    oldest = str(date.today() - timedelta(days=days))
    async with httpx.AsyncClient(
        base_url=ICU_API,
        auth=httpx.BasicAuth("API_KEY", api_key),
        timeout=30.0,
    ) as client:
        resp = await client.get(f"/athlete/{athlete_id}/activities", params={"oldest": oldest})
        resp.raise_for_status()
        all_activities = resp.json()

    print(f"Found {len(all_activities)} total activities")

    # Filter to runs and fetch full data
    processed = 0
    skipped = 0
    failed = 0

    async with httpx.AsyncClient(
        base_url=ICU_API,
        auth=httpx.BasicAuth("API_KEY", api_key),
        timeout=30.0,
    ) as client:
        for i, act_summary in enumerate(all_activities):
            aid = str(act_summary["id"])

            if aid in existing:
                skipped += 1
                continue

            # Fetch full activity data
            try:
                act_resp = await client.get(f"/activity/{aid}")
                act_resp.raise_for_status()
                act = act_resp.json()
            except Exception as e:
                print(f"  [{i+1}] ✗ {aid} — failed to fetch activity: {e}")
                failed += 1
                continue

            # Check if it's a run
            act_type = (act.get("type") or "").lower()
            if act_type not in ("run", "trailrun", "virtualrun"):
                skipped += 1
                continue

            # Check for the Strava restriction
            if act.get("_note") and "not available" in act.get("_note", ""):
                skipped += 1
                continue

            dt = (act.get("start_date_local") or "")[:10]
            name = act.get("name", "Untitled")

            # Fetch streams
            try:
                stream_resp = await client.get(
                    f"/activity/{aid}/streams.json",
                    params={"types": STREAM_TYPES},
                )
                stream_resp.raise_for_status()
                raw_streams = stream_resp.json()
            except Exception as e:
                print(f"  [{i+1}] ✗ {dt} {name} — failed to fetch streams: {e}")
                failed += 1
                await asyncio.sleep(DELAY_BETWEEN_ACTIVITIES)
                continue

            # Parse streams into dict
            streams = {}
            for s in raw_streams:
                if "type" in s and "data" in s:
                    streams[s["type"]] = s["data"]

            if not streams.get("time") or len(streams["time"]) < 30:
                print(f"  [{i+1}] — {dt} {name} — too short, skipping")
                skipped += 1
                continue

            # Compute profile
            try:
                profile = compute_profile(
                    activity_id=aid,
                    athlete_id=athlete_id,
                    streams=streams,
                    activity_data=act,
                )
                profile_data = profile.to_db_row()
                summary = profile_summary(profile)
            except Exception as e:
                print(f"  [{i+1}] ✗ {dt} {name} — profile computation failed: {e}")
                failed += 1
                await asyncio.sleep(DELAY_BETWEEN_ACTIVITIES)
                continue

            # Save to DB
            if _save_profile(profile_data):
                processed += 1
                print(f"  [{i+1}] ✓ {dt} {name} — {summary}")
            else:
                failed += 1
                print(f"  [{i+1}] ✗ {dt} {name} — failed to save")

            await asyncio.sleep(DELAY_BETWEEN_ACTIVITIES)

    print()
    print(f"Done! {processed} computed, {skipped} skipped, {failed} failed")


def main():
    parser = argparse.ArgumentParser(description="Compute run profiles for all activities")
    parser.add_argument("--user", default="ben", help="PaceTrace username")
    parser.add_argument("--days", type=int, default=365, help="Days to look back")
    parser.add_argument("--force", action="store_true", help="Reprocess existing profiles")
    args = parser.parse_args()

    asyncio.run(process_activities(args.user, args.days, args.force))


if __name__ == "__main__":
    main()
