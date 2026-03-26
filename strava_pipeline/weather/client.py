"""
Open-Meteo weather client for PaceTrace.

Fetches historical weather conditions for a given GPS coordinate and time.
Free API, no key needed. Used to contextualise running performance —
a "slow" run might just be a hot day.

https://open-meteo.com/en/docs/historical-weather-api
"""

import httpx
from datetime import date, datetime
from typing import Any, Optional


ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes → human descriptions
WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Light freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Light snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


def _describe_weather_code(code: int | None) -> str:
    if code is None:
        return "—"
    return WMO_CODES.get(code, f"Code {code}")


def _feels_like(temp_c: float, humidity: float, wind_kmh: float) -> float:
    """Approximate 'feels like' temperature considering humidity and wind."""
    # Heat index (when warm and humid)
    if temp_c >= 20 and humidity >= 40:
        # Simplified Steadman formula
        hi = temp_c + 0.33 * (humidity / 100 * 6.105 * (17.27 * temp_c / (237.7 + temp_c))) - 4.0
        return round(hi, 1)
    # Wind chill (when cold and windy)
    if temp_c <= 10 and wind_kmh > 5:
        wc = 13.12 + 0.6215 * temp_c - 11.37 * (wind_kmh ** 0.16) + 0.3965 * temp_c * (wind_kmh ** 0.16)
        return round(wc, 1)
    return temp_c


def _running_impact(temp_c: float, humidity: float, dew_point: float | None = None) -> str:
    """Describe impact on running performance — factual, not judgmental."""
    # Dew point is the best single metric for running comfort
    if dew_point is not None:
        if dew_point >= 21:
            return "very tough conditions for running — body struggles to cool"
        elif dew_point >= 16:
            return "warm and sticky — pace will naturally be slower"
        elif dew_point >= 10:
            return "comfortable running weather"
        elif dew_point >= 0:
            return "good conditions — cool and dry"
        else:
            return "cold and dry"

    # Fallback without dew point
    if temp_c >= 28 and humidity >= 60:
        return "very tough conditions for running — body struggles to cool"
    elif temp_c >= 22 and humidity >= 50:
        return "warm and sticky — pace will naturally be slower"
    elif 5 <= temp_c <= 15:
        return "good conditions — cool"
    elif temp_c < 0:
        return "cold — airways and muscles need extra warmup"
    return "comfortable running weather"


async def get_weather_for_activity(
    lat: float,
    lon: float,
    start_time: str | datetime,
    duration_secs: int = 3600,
) -> dict[str, Any] | None:
    """
    Fetch weather conditions for a run.

    Returns dict with: temp, feels_like, humidity, dew_point, wind_speed,
    wind_gusts, precipitation, weather_description, running_impact.

    Uses the hourly value closest to the activity start time.
    """
    if isinstance(start_time, str):
        # Handle various ISO formats
        start_time = start_time.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(start_time)
        except ValueError:
            return None
    else:
        dt = start_time

    activity_date = dt.date()
    hour = dt.hour

    # Use forecast API for recent/today, archive for historical
    is_recent = (date.today() - activity_date).days <= 2
    url = FORECAST_URL if is_recent else ARCHIVE_URL

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": str(activity_date),
        "end_date": str(activity_date),
        "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m,wind_gusts_10m,precipitation,weather_code",
        "timezone": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    if not times:
        return None

    # Find the hour index closest to the activity start
    idx = min(hour, len(times) - 1)

    def _val(key: str, i: int) -> Any:
        arr = hourly.get(key, [])
        return arr[i] if i < len(arr) else None

    temp = _val("temperature_2m", idx)
    humidity = _val("relative_humidity_2m", idx)
    dew_point = _val("dew_point_2m", idx)
    wind = _val("wind_speed_10m", idx)
    gusts = _val("wind_gusts_10m", idx)
    precip = _val("precipitation", idx)
    code = _val("weather_code", idx)

    if temp is None:
        return None

    feels = _feels_like(temp, humidity or 50, wind or 0)
    impact = _running_impact(temp, humidity or 50, dew_point)

    return {
        "temp_c": temp,
        "feels_like_c": feels,
        "humidity_pct": humidity,
        "dew_point_c": dew_point,
        "wind_kmh": wind,
        "wind_gusts_kmh": gusts,
        "precipitation_mm": precip,
        "weather_code": code,
        "weather_description": _describe_weather_code(code),
        "running_impact": impact,
    }


def format_weather(w: dict) -> str:
    """Format weather dict into a readable summary for MCP output."""
    parts = []
    parts.append(f"{w['weather_description']}")
    parts.append(f"{w['temp_c']:.0f}°C (feels like {w['feels_like_c']:.0f}°C)")

    if w.get("humidity_pct"):
        parts.append(f"{w['humidity_pct']:.0f}% humidity")
    if w.get("dew_point_c") is not None:
        parts.append(f"dew point {w['dew_point_c']:.0f}°C")
    if w.get("wind_kmh"):
        wind_str = f"wind {w['wind_kmh']:.0f} km/h"
        if w.get("wind_gusts_kmh") and w["wind_gusts_kmh"] > w["wind_kmh"] * 1.5:
            wind_str += f" (gusts {w['wind_gusts_kmh']:.0f})"
        parts.append(wind_str)
    if w.get("precipitation_mm") and w["precipitation_mm"] > 0:
        parts.append(f"{w['precipitation_mm']:.1f}mm rain")

    return " | ".join(parts)
