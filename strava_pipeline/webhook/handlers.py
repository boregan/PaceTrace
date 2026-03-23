"""
Strava webhook route handlers.

GET  /webhook  — hub challenge verification (one-time, during subscription setup)
POST /webhook  — incoming activity events
"""

from __future__ import annotations
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from strava_pipeline.auth.token_manager import get_client
from strava_pipeline.backfill.stream_fetcher import fetch_and_store_streams
from strava_pipeline.db.activities import activity_from_stravalib, upsert_activity
from strava_pipeline.utils.user_loader import get_user_by_athlete_id

router = APIRouter()


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
) -> JSONResponse:
    """Strava calls this once when you subscribe a webhook. Must echo back the challenge."""
    expected_token = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "")
    if hub_mode != "subscribe" or hub_verify_token != expected_token:
        raise HTTPException(status_code=403, detail="Verification failed")
    return JSONResponse({"hub.challenge": hub_challenge})


@router.post("/webhook")
async def receive_event(request: Request, background_tasks: BackgroundTasks) -> dict:
    """
    Receive Strava activity events. Returns 200 immediately; processes async.
    Strava requires a response within 2 seconds.
    """
    event = await request.json()

    object_type = event.get("object_type")
    aspect_type = event.get("aspect_type")
    activity_id = event.get("object_id")
    athlete_id = event.get("owner_id")

    if object_type == "activity" and aspect_type == "create":
        background_tasks.add_task(_process_new_activity, athlete_id, activity_id)

    return {"status": "received"}


async def _process_new_activity(athlete_id: int, activity_id: int) -> None:
    """Background task: fetch activity + streams and write to Supabase."""
    user = get_user_by_athlete_id(athlete_id)
    if user is None:
        print(f"[webhook] Unknown athlete_id={athlete_id}, ignoring event.")
        return

    try:
        client = get_client(user["_env_path"])

        from strava_pipeline.utils.rate_limiter import throttle
        throttle()
        activity = client.get_activity(activity_id)

        sport = str(activity.sport_type or activity.type or "")
        if sport not in {"Run", "VirtualRun", "TrailRun"}:
            print(f"[webhook] Skipping non-run activity {activity_id} (type={sport})")
            return

        row = activity_from_stravalib(activity)
        upsert_activity(row)
        print(f"[webhook] Stored activity {activity_id}: {activity.name}")

        fetch_and_store_streams(client=client, activity_ids=[activity_id])

    except Exception as e:
        print(f"[webhook] ERROR processing activity {activity_id}: {e}")
