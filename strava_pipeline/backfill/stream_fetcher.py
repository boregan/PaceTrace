"""
Fetches stream data for a list of activity IDs and stores in Supabase.
Skips activities that already have streams stored (resumable).
"""

from __future__ import annotations
from stravalib.client import Client

from strava_pipeline.db.streams import has_stream, streams_from_stravalib, upsert_stream
from strava_pipeline.utils.rate_limiter import throttle

STREAM_TYPES = ["time", "heartrate", "velocity_smooth", "altitude", "distance", "cadence"]


def fetch_and_store_streams(
    client: Client,
    activity_ids: list[int],
    dry_run: bool = False,
) -> int:
    """
    Fetch streams for each activity_id and store in Supabase.

    Args:
        client: Authenticated stravalib Client.
        activity_ids: List of Strava activity IDs to process.
        dry_run: If True, print but don't write.

    Returns:
        Number of streams successfully stored.
    """
    stored = 0
    skipped = 0

    for i, activity_id in enumerate(activity_ids):
        if has_stream(activity_id):
            skipped += 1
            continue

        throttle()

        try:
            stream_set = client.get_activity_streams(
                activity_id,
                types=STREAM_TYPES,
                resolution="high",
            )
        except Exception as e:
            print(f"  [stream_fetcher] WARN: failed to fetch streams for {activity_id}: {e}")
            continue

        if stream_set is None:
            print(f"  [stream_fetcher] No streams returned for {activity_id}")
            continue

        stream_data = streams_from_stravalib(stream_set)

        if dry_run:
            length = len(stream_data.get("time_s") or [])
            print(f"  [dry-run] Would store {length} data points for activity {activity_id}")
        else:
            upsert_stream(activity_id, stream_data)
            length = len(stream_data.get("time_s") or [])
            print(f"  [{i+1}/{len(activity_ids)}] Stored {length} points for activity {activity_id}")

        stored += 1

    print(f"[stream_fetcher] Done. {stored} stored, {skipped} already present.")
    return stored
