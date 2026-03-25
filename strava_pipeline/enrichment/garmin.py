"""
Garmin Connect wellness data sync (unofficial API via garminconnect library).

Pulls daily HRV, sleep, body battery, stress, and resting HR.

Setup:
  pip install garminconnect
  Set env vars: GARMIN_EMAIL, GARMIN_PASSWORD

Note on presenting this data:
  Sleep and recovery metrics are personal and context-dependent.
  Low scores on a given day may reflect many legitimate factors —
  young children, shift work, illness, travel — not just training choices.
  Always frame these metrics constructively: what the athlete CAN do,
  not what they failed to achieve. Body battery and HRV trends over
  weeks matter more than any single night's reading.
"""
from __future__ import annotations

import os
from datetime import date, timedelta


def get_garmin_client():
    """
    Authenticate with Garmin Connect.
    Requires GARMIN_EMAIL and GARMIN_PASSWORD env vars.
    Handles 2FA interactively if required.
    """
    try:
        from garminconnect import Garmin, GarminConnectAuthenticationError
    except ImportError:
        raise ImportError("Run: pip install garminconnect")

    email    = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        raise ValueError("Set GARMIN_EMAIL and GARMIN_PASSWORD env vars")

    client = Garmin(email, password)

    try:
        client.login()
    except Exception as e:
        if "MFA" in str(e) or "2FA" in str(e) or "factor" in str(e).lower():
            code = input("Garmin 2FA code: ").strip()
            client.garth.resume(code)
        else:
            raise

    return client


def fetch_daily_wellness(client, date_str: str) -> dict | None:
    """
    Fetch all available wellness metrics for a given date (YYYY-MM-DD).

    Returns a dict with whatever data is available, or None if nothing
    could be fetched. Failures on individual metrics are silently skipped
    so a partial result is always returned if any data is available.
    """
    result: dict = {}

    # HRV
    try:
        hrv = client.get_hrv_data(date_str)
        summary = (hrv or {}).get("hrvSummary", {})
        if summary:
            result["hrv_weekly_avg"]  = summary.get("weeklyAvg")
            result["hrv_last_night"]  = summary.get("lastNight")
            result["hrv_status"]      = summary.get("status")  # BALANCED/UNBALANCED/POOR/LOW
    except Exception:
        pass

    # Sleep
    try:
        sleep = client.get_sleep_data(date_str)
        dto   = (sleep or {}).get("dailySleepDTO", {})
        if dto:
            result["sleep_duration_s"] = dto.get("sleepTimeSeconds")
            scores = dto.get("sleepScores") or {}
            result["sleep_score"]      = (scores.get("overall") or {}).get("value")
    except Exception:
        pass

    # Body battery (Garmin's energy reserve metric, 0-100)
    try:
        battery = client.get_body_battery(date_str)
        if battery:
            vals = battery[0].get("bodyBatteryValuesArray", []) if battery else []
            levels = [v[1] for v in vals if v and v[1] is not None]
            if levels:
                result["body_battery_high"] = max(levels)
                result["body_battery_low"]  = min(levels)
    except Exception:
        pass

    # Stress (0-100 scale; -1 means no data, <25 = rest, 26-50 = low, 51-75 = medium, >75 = high)
    try:
        stress = client.get_stress_data(date_str)
        avg = (stress or {}).get("avgStressLevel")
        if avg and avg > 0:
            result["stress_avg"] = avg
    except Exception:
        pass

    # Resting HR
    try:
        rhr_data = client.get_rhr_day(date_str)
        metrics  = (rhr_data or {}).get("allMetrics", {}).get("metricsMap", {})
        rhr_list = metrics.get("WELLNESS_RESTING_HEART_RATE", [])
        if rhr_list:
            result["resting_hr"] = rhr_list[0].get("value")
    except Exception:
        pass

    return result if result else None


def fetch_wellness_range(client, athlete_id: int, days: int = 30) -> list[dict]:
    """
    Fetch wellness data for the last N days and return as a list of DB rows.
    """
    rows = []
    today = date.today()
    for i in range(days):
        d = today - timedelta(days=i)
        date_str = d.isoformat()
        data = fetch_daily_wellness(client, date_str)
        if data:
            rows.append({"athlete_id": athlete_id, "date": date_str, **data})
    return rows
