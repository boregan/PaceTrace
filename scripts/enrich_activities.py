#!/usr/bin/env python3
"""
Backfill enrichment data for existing activities.

For each activity that has a start location (lat/lng) and is missing
weather data, fetches: weather conditions, air quality, daylight phase,
and shoe km at time of run.

Usage:
    python scripts/enrich_activities.py --user ben
    python scripts/enrich_activities.py --user ben --limit 50
    python scripts/enrich_activities.py --user ben --force   # re-fetch everything

Requires: SUPABASE_URL, SUPABASE_SERVICE_KEY env vars
Optional: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_ATHLETE_ID
          (for shoe name lookup — skipped if unavailable)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from strava_pipeline.db.activities import (
    get_activities_in_range,
    upsert_enrichment,
)
from strava_pipeline.db.tokens import get_tokens_by_username
from strava_pipeline.enrichment.weather import fetch_weather, fetch_aqi
from strava_pipeline.enrichment.daylight import get_daylight_phase
from strava_pipeline.enrichment.gear import get_gear_name, shoe_km_at_run
from strava_pipeline.utils.user_loader import get_user_by_name


def _resolve_athlete(username: str) -> tuple[int, object | None]:
    """Return (athlete_id, strava_client_or_None)."""
    u = get_user_by_name(username)
    if u:
        athlete_id = int(u["STRAVA_ATHLETE_ID"])
    else:
        tokens = get_tokens_by_username(username)
        if not tokens:
            print(f"User '{username}' not found.")
            sys.exit(1)
        athlete_id = int(tokens["athlete_id"])

    # Try to get a Strava client for gear lookups (optional)
    strava = None
    try:
        from strava_pipeline.auth.token_manager import get_client_from_db, get_client
        if u:
            env_path = Path(__file__).parent.parent / "config" / "users" / f"{username}.env"
            strava = get_client(env_path)
        else:
            strava = get_client_from_db(athlete_id)
    except Exception as e:
        print(f"[gear] Strava client unavailable — shoe names will be skipped: {e}")

    return athlete_id, strava


def enrich(username: str, limit: int | None = None, force: bool = False) -> None:
    athlete_id, strava = _resolve_athlete(username)

    activities = get_activities_in_range(athlete_id, "2000-01-01", "2099-12-31")
    runs = [
        a for a in activities
        if a.get("sport_type") in ("Run", "VirtualRun", "TrailRun")
    ]

    # Filter to those needing enrichment
    if not force:
        runs = [a for a in runs if not a.get("weather_desc")]

    if limit:
        runs = runs[:limit]

    print(f"Enriching {len(runs)} activities for {username}...")

    # Cache gear names to avoid repeat API calls
    gear_cache: dict[str, str | None] = {}

    for i, run in enumerate(runs, 1):
        sid = run["strava_id"]
        lat = run.get("start_lat")
        lng = run.get("start_lng")
        start_date = run.get("start_date", "")
        gear_id    = run.get("gear_id")

        enrichment: dict = {}

        # Weather + AQI (requires location)
        if lat and lng and start_date:
            try:
                dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                dt_utc = dt.replace(tzinfo=None)  # work in UTC

                weather = fetch_weather(lat, lng, dt_utc)
                if weather:
                    enrichment["weather_temp_c"]  = weather.get("temp_c")
                    enrichment["weather_feels_c"] = weather.get("feels_like_c")
                    enrichment["weather_humidity"] = weather.get("humidity_pct")
                    enrichment["weather_wind_kmh"] = weather.get("wind_kmh")
                    wind_desc = f"{weather['wind_kmh']:.0f}km/h {weather['wind_dir']}" \
                                if weather.get("wind_dir") else None
                    enrichment["weather_desc"]    = weather.get("weather_desc")
                    enrichment["weather_precip_mm"] = weather.get("precip_mm")

                aqi = fetch_aqi(lat, lng, dt_utc)
                if aqi:
                    enrichment["aqi"]      = aqi["aqi"]
                    enrichment["aqi_desc"] = aqi["aqi_desc"]

                enrichment["daylight_phase"] = get_daylight_phase(dt_utc, lat, lng)

                time.sleep(0.15)  # be polite to Open-Meteo

            except Exception as e:
                print(f"  [{sid}] weather error: {e}")
        else:
            # No location — still set daylight from start_date time if available
            if start_date:
                try:
                    dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                    hour = dt.hour
                    if hour < 5:
                        enrichment["daylight_phase"] = "pre-dawn / night"
                    elif hour < 8:
                        enrichment["daylight_phase"] = "morning"
                    elif hour < 12:
                        enrichment["daylight_phase"] = "late morning"
                    elif hour < 15:
                        enrichment["daylight_phase"] = "midday / afternoon"
                    elif hour < 19:
                        enrichment["daylight_phase"] = "afternoon / evening"
                    else:
                        enrichment["daylight_phase"] = "evening / night"
                except Exception:
                    pass

        # Shoe name + km
        if gear_id:
            if gear_id not in gear_cache:
                gear_cache[gear_id] = get_gear_name(strava, gear_id) if strava else None
            name = gear_cache[gear_id]
            if name:
                enrichment["shoe_name"] = name
                km = shoe_km_at_run(athlete_id, gear_id, start_date[:10] + "T23:59:59")
                if km is not None:
                    enrichment["shoe_km_at_run"] = km

        if enrichment:
            upsert_enrichment(sid, enrichment)
            marker = "☀" if enrichment.get("weather_desc") else "·"
            print(f"  {marker} [{i}/{len(runs)}] {sid}  {start_date[:10]}  "
                  f"{enrichment.get('weather_desc', '')}  "
                  f"{enrichment.get('daylight_phase', '')}  "
                  f"{enrichment.get('shoe_name', '')}")
        else:
            print(f"  · [{i}/{len(runs)}] {sid}  {start_date[:10]}  (no location — skipped)")

    print(f"\nDone. {len(runs)} activities processed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich activities with weather, AQI, daylight, shoe data")
    parser.add_argument("--user",  default="ben", help="Athlete username")
    parser.add_argument("--limit", type=int,      help="Max activities to process")
    parser.add_argument("--force", action="store_true", help="Re-enrich even if data exists")
    args = parser.parse_args()

    enrich(args.user, limit=args.limit, force=args.force)
