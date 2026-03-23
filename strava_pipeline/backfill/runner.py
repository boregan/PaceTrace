from __future__ import annotations

"""
Orchestrates the full backfill for one or all users.

1. Get authenticated client for each user
2. Fetch and store all run activities
3. Fetch and store streams for all run activities (new + any missing)
"""
import os
from dotenv import load_dotenv

from strava_pipeline.auth.token_manager import get_client
from strava_pipeline.backfill.activity_fetcher import fetch_and_store_activities
from strava_pipeline.backfill.stream_fetcher import fetch_and_store_streams
from strava_pipeline.db.activities import get_existing_strava_ids
from strava_pipeline.db.streams import has_stream
from strava_pipeline.utils.rate_limiter import remaining
from strava_pipeline.utils.user_loader import get_all_users, get_user_by_name


def run(
    user_name: str | None = None,
    after: int | None = None,
    streams_only: bool = False,
    dry_run: bool = False,
) -> None:
    """
    Run the backfill pipeline.

    Args:
        user_name: Specific user to process (None = all users).
        after: Only process activities after this Unix timestamp.
        streams_only: Skip activity fetch, only fill missing streams.
        dry_run: Print what would happen without writing to DB.
    """
    load_dotenv()  # load STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, SUPABASE_* from .env

    users = [get_user_by_name(user_name)] if user_name else get_all_users()
    users = [u for u in users if u is not None]

    if not users:
        print(f"[runner] No users found.")
        return

    print(f"[runner] Processing {len(users)} user(s).")

    for user in users:
        name = user["_user_name"]
        env_path = user["_env_path"]
        athlete_id = int(user["STRAVA_ATHLETE_ID"])

        print(f"\n{'='*60}")
        print(f"[runner] User: {name} (athlete_id={athlete_id})")
        print(f"[runner] Rate limit status: {remaining()}")

        client = get_client(env_path)

        if not streams_only:
            new_activity_ids = fetch_and_store_activities(
                client=client,
                athlete_id=athlete_id,
                after=after,
                dry_run=dry_run,
            )
        else:
            new_activity_ids = []

        # Backfill streams for ALL runs missing a stream record (not just new ones)
        all_run_ids = get_existing_strava_ids(athlete_id)
        missing_streams = [aid for aid in all_run_ids if not has_stream(aid)]

        print(f"[runner] {len(missing_streams)} activities missing stream data.")

        if missing_streams:
            fetch_and_store_streams(
                client=client,
                activity_ids=missing_streams,
                dry_run=dry_run,
            )

    print(f"\n[runner] Backfill complete.")
