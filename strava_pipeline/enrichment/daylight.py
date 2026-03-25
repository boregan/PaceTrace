"""
Daylight phase and sunrise/sunset calculation.

Pure Python — no external dependencies. Uses a simplified NOAA solar
position algorithm, accurate to within ~2 minutes for latitudes < 66°.

Phases returned:
  pre-dawn   04:00 → sunrise - 30min
  dawn       sunrise - 30min → sunrise
  morning    sunrise → sunrise + 2h
  late morning sunrise + 2h → 12:00
  midday     12:00 → 14:00 (local solar time)
  afternoon  14:00 → sunset - 1h
  evening    sunset - 1h → sunset
  dusk       sunset → sunset + 30min
  night      sunset + 30min → 04:00
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta


def get_daylight_phase(dt_utc: datetime, lat: float, lng: float) -> str:
    """
    Classify time of day relative to sunrise/sunset at the given location.

    Args:
        dt_utc: Run start time in UTC (timezone-naive)
        lat, lng: Activity location in decimal degrees

    Returns one of the phase strings listed in the module docstring.
    """
    sunrise_utc, sunset_utc = sunrise_sunset(dt_utc.date(), lat, lng)

    dawn_utc  = sunrise_utc - timedelta(minutes=30)
    dusk_end  = sunset_utc  + timedelta(minutes=30)

    hour_frac = dt_utc.hour + dt_utc.minute / 60

    if dt_utc < dawn_utc:
        return "night" if hour_frac < 4.0 else "pre-dawn"
    if dt_utc < sunrise_utc:
        return "dawn"
    if dt_utc < sunrise_utc + timedelta(hours=2):
        return "morning"
    if dt_utc < sunrise_utc + timedelta(hours=4):
        return "late morning"
    if dt_utc < sunset_utc - timedelta(hours=2):
        return "afternoon" if dt_utc.hour >= 12 else "midday"
    if dt_utc < sunset_utc - timedelta(hours=1):
        return "afternoon"
    if dt_utc < sunset_utc:
        return "evening"
    if dt_utc < dusk_end:
        return "dusk"
    return "night"


def sunrise_sunset(d: date, lat: float, lng: float) -> tuple[datetime, datetime]:
    """
    Compute sunrise and sunset times (UTC) for a given date and location.

    Based on the NOAA simplified solar position algorithm.
    """
    N = d.timetuple().tm_yday

    # Solar mean anomaly (degrees)
    M = (357.5291 + 0.98560028 * N) % 360

    # Equation of centre
    C = (1.9148 * math.sin(math.radians(M))
         + 0.0200 * math.sin(math.radians(2 * M))
         + 0.0003 * math.sin(math.radians(3 * M)))

    # Ecliptic longitude of the sun
    lam = (M + C + 180 + 102.9372) % 360

    # Solar noon (UTC, fractional hours) corrected for longitude
    solar_noon = 12 - lng / 15 - _equation_of_time(N) / 60

    # Declination
    sin_decl = math.sin(math.radians(lam)) * math.sin(math.radians(23.4397))
    decl     = math.asin(sin_decl)

    # Hour angle at sunrise (sun centre at horizon)
    lat_r = math.radians(lat)
    cos_ha = (math.sin(math.radians(-0.833)) - math.sin(lat_r) * math.sin(decl)) \
             / (math.cos(lat_r) * math.cos(decl))
    cos_ha = max(-1.0, min(1.0, cos_ha))  # clamp for polar regions
    ha = math.degrees(math.acos(cos_ha)) / 15  # hours

    rise_h = solar_noon - ha
    set_h  = solar_noon + ha

    base = datetime(d.year, d.month, d.day)
    return (base + timedelta(hours=rise_h), base + timedelta(hours=set_h))


def _equation_of_time(day_of_year: int) -> float:
    """Equation of time in minutes (approx)."""
    B = math.radians(360 / 365 * (day_of_year - 81))
    return 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)
