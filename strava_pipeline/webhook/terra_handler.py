"""
Terra webhook handler.

Terra POSTs health data events here as they arrive from wearable devices:
  auth    — user successfully connected a device
  deauth  — user disconnected
  sleep   — sleep session completed
  daily   — daily wellness summary updated

All requests are HMAC-SHA256 signed — we verify before processing.
Respond within 8 seconds or Terra will retry (10 attempts, exponential backoff).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request, Response

from strava_pipeline.db.terra_users import (
    deactivate_terra_user,
    get_athlete_id_by_reference,
    get_athlete_id_by_terra_user,
    upsert_terra_user,
)
from strava_pipeline.db.wellness import upsert_wellness
from strava_pipeline.enrichment.terra import (
    parse_daily_payload,
    parse_sleep_payload,
    verify_webhook_signature,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/terra")
async def terra_webhook(request: Request) -> Response:
    raw_body = await request.body()
    sig      = request.headers.get("terra-signature", "")

    if not verify_webhook_signature(raw_body, sig):
        log.warning("Terra webhook: invalid signature — rejecting request")
        return Response(status_code=403, content="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    event_type = payload.get("type", "")
    user_info  = payload.get("user") or {}
    terra_uid  = user_info.get("user_id", "")
    ref_id     = user_info.get("reference_id", "")
    provider   = user_info.get("provider", "")

    log.info("Terra event: type=%s provider=%s terra_uid=%s ref=%s",
             event_type, provider, terra_uid[:8] if terra_uid else "", ref_id)

    # ── Auth events ────────────────────────────────────────────────────────────
    if event_type == "auth":
        athlete_id = get_athlete_id_by_reference(ref_id)
        if athlete_id and terra_uid:
            upsert_terra_user(athlete_id, terra_uid, provider, ref_id)
            log.info("Terra: athlete %s connected %s", athlete_id, provider)
        else:
            log.warning("Terra auth: could not resolve reference_id='%s'", ref_id)
        return Response(status_code=200)

    if event_type in ("deauth", "access_revoked"):
        if terra_uid:
            deactivate_terra_user(terra_uid)
            log.info("Terra: deactivated %s (%s)", terra_uid[:8], provider)
        return Response(status_code=200)

    # ── Data events — need a known athlete ────────────────────────────────────
    athlete_id = get_athlete_id_by_terra_user(terra_uid) if terra_uid else None
    if not athlete_id:
        # Respond 200 — an unknown user isn't a server error, just ignore
        log.warning("Terra data event for unknown terra_user_id=%s", terra_uid)
        return Response(status_code=200)

    data_items = payload.get("data") or []
    synced = 0

    if event_type == "sleep":
        for item in data_items:
            row = parse_sleep_payload(item)
            if row:
                upsert_wellness({
                    "athlete_id": athlete_id,
                    "source":     provider.lower(),
                    **row,
                })
                synced += 1
        log.info("Terra sleep: %d records upserted for athlete %s", synced, athlete_id)

    elif event_type == "daily":
        for item in data_items:
            row = parse_daily_payload(item)
            if row:
                upsert_wellness({
                    "athlete_id": athlete_id,
                    "source":     provider.lower(),
                    **row,
                })
                synced += 1
        log.info("Terra daily: %d records upserted for athlete %s", synced, athlete_id)

    # healthcheck, processing, rate_limit_hit, large_request_* — just acknowledge
    return Response(status_code=200)
