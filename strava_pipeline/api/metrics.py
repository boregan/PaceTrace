"""
Advanced running metrics computed from second-by-second stream data.

Functions work on raw lists from the Supabase streams table:
  time_s   list[int]        elapsed seconds
  dist_m   list[float]      cumulative metres
  hr       list[int|None]   bpm per second
  vel      list[float]      m/s per second
  alt      list[float]      metres above sea level
  cad      list[int|None]   steps per minute
"""
from __future__ import annotations

import bisect
import math
import statistics
from datetime import date, timedelta
from typing import Optional


# ── TRIMP ──────────────────────────────────────────────────────────────────────

def trimp_from_stream(
    hr_series: list,
    duration_s: float,
    hr_max: int,
    hr_rest: int = 55,
    gender: str = "male",
) -> float:
    """
    Banister's TRIMP via per-second HR integration.

    Integrates each second's contribution so intervals/fartlek are
    weighted correctly (high-HR seconds contribute more than low-HR).
    Returns TRIMP units comparable across sessions.
    """
    valid = [h for h in hr_series if h is not None and h > 0]
    if not valid or not duration_s:
        return 0.0

    a, b = (0.64, 1.92) if gender == "male" else (0.86, 1.67)
    total = 0.0
    for hr in valid:
        hrr = (hr - hr_rest) / (hr_max - hr_rest)
        hrr = max(0.0, min(1.0, hrr))
        total += hrr * a * math.exp(b * hrr)

    # Scale: each sample represents (duration_s / n_samples) seconds
    return total * (duration_s / len(valid)) / 60


def trimp_from_avg_hr(
    avg_hr: float,
    duration_s: float,
    hr_max: int,
    hr_rest: int = 55,
    gender: str = "male",
) -> float:
    """
    Fast TRIMP estimate using only avg HR + duration.
    Use when stream data is unavailable (accurate for steady-state runs).
    """
    if not avg_hr or not duration_s:
        return 0.0
    a, b = (0.64, 1.92) if gender == "male" else (0.86, 1.67)
    hrr = (avg_hr - hr_rest) / (hr_max - hr_rest)
    hrr = max(0.0, min(1.0, hrr))
    return (duration_s / 60) * hrr * a * math.exp(b * hrr)


# ── ATL / CTL / TSB ────────────────────────────────────────────────────────────

def compute_fitness_metrics(
    daily_loads: dict,
    target_date: Optional[date] = None,
) -> dict:
    """
    Compute ATL (fatigue), CTL (fitness), TSB (form) from daily TRIMP.

    Uses exponential weighted averages:
      ATL: 7-day time constant  → acute fatigue
      CTL: 42-day time constant → chronic fitness base
      TSB: CTL - ATL            → form / freshness

    Args:
        daily_loads: {date: trimp_float} for each day that has training
        target_date: compute up to this date (default: latest loaded day)

    Returns dict with atl, ctl, tsb, weekly_loads (last 10 weeks), daily_history
    """
    if not daily_loads:
        return {"atl": 0.0, "ctl": 0.0, "tsb": 0.0, "weekly_loads": [], "daily_history": []}

    if target_date is None:
        target_date = max(daily_loads.keys())

    k_atl = math.exp(-1 / 7)
    k_ctl = math.exp(-1 / 42)

    atl = ctl = 0.0
    weekly: dict = {}
    daily_history = []

    current = min(daily_loads.keys())
    while current <= target_date:
        load = daily_loads.get(current, 0.0)
        atl = atl * k_atl + load * (1 - k_atl)
        ctl = ctl * k_ctl + load * (1 - k_ctl)

        # Accumulate into ISO week (week ending Sunday)
        days_until_sunday = (6 - current.weekday()) % 7
        week_end = current + timedelta(days=days_until_sunday)
        weekly[week_end] = weekly.get(week_end, 0.0) + load

        if load > 0:
            daily_history.append({
                "date": str(current),
                "trimp": round(load, 1),
                "atl": round(atl, 1),
                "ctl": round(ctl, 1),
                "tsb": round(ctl - atl, 1),
            })

        current += timedelta(days=1)

    sorted_weeks = sorted(weekly.items())[-10:]

    return {
        "atl": round(atl, 1),
        "ctl": round(ctl, 1),
        "tsb": round(ctl - atl, 1),
        "weekly_loads": [(str(w), round(v, 1)) for w, v in sorted_weeks],
        "daily_history": daily_history[-14:],  # last 2 weeks for context
    }


