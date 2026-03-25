"""
Historical weather + air quality via Open-Meteo (free, no API key required).

Fetches hourly data for the day of a run and picks the hour closest to
the run's start time. Works for any date from 1940 onwards.

APIs used:
  Weather:     https://archive-api.open-meteo.com/v1/archive
  Air quality: https://air-quality-api.open-meteo.com/v1/air-quality
"""
from __future__ import annotations

import requests
from datetime import datetime


# WMO Weather Interpretation Codes → human description
WMO_CODES = {
    0:  "Clear sky",
    1:  "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Light freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Rain showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Thunderstorm + heavy hail",
}

WIND_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def fetch_weather(lat: float, lng: float, start_dt: datetime) -> dict | None:
    """
    Fetch historical weather conditions at run start time.

    Returns dict with:
      temp_c, feels_like_c, humidity_pct, wind_kmh, wind_dir,
      precip_mm, weather_desc
    or None on failure.
    """
    date_str = start_dt.strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": lat,
                "longitude": lng,
                "start_date": date_str,
                "end_date": date_str,
                "hourly": ",".join([
                    "temperature_2m",
                    "apparent_temperature",
                    "relative_humidity_2m",
                    "precipitation",
                    "wind_speed_10m",
                    "wind_direction_10m",
                    "weather_code",
                ]),
                "wind_speed_unit": "kmh",
                "timezone": "UTC",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[weather] fetch failed: {e}")
        return None

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    if not times:
        return None

    idx = _closest_hour_index(times, start_dt)

    wind_deg = hourly["wind_direction_10m"][idx]
    wind_dir = WIND_DIRS[round(wind_deg / 22.5) % 16] if wind_deg is not None else None
    code     = hourly["weather_code"][idx]

    return {
        "temp_c":       hourly["temperature_2m"][idx],
        "feels_like_c": hourly["apparent_temperature"][idx],
        "humidity_pct": hourly["relative_humidity_2m"][idx],
        "wind_kmh":     hourly["wind_speed_10m"][idx],
        "wind_dir":     wind_dir,
        "precip_mm":    hourly["precipitation"][idx],
        "weather_desc": WMO_CODES.get(code, f"Code {code}") if code is not None else None,
    }


def fetch_aqi(lat: float, lng: float, start_dt: datetime) -> dict | None:
    """
    Fetch historical European AQI at run start time.

    Returns dict with aqi (0-500+), aqi_desc (Good/Fair/…) or None.
    """
    date_str = start_dt.strftime("%Y-%m-%d")
    try:
        resp = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": lat,
                "longitude": lng,
                "start_date": date_str,
                "end_date": date_str,
                "hourly": "european_aqi,pm2_5",
                "timezone": "UTC",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[aqi] fetch failed: {e}")
        return None

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    aqi_vals = hourly.get("european_aqi", [])
    if not times or not aqi_vals:
        return None

    idx = _closest_hour_index(times, start_dt)
    aqi = aqi_vals[idx]
    if aqi is None:
        return None

    return {"aqi": int(aqi), "aqi_desc": _aqi_label(int(aqi))}


def _closest_hour_index(times: list[str], dt: datetime) -> int:
    """Return index of the hourly time string closest to dt (UTC)."""
    target = dt.strftime("%Y-%m-%dT%H:00")
    best_idx = 0
    for i, t in enumerate(times):
        if t <= target:
            best_idx = i
        else:
            break
    return best_idx


def _aqi_label(aqi: int) -> str:
    if aqi <= 20:  return "Good"
    if aqi <= 40:  return "Fair"
    if aqi <= 60:  return "Moderate"
    if aqi <= 80:  return "Poor"
    if aqi <= 100: return "Very Poor"
    return "Extremely Poor"
