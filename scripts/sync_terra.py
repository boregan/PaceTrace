#!/usr/bin/env python3
"""
Backfill historical wellness data from Terra-connected wearables.

Fetches sleep + daily summaries for each of the athlete's connected Terra
devices and upserts them into the athlete_wellness table.

Usage:
    python scripts/sync_terra.py --user ben
    python scripts/sync_terra.py --user ben --days 60
    python scripts/sync_terra.py --all --days 30   # sync every connected athlete

Requires env vars: TERRA_DEV_ID, TERRA_API_KEY
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from strava_pipeline.db.terra_users import (
    get_terra_users,
    get_athlete_id_by_reference,
    list_all_terra_users,
)
from strava_pipeline.db.wellness import upsert_wellness
from strava_pipeline.enrichment.terra import (
    fetch_sleep,
    fetch_daily,
    parse_sleep_payload,
    parse_daily_payload,
)


def sync_athlete(athlete_id: int, username: str, days: int = 30) -> None:
    connections = get_terra_users(athlete_id)
    if not connections:
        print(f"  No Terra connections for {username}. Connect at /terra/connect?user={username}")
        return

    for conn in connections:
        terra_uid = conn["terra_user_id"]
        provider  = conn["provider"]
        print(f"\n  [{provider}] {terra_uid[:8]}...")

        # Sleep
        try:
            sleep_items = fetch_sleep(terra_uid, days=days)
            sleep_synced = 0
            for item in sleep_items:
                row = parse_sleep_payload(item)
                if row:
                    upsert_wellness({"athlete_id": athlete_id, "source": provider.lower(), **row})
                    hrv      = row.get("hrv_last_night", "—")
                    score    = row.get("sleep_score",    "—")
                    ready    = row.get("readiness_score","—")
                    dur_h    = f"{row['sleep_duration_s']/3600:.1f}h" if row.get("sleep_duration_s") else "—"
                    print(f"    ✓ sleep {row['date']}  HRV: {hrv}  "
                          f"score: {score}  readiness: {ready}  duration: {dur_h}")
                    sleep_synced += 1
            print(f"  → {sleep_synced} sleep records synced")
        except Exception as e:
            print(f"  [sleep error] {e}")

        time.sleep(0.5)

        # Daily
        try:
            daily_items = fetch_daily(terra_uid, days=days)
            daily_synced = 0
            for item in daily_items:
                row = parse_daily_payload(item)
                if row:
                    upsert_wellness({"athlete_id": athlete_id, "source": provider.lower(), **row})
                    stress  = row.get("stress_avg",      "—")
                    battery = row.get("body_battery_high","—")
                    print(f"    · daily {row['date']}  stress: {stress}  battery: {battery}")
                    daily_synced += 1
            print(f"  → {daily_synced} daily records synced")
        except Exception as e:
            print(f"  [daily error] {e}")

        time.sleep(0.5)


def sync_user(username: str, days: int = 30) -> None:
    athlete_id = get_athlete_id_by_reference(username)
    if not athlete_id:
        print(f"User '{username}' not found.")
        sys.exit(1)

    print(f"Syncing Terra data for {username} (athlete_id={athlete_id}, last {days} days)...")
    sync_athlete(athlete_id, username, days)
    print("\nDone.")


def sync_all(days: int = 30) -> None:
    all_connections = list_all_terra_users()
    if not all_connections:
        print("No Terra connections found in database.")
        return

    # Group by athlete_id
    by_athlete: dict[int, str] = {}
    for conn in all_connections:
        aid = conn["athlete_id"]
        if aid not in by_athlete:
            by_athlete[aid] = conn.get("reference_id", str(aid))

    print(f"Syncing {len(by_athlete)} athlete(s)...")
    for athlete_id, username in by_athlete.items():
        print(f"\n{'='*50}")
        print(f"Athlete: {username} (id={athlete_id})")
        sync_athlete(athlete_id, username, days)

    print("\nAll done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Terra wellness data")
    parser.add_argument("--user",  default="ben", help="Athlete username")
    parser.add_argument("--all",   action="store_true", help="Sync all connected athletes")
    parser.add_argument("--days",  type=int, default=30, help="Days to backfill (default: 30)")
    args = parser.parse_args()

    if args.all:
        sync_all(days=args.days)
    else:
        sync_user(args.user, days=args.days)
