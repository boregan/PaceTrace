"""
Terra API client for wearable wellness data.

Supports any device Terra integrates with:
  Garmin, Oura, Whoop, Apple Watch, Fitbit, Samsung, Polar, Suunto, ...

The connect flow is a hosted OAuth widget — no credentials stored.
We just generate a session URL and redirect the user there.

Requires env vars:
  TERRA_DEV_ID        — from Terra dashboard
  TERRA_API_KEY       — from Terra dashboard
  TERRA_SIGNING_SECRET — from Terra dashboard (for webhook verification)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

TERRA_BASE = "https://api.tryterra.co/v2"


def _headers() -> dict:
    return {
        "dev-id": os.environ["TERRA_DEV_ID"],
        "x-api-key": os.environ["TERRA_API_KEY"],
        "Content-Type": "application/json",
    }


# ── Auth ───────────────────────────────────────────────────────────────────────

def generate_widget_session(reference_id: str, success_url: str, failure_url: str) -> str:
    """
    Create a Terra hosted widget session and return the URL to redirect the user to.

    reference_id should be the athlete's username (e.g. "ben") so we can
    map the Terra user_id back to our athlete when the webhook fires.

    The widget URL expires in 15 minutes.
    """
    resp = httpx.post(
        f"{TERRA_BASE}/auth/generateWidgetSession",
        headers=_headers(),
        json={
            "reference_id":             reference_id,
            "auth_success_redirect_url": success_url,
            "auth_failure_redirect_url": failure_url,
            "language":                 "en",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Terra widget session failed: {data}")
    return data["url"]


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_sleep(terra_user_id: str, days: int = 30) -> list[dict]:
    """Fetch sleep sessions for the past n days (synchronous, inline response)."""
    end_ts   = int(time.time())
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    resp = httpx.get(
        f"{TERRA_BASE}/sleep",
        headers=_headers(),
        params={
            "user_id":      terra_user_id,
            "start_date":   start_ts,
            "end_date":     end_ts,
            "to_webhook":   "false",
            "with_samples": "false",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data") or []


def fetch_daily(terra_user_id: str, days: int = 30) -> list[dict]:
    """Fetch daily wellness summaries for the past n days."""
    end_ts   = int(time.time())
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    resp = httpx.get(
        f"{TERRA_BASE}/daily",
        headers=_headers(),
        params={
            "user_id":      terra_user_id,
            "start_date":   start_ts,
            "end_date":     end_ts,
            "to_webhook":   "false",
            "with_samples": "false",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data") or []


# ── Webhook verification ───────────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify a Terra webhook HMAC-SHA256 signature.

    Header format: "t=<unix_timestamp>,v1=<hex_signature>"
    Signed payload: "<timestamp>.<raw_body_string>"
    """
    secret = os.environ.get("TERRA_SIGNING_SECRET", "")
    if not secret or not signature_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts    = parts["t"]
        sig   = parts["v1"]
        payload_to_sign = f"{ts}.{raw_body.decode('utf-8')}"
        expected = hmac.new(
            secret.encode(), payload_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


# ── Payload parsers ────────────────────────────────────────────────────────────

def parse_sleep_payload(item: dict) -> dict | None:
    """
    Extract wellness fields from a single Terra sleep data item.

    Returns a dict compatible with athlete_wellness, keyed by date (YYYY-MM-DD).
    Returns None if there's no useful data or it's a nap.
    """
    meta = item.get("metadata") or {}
    start = meta.get("start_time") or ""
    if not start:
        return None

    # Skip naps — only process main sleep sessions
    if meta.get("is_nap"):
        return None

    date_str = start[:10]  # YYYY-MM-DD
    row: dict = {"date": date_str}

    # HRV (RMSSD — the gold standard for recovery)
    hr_summary = (item.get("heart_rate_data") or {}).get("summary") or {}
    hrv = hr_summary.get("avg_hrv_rmssd")
    if hrv:
        row["hrv_last_night"] = round(float(hrv), 1)

    # Resting HR
    rhr = hr_summary.get("resting_hr_bpm")
    if rhr:
        row["resting_hr"] = int(rhr)

    # Sleep score — prefer provider-native, fall back to Terra-enriched
    scores     = item.get("scores") or {}
    enrichment = item.get("data_enrichment") or {}
    sleep_score = scores.get("sleep") or enrichment.get("sleep_score")
    if sleep_score is not None:
        row["sleep_score"] = int(sleep_score)

    # Total sleep duration
    asleep = (item.get("sleep_durations_data") or {}).get("asleep") or {}
    duration_s = asleep.get("duration_asleep_state_seconds")
    if duration_s:
        row["sleep_duration_s"] = int(duration_s)

    # Sleep stage breakdown
    light_s = asleep.get("duration_light_sleep_state_seconds")
    deep_s  = asleep.get("duration_deep_sleep_state_seconds")
    rem_s   = asleep.get("duration_REM_sleep_state_seconds")
    if light_s: row["sleep_light_s"] = int(light_s)
    if deep_s:  row["sleep_deep_s"]  = int(deep_s)
    if rem_s:   row["sleep_rem_s"]   = int(rem_s)

    # Readiness score → also drives hrv_status label
    readiness_raw  = (item.get("readiness_data") or {}).get("readiness")
    readiness_enr  = enrichment.get("readiness_score")
    readiness      = readiness_raw or readiness_enr
    if readiness is not None:
        row["readiness_score"] = int(readiness)
        row["hrv_status"]      = _readiness_label(int(readiness))

    if len(row) <= 1:  # only "date" — nothing useful
        return None
    return row


def parse_daily_payload(item: dict) -> dict | None:
    """
    Extract wellness fields from a single Terra daily summary item.

    Daily payloads fill in stress + body battery / readiness data,
    and can patch resting HR / HRV on days without a recorded sleep.
    """
    meta  = item.get("metadata") or {}
    start = meta.get("start_time") or ""
    if not start:
        return None

    date_str = start[:10]
    row: dict = {"date": date_str}

    # Stress
    stress     = item.get("stress_data") or {}
    avg_stress = stress.get("avg_stress_level")
    if avg_stress is not None:
        row["stress_avg"] = int(avg_stress)

    # Resting HR + HRV from daily summary
    hr_summary = (item.get("heart_rate_data") or {}).get("summary") or {}
    rhr = hr_summary.get("resting_hr_bpm")
    hrv = hr_summary.get("avg_hrv_rmssd")
    if rhr: row["resting_hr"]      = int(rhr)
    if hrv: row["hrv_last_night"]  = round(float(hrv), 1)

    # Readiness / body battery — provider recovery score or Terra-enriched
    enrichment = item.get("data_enrichment") or {}
    scores     = item.get("scores") or {}
    readiness  = enrichment.get("readiness_score") or scores.get("recovery")
    if readiness is not None:
        row["readiness_score"]  = int(readiness)
        row["body_battery_high"] = int(readiness)   # closest universal equivalent

    if len(row) <= 1:
        return None
    return row


def _readiness_label(score: int) -> str:
    """Map a 0–100 readiness/recovery score to an HRV status label."""
    if score >= 85: return "BALANCED"
    if score >= 65: return "GOOD"
    if score >= 45: return "STRAINED"
    return "LOW"
