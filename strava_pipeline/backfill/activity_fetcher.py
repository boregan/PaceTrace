from __future__ import annotations

"""
Fetches all run activities for an athlete and stores them in Supabase.
Skips activities already present (resumable).
"""

from stravalib.client import Client

from strava_pipeline.db.activities import (
    activity_from_stravalib,
    get_existing_strava_ids,
    upsert_activity,
)
from strava_pipeline.utils.rate_limiter import throttle

RUNS_ONLY = {"Run", "VirtualRun", "TrailRun"}


def fetch_and_store_activities(
    client: Client,
    athlete_id: int,
    after: int | None = None,
    dry_run: bool = False,
) -> list[int]:
    """
    Fetch all run activities and store new ones in Supabase.

    Args:
        client: Authenticated stravalib Client.
        athlete_id: Strava athlete ID (for checking existing records).
        after: Optional Unix timestamp — only fetch activities after this date.
        dry_run: If True, print what would be stored but don't write.

    Returns:
        List of new activity strava_ids that were stored.
    """
    print(f"[activity_fetcher] Loading existing IDs for athlete {athlete_id}...")
    existing = get_existing_strava_ids(athlete_id)
    print(f"[activity_fetcher] {len(existing)} activities already in database.")

    new_ids: list[int] = []
    total_fetched = 0
    total_skipped = 0

    # stravalib's get_activities returns a lazy iterator that handles pagination internally.
    # Each page fetch counts as one API request; we throttle every 30 activities.
    throttle()
    for activity in client.get_activities(after=after):
        total_fetched += 1

        if total_fetched % 30 == 0:
            throttle()

        sport = str(activity.sport_type or activity.type or "")
        if sport not in RUNS_ONLY:
            continue

        if activity.id in existing:
            total_skipped += 1
            continue

        row = activity_from_stravalib(activity)

        if dry_run:
            print(f"  [dry-run] Would store: {activity.id} - {activity.name}")
        else:
            upsert_activity(row)
            print(f"  Stored: {activity.id} - {activity.name} ({activity.start_date})")

        new_ids.append(activity.id)

    print(
        f"[activity_fetcher] Done. {total_fetched} fetched, "
        f"{len(new_ids)} new, {total_skipped} already stored."
    )
    return new_ids
