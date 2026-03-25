#!/usr/bin/env python3
"""
Sync daily wellness data from Garmin Connect.

Pulls HRV status, sleep score, body battery, and stress for each day.
Stores in the athlete_wellness table.

Usage:
    python scripts/sync_garmin.py --user ben
    python scripts/sync_garmin.py --user ben --days 60

Requires env vars: GARMIN_EMAIL, GARMIN_PASSWORD
Optional 2FA:      enter code interactively when prompted

Install dependency first:
    pip install garminconnect
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from strava_pipeline.db.wellness import upsert_wellness
from strava_pipeline.db.tokens import get_tokens_by_username
from strava_pipeline.enrichment.garmin import get_garmin_client, fetch_daily_wellness
from strava_pipeline.utils.user_loader import get_user_by_name
from datetime import date, timedelta


def _resolve_athlete_id(username: str) -> int:
    u = get_user_by_name(username)
    if u:
        return int(u["STRAVA_ATHLETE_ID"])
    tokens = get_tokens_by_username(username)
    if tokens:
        return int(tokens["athlete_id"])
    print(f"User '{username}' not found.")
    sys.exit(1)


def sync(username: str, days: int = 30) -> None:
    athlete_id = _resolve_athlete_id(username)

    print("Connecting to Garmin Connect...")
    client = get_garmin_client()
    print("Connected.\n")

    today   = date.today()
    synced  = 0
    skipped = 0

    for i in range(days):
        d        = today - timedelta(days=i)
        date_str = d.isoformat()

        data = fetch_daily_wellness(client, date_str)
        if data:
            upsert_wellness({"athlete_id": athlete_id, "date": date_str, **data})
            hrv   = data.get("hrv_last_night", "—")
            sleep = data.get("sleep_score", "—")
            batt  = data.get("body_battery_high", "—")
            print(f"  ✓ {date_str}  HRV: {hrv}  sleep: {sleep}  battery: {batt}")
            synced += 1
        else:
            skipped += 1

        time.sleep(0.3)  # rate limit Garmin API

    print(f"\nDone. {synced} days synced, {skipped} skipped (no data).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Garmin wellness data")
    parser.add_argument("--user", default="ben",  help="Athlete username")
    parser.add_argument("--days", type=int, default=30, help="Days to sync (default: 30)")
    args = parser.parse_args()

    sync(args.user, days=args.days)