# ── Aerobic Decoupling ─────────────────────────────────────────────────────────

def aerobic_decoupling(hr_series: list, vel_series: list) -> Optional[float]:
    """
    Pa:Hr decoupling — degradation of the pace:HR relationship over a run.

    Splits the run in half and compares speed-per-bpm in each half.
    Positive % = HR rising faster than pace (cardiac drift / fatigue).

    Interpretation:
      < 5%   Excellent aerobic pacing, well-conditioned
      5-10%  Some fatigue or heat stress
      > 10%  Significant drift — dehydration, poor pacing, or underfit

    Returns decoupling % or None if data insufficient.
    """
    pairs = [(h, v) for h, v in zip(hr_series, vel_series)
             if h is not None and v is not None and v > 0.5]
    if len(pairs) < 30:
        return None

    n = len(pairs)
    half = n // 2
    first, second = pairs[:half], pairs[half:]

    hr1 = statistics.mean(h for h, _ in first)
    v1  = statistics.mean(v for _, v in first)
    hr2 = statistics.mean(h for h, _ in second)
    v2  = statistics.mean(v for _, v in second)

    if hr1 <= 0 or hr2 <= 0:
        return None

    ratio1 = v1 / hr1  # speed-per-bpm first half
    ratio2 = v2 / hr2  # speed-per-bpm second half

    if ratio1 <= 0:
        return None

    return round((ratio1 - ratio2) / ratio1 * 100, 1)


# ── Grade Adjusted Pace ────────────────────────────────────────────────────────

def grade_adjusted_pace(
    vel_series: list,
    alt_series: list,
    dist_series: list,
) -> Optional[float]:
    """
    Grade Adjusted Pace — equivalent flat-ground velocity (m/s).

    Uses Minetti et al. energy cost curve (validated biomechanics research):
      cost_factor ≈ 1 + 0.0446·g + 0.0011·g²   where g = grade in %

    This means:
      +10% grade → cost_factor ≈ 1.56 (56% harder than flat)
      -5% grade  → cost_factor ≈ 0.80 (20% easier than flat)

    GAP = actual_velocity × cost_factor
    If GAP > actual pace → you ran uphill (would be faster on flat)
    If GAP < actual pace → you ran downhill (slower equivalent effort)

    Returns mean GAP velocity (m/s) or None.
    """
    if not vel_series or not alt_series or not dist_series:
        return None

    n = min(len(vel_series), len(alt_series), len(dist_series))
    smooth_alt = _smooth([alt_series[i] for i in range(n)], window=15)

    gap_vels = []
    for i in range(1, n):
        v = vel_series[i]
        if not v or v <= 0.3:
            continue

        d0 = dist_series[i - 1]
        d1 = dist_series[i]
        if d0 is None or d1 is None:
            gap_vels.append(v)
            continue

        d_delta = d1 - d0
        if d_delta <= 0:
            gap_vels.append(v)
            continue

        a0, a1 = smooth_alt[i - 1], smooth_alt[i]
        a_delta = (a1 - a0) if (a0 is not None and a1 is not None) else 0
        grade = (a_delta / d_delta) * 100  # grade in %
        grade = max(-30.0, min(30.0, grade))  # clamp extremes

        # Minetti energy cost factor
        cost = 1.0 + 0.0446 * grade + 0.0011 * grade ** 2
        cost = max(0.4, cost)  # floor at 40% of flat cost

        gap_vels.append(v * cost)

    return statistics.mean(gap_vels) if gap_vels else None


def _smooth(series: list, window: int = 15) -> list:
    """Simple centred moving average for GPS noise reduction."""
    half = window // 2
    result = []
    for i in range(len(series)):
        vals = [
            series[j]
            for j in range(max(0, i - half), min(len(series), i + half + 1))
            if series[j] is not None
        ]
        result.append(statistics.mean(vals) if vals else series[i])
    return result


# ── Best Efforts ───────────────────────────────────────────────────────────────

EFFORT_DISTANCES = {
    "1km":  1_000,
    "5km":  5_000,
    "10km": 10_000,
    "half": 21_097,
    "full": 42_195,
}


def find_best_efforts(
    dist_series: list,
    time_series: list,
    targets_m: Optional[list] = None,
) -> dict:
    """
    Find fastest time for each target distance via sliding window.

    Uses binary search (O(n log n)) so it's fast even on long runs.

    Returns {label: {time_s, time_fmt, pace_per_km}} for achieved distances.
    """
    if targets_m is None:
        targets_m = list(EFFORT_DISTANCES.values())

    pairs = [
        (d, t)
        for d, t in zip(dist_series, time_series)
        if d is not None and t is not None
    ]
    if not pairs:
        return {}

    dists = [p[0] for p in pairs]
    times = [p[1] for p in pairs]
    total_dist = dists[-1]

    label_map = {v: k for k, v in EFFORT_DISTANCES.items()}
    results = {}

    for target_m in targets_m:
        if total_dist < target_m * 0.9:
            continue

        best_time = None
        for i in range(len(dists)):
            target_d = dists[i] + target_m
            j = bisect.bisect_left(dists, target_d, i + 1)
            if j < len(dists):
                elapsed = times[j] - times[i]
                if elapsed > 0 and (best_time is None or elapsed < best_time):
                    best_time = elapsed

        if best_time and best_time > 0:
            pace_s_km = best_time / (target_m / 1000)
            label = label_map.get(target_m, f"{target_m}m")
            results[label] = {
                "time_s": best_time,
                "time_fmt": _fmt_time(best_time),
                "pace": f"{int(pace_s_km // 60)}:{int(pace_s_km % 60):02d}",
            }

    return results


def _fmt_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── Cadence Analysis ───────────────────────────────────────────────────────────

def cadence_analysis(cadence_series: list) -> Optional[dict]:
    """
    Analyse running cadence (steps per minute).

    Optimal range: 170-185 spm. Higher cadence reduces ground contact time
    and loading rate, lowering injury risk.

    Returns dict with avg, std, consistency_cv_pct, rating — or None.
    """
    valid = [c for c in cadence_series if c is not None and c > 50]
    if not valid:
        return None

    avg = statistics.mean(valid)
    std = statistics.stdev(valid) if len(valid) > 1 else 0.0
    cv = (std / avg * 100) if avg > 0 else 0.0

    if avg >= 178:
        rating = "excellent — elite range"
    elif avg >= 170:
        rating = "good"
    elif avg >= 160:
        rating = "moderate — consider increasing by 3-5 spm"
    else:
        rating = "low — target ≥170 spm to reduce injury risk"

    return {
        "avg_spm": round(avg, 1),
        "std_spm": round(std, 1),
        "consistency_cv_pct": round(cv, 1),
        "rating": rating,
    }


# ── HR Zones (Karvonen / Heart Rate Reserve) ───────────────────────────────────

def karvonen_zones(hr_max: int, hr_rest: int = 55) -> dict:
    """
    Compute 5-zone Karvonen (HRR) boundaries in bpm.

    Karvonen zones are more individualised than %HRmax because they
    account for resting HR (fitness level). Returns {zone_name: (lo, hi)}.
    """
    hrr = hr_max - hr_rest
    return {
        "Z1 Recovery":  (hr_rest + hrr * 0.50, hr_rest + hrr * 0.60),
        "Z2 Aerobic":   (hr_rest + hrr * 0.60, hr_rest + hrr * 0.70),
        "Z3 Tempo":     (hr_rest + hrr * 0.70, hr_rest + hrr * 0.80),
        "Z4 Threshold": (hr_rest + hrr * 0.80, hr_rest + hrr * 0.90),
        "Z5 VO2max":    (hr_rest + hrr * 0.90, float(hr_max)),
    }


def karvonen_zone_distribution(
    hr_series: list,
    hr_max: int,
    hr_rest: int = 55,
) -> dict:
    """
    Time-in-zone % using Karvonen zones.
    Returns {zone_name: pct} for zones with > 0% time.
    """
    valid = [h for h in hr_series if h is not None and h > 0]
    if not valid:
        return {}

    zones = karvonen_zones(hr_max, hr_rest)
    counts = {z: 0 for z in zones}
    above = 0

    for hr in valid:
        placed = False
        for zone, (lo, hi) in zones.items():
            if lo <= hr < hi:
                counts[zone] += 1
                placed = True
                break
        if not placed and hr >= hr_max * 0.90:
            above += 1

    n = len(valid)
    result = {z: round(c / n * 100, 1) for z, c in counts.items() if c > 0}
    if above > 0:
        result["Z5+ Max"] = round(above / n * 100, 1)
    return result


# ── Efficiency Factor ──────────────────────────────────────────────────────────

def efficiency_factor(hr_series: list, vel_series: list) -> Optional[float]:
    """
    EF = avg speed / avg HR × 1000 (scaled for readability).

    Tracks running economy over time: same pace at lower HR = improving.
    Only meaningful for aerobic (Z1-Z2) runs — exclude intervals/races.

    Typical range: 1.6–2.4. Higher is better.
    """
    pairs = [
        (h, v)
        for h, v in zip(hr_series, vel_series)
        if h is not None and v is not None and v > 0.5
    ]
    if len(pairs) < 20:
        return None

    avg_hr = statistics.mean(h for h, _ in pairs)
    avg_vel = statistics.mean(v for _, v in pairs)

    if avg_hr <= 0:
        return None

    return round(avg_vel / avg_hr * 1000, 3)


# ── Pace Zones ────────────────────────────────────────────────────────────────

def pace_zone_distribution(
    vel_series: list,
    threshold_vel_ms: float,
) -> dict:
    """
    Time-in-pace-zone % relative to threshold velocity.

    Zones are defined as % of threshold pace (vVO2max / lactate threshold).
    Returns {zone_label: pct}.
    """
    valid = [v for v in vel_series if v is not None and v > 0.3]
    if not valid or not threshold_vel_ms:
        return {}

    t = threshold_vel_ms
    zone_defs = [
        ("Recovery (<65%)",     lambda v: v < t * 0.65),
        ("Easy (65-75%)",       lambda v: t * 0.65 <= v < t * 0.75),
        ("Aerobic (75-85%)",    lambda v: t * 0.75 <= v < t * 0.85),
        ("Tempo (85-95%)",      lambda v: t * 0.85 <= v < t * 0.95),
        ("Threshold (95-105%)", lambda v: t * 0.95 <= v < t * 1.05),
        ("VO2max (105-120%)",   lambda v: t * 1.05 <= v < t * 1.20),
        ("Anaerobic (>120%)",   lambda v: v >= t * 1.20),
    ]

    counts = {label: 0 for label, _ in zone_defs}
    for v in valid:
        for label, fn in zone_defs:
            if fn(v):
                counts[label] += 1
                break

    n = len(valid)
    return {z: round(c / n * 100, 1) for z, c in counts.items() if c > 0}


# ── Interpretive helpers ───────────────────────────────────────────────────────

def interpret_tsb(tsb: float) -> str:
    if tsb > 20:
        return "Very fresh — peaked or detrained, ideal for racing"
    if tsb > 10:
        return "Fresh — good form, ready for a quality session"
    if tsb > 0:
        return "Slightly fresh — balanced training and recovery"
    if tsb > -10:
        return "Slight fatigue — productive training zone"
    if tsb > -20:
        return "Fatigued — heavy training block, plan recovery soon"
    if tsb > -30:
        return "Very fatigued — reduce load, risk of illness/injury rising"
    return "Overtraining risk — rest immediately"


def interpret_ctl(ctl: float) -> str:
    if ctl > 90:
        return "Elite / very high fitness base"
    if ctl > 70:
        return "High fitness — well-trained"
    if ctl > 50:
        return "Moderate-high fitness"
    if ctl > 30:
        return "Moderate — building base"
    if ctl > 15:
        return "Low — early training or returning from break"
    return "Minimal base — just starting out"


def interpret_decoupling(dc: float) -> str:
    if dc < 0:
        return "negative drift (HR dropped relative to pace — good warm-up effect)"
    if dc < 5:
        return "excellent — aerobically efficient, well-paced"
    if dc < 10:
        return "moderate drift — some fatigue or heat stress"
    return "high drift — dehydration, poor pacing, or aerobic base needs work"
