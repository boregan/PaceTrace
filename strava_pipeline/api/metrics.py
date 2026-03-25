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

    # Strava/Garmin often stores single-leg cadence (one foot's strikes per min).
    # Real running cadence (both feet) is double this. Detect by threshold:
    # single-leg values are typically 80-100; full cadence is 150-200.
    single_leg = avg < 120
    if single_leg:
        avg *= 2
        std *= 2

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


# ── Race Prediction ────────────────────────────────────────────────────────────

# Riegel exponents: longer races are disproportionately harder
_RIEGEL_EXPONENTS = {
    1_000:  1.04,
    5_000:  1.06,
    10_000: 1.06,
    21_097: 1.07,
    42_195: 1.08,
}

def riegel_predict(known_dist_m: float, known_time_s: float, target_dist_m: float) -> float:
    """
    Riegel endurance formula: T2 = T1 × (D2/D1)^e

    Exponent varies by target distance (longer = more fatigue penalty).
    Returns predicted time in seconds.
    """
    exp = _RIEGEL_EXPONENTS.get(int(target_dist_m), 1.06)
    return known_time_s * (target_dist_m / known_dist_m) ** exp


def predict_race_times(best_efforts: dict) -> dict:
    """
    Predict finish times at all standard distances from the best available efforts.

    Uses the closest known effort as the seed — predictions based on a nearby
    distance are more accurate than projecting from very different distances.

    Returns {label: {predicted_time_s, predicted_pace, source_label, source_dist_m,
                     confidence}} for all distances not already in best_efforts.
    """
    standard = [
        ("1km",  1_000),
        ("5km",  5_000),
        ("10km", 10_000),
        ("half", 21_097),
        ("full", 42_195),
    ]
    label_to_dist = {k: v for k, v in standard}
    dist_to_label = {v: k for k, v in standard}

    # Build numeric lookup of known efforts: dist_m → time_s
    known: list[tuple[float, float, str]] = []
    for label, data in best_efforts.items():
        d = label_to_dist.get(label)
        if d:
            known.append((d, data["time_s"], label))

    if not known:
        return {}

    results = {}
    for label, target_m in standard:
        # Find best seed: prefer efforts closest in distance to target
        seeds = sorted(known, key=lambda x: abs(math.log(x[0] / target_m)))
        seed_dist, seed_time, seed_label = seeds[0]

        predicted_s = riegel_predict(seed_dist, seed_time, target_m)
        pace_s_km   = predicted_s / (target_m / 1000)

        # Confidence degrades the further we project
        ratio      = max(target_m, seed_dist) / min(target_m, seed_dist)
        if ratio < 1.5:
            confidence = "high"
        elif ratio < 4:
            confidence = "moderate"
        else:
            confidence = "estimate only"

        results[label] = {
            "predicted_time_s": round(predicted_s),
            "predicted_time":   _fmt_time(predicted_s),
            "predicted_pace":   f"{int(pace_s_km // 60)}:{int(pace_s_km % 60):02d}",
            "source":           seed_label,
            "confidence":       confidence,
            # Include known PR if we have it
            "pr_time":          best_efforts[label]["time_fmt"] if label in best_efforts else None,
            "pr_pace":          best_efforts[label]["pace"] if label in best_efforts else None,
        }

    return results


# ── Training Balance Analysis ──────────────────────────────────────────────────

def training_balance_analysis(
    runs: list[dict],
    zone_data: list[dict],
    weeks: int = 8,
) -> dict:
    """
    Analyse training balance over a period against evidence-based recommendations.

    Checks:
      - 80/20 rule: 80% easy (Z1-Z2), 20% hard (Z3-Z5)
      - Long run ratio: longest run ≥ 25% of weekly km
      - Frequency: runs per week
      - Weekly km consistency: avoid >25% spikes
      - Easy run purity: are easy days truly easy?

    Args:
        runs:      list of activity dicts (from DB) in chronological order
        zone_data: list of {strava_id, zones: {Z1..Z5}} dicts
        weeks:     number of weeks analysed

    Returns structured dict with findings and recommendations.
    """
    if not runs:
        return {}

    zone_map = {z["strava_id"]: z.get("zones", {}) for z in zone_data}

    total_km  = sum((r.get("distance_m") or 0) for r in runs) / 1000
    total_s   = sum((r.get("moving_time_s") or 0) for r in runs)
    runs_pw   = len(runs) / weeks

    # Aggregate zone time weighted by run duration
    zone_seconds = {f"Z{i}": 0.0 for i in range(1, 6)}
    total_zone_s = 0.0
    for run in runs:
        dur = run.get("moving_time_s") or 0
        sid = run["strava_id"]
        zones = zone_map.get(sid, {})
        for z in ["Z1", "Z2", "Z3", "Z4", "Z5"]:
            pct = zones.get(z, 0) / 100
            zone_seconds[z] += dur * pct
            total_zone_s    += dur * pct

    zone_pct = {}
    if total_zone_s > 0:
        zone_pct = {z: round(s / total_zone_s * 100, 1)
                    for z, s in zone_seconds.items()}

    easy_pct  = zone_pct.get("Z1", 0) + zone_pct.get("Z2", 0)
    hard_pct  = zone_pct.get("Z3", 0) + zone_pct.get("Z4", 0) + zone_pct.get("Z5", 0)

    # Weekly km breakdown
    weekly_km: dict = {}
    for run in runs:
        sd = (run.get("start_date") or "")[:10]
        if not sd:
            continue
        try:
            d = date.fromisoformat(sd)
            days_until_sunday = (6 - d.weekday()) % 7
            week_end = str(d + timedelta(days=days_until_sunday))
            weekly_km[week_end] = weekly_km.get(week_end, 0.0) + (run.get("distance_m") or 0) / 1000
        except Exception:
            pass

    weekly_vals = list(weekly_km.values())
    avg_weekly  = statistics.mean(weekly_vals) if weekly_vals else 0
    max_weekly  = max(weekly_vals) if weekly_vals else 0
    km_cv       = (statistics.stdev(weekly_vals) / avg_weekly * 100
                   if len(weekly_vals) > 1 and avg_weekly > 0 else 0)

    # Long run check: does any week have a run ≥ 25% of that week's total?
    long_run_ratios = []
    for run in runs:
        sd = (run.get("start_date") or "")[:10]
        try:
            d   = date.fromisoformat(sd)
            wk  = str(d + timedelta(days=(6 - d.weekday()) % 7))
            wkm = weekly_km.get(wk, 0)
            if wkm > 0:
                long_run_ratios.append((run.get("distance_m") or 0) / 1000 / wkm)
        except Exception:
            pass
    has_long_runs = any(r >= 0.25 for r in long_run_ratios)

    # Build recommendations
    recs = []
    if easy_pct < 70 and total_zone_s > 0:
        recs.append(
            f"Only {easy_pct:.0f}% of training time is in Z1-Z2. "
            f"Evidence suggests 75-80% easy running optimises aerobic development "
            f"while reducing injury risk. Consider replacing some moderate runs with easy efforts."
        )
    elif easy_pct >= 80:
        recs.append(f"Great base — {easy_pct:.0f}% easy training. Well within the 80/20 guideline.")

    if hard_pct > 25 and total_zone_s > 0:
        recs.append(
            f"{hard_pct:.0f}% of time in Z3-Z5 is on the high side. "
            f"Too much intensity without easy recovery is a common overuse injury driver."
        )

    if runs_pw < 3:
        recs.append(
            f"Averaging {runs_pw:.1f} runs/week. Most training research shows "
            f"4+ sessions per week yields meaningfully better aerobic adaptation."
        )
    elif runs_pw >= 5:
        recs.append(f"Good consistency — {runs_pw:.1f} runs/week average.")

    if not has_long_runs:
        recs.append(
            "No week has a run ≥ 25% of that week's total km. "
            "A weekly long run (25-35% of weekly volume) is the single biggest driver "
            "of endurance development."
        )

    if km_cv > 30:
        recs.append(
            f"Weekly km varies a lot (CV {km_cv:.0f}%). "
            f"More consistent week-to-week volume reduces injury risk and builds "
            f"adaptation more reliably."
        )

    return {
        "weeks_analysed":  weeks,
        "total_runs":      len(runs),
        "total_km":        round(total_km, 1),
        "runs_per_week":   round(runs_pw, 1),
        "avg_weekly_km":   round(avg_weekly, 1),
        "max_weekly_km":   round(max_weekly, 1),
        "zone_pct":        zone_pct,
        "easy_pct":        round(easy_pct, 1),
        "hard_pct":        round(hard_pct, 1),
        "has_long_runs":   has_long_runs,
        "weekly_km":       weekly_km,
        "recommendations": recs,
    }


# ── Route / Segment Trend Tracking ────────────────────────────────────────────

def detect_recurring_routes(
    activities: list[dict],
    dist_threshold_pct: float = 0.08,
    loc_threshold_deg:  float = 0.004,   # ~400m
    min_runs:           int   = 3,
) -> list[dict]:
    """
    Cluster runs into recurring routes and track performance over time.

    Primary key: distance (within ±8%).
    Secondary refinement: start location (within ~400m) if lat/lng available.

    Returns list of route dicts sorted by frequency, each with:
      name, run_count, distance_km, pace_trend, best_pace, recent_pace,
      improvement_s_per_km, activity_ids
    """
    if not activities:
        return []

    runs = [
        a for a in activities
        if a.get("sport_type") in ("Run", "VirtualRun", "TrailRun")
        and (a.get("distance_m") or 0) > 500
        and a.get("avg_speed_ms")
    ]
    runs.sort(key=lambda a: a.get("start_date") or "")

    # Cluster by distance proximity
    used   = set()
    routes = []

    for i, anchor in enumerate(runs):
        if i in used:
            continue
        anchor_d = anchor["distance_m"]
        cluster  = [anchor]
        used.add(i)

        for j, cand in enumerate(runs):
            if j in used:
                continue
            if abs(cand["distance_m"] - anchor_d) / anchor_d <= dist_threshold_pct:
                # Optionally refine by start location
                if _same_location(anchor, cand, loc_threshold_deg):
                    cluster.append(cand)
                    used.add(j)

        if len(cluster) < min_runs:
            continue

        cluster.sort(key=lambda a: a.get("start_date") or "")
        paces   = [1000 / a["avg_speed_ms"] for a in cluster]   # sec/km
        dist_km = round(statistics.mean(a["distance_m"] for a in cluster) / 1000, 1)

        # Trend: compare first third vs last third
        n = len(paces)
        third = max(1, n // 3)
        early_pace = statistics.mean(paces[:third])
        late_pace  = statistics.mean(paces[n - third:])
        delta      = round(early_pace - late_pace, 1)   # positive = got faster

        # Label
        lat = anchor.get("start_lat")
        lng = anchor.get("start_lng")
        if lat and lng:
            name = f"~{dist_km}km route ({lat:.3f},{lng:.3f})"
        else:
            name = f"~{dist_km}km route"

        routes.append({
            "name":                name,
            "distance_km":         dist_km,
            "run_count":           len(cluster),
            "first_run":           (cluster[0].get("start_date") or "")[:10],
            "last_run":            (cluster[-1].get("start_date") or "")[:10],
            "best_pace":           _fmt_pace(min(paces)),
            "recent_pace":         _fmt_pace(paces[-1]),
            "avg_pace":            _fmt_pace(statistics.mean(paces)),
            "improvement_s_km":    delta,
            "trend":               _trend_label(delta),
            "activity_ids":        [a["strava_id"] for a in cluster],
        })

    routes.sort(key=lambda r: r["run_count"], reverse=True)
    return routes


def _same_location(a: dict, b: dict, threshold: float) -> bool:
    """True if both activities have location and are within threshold degrees."""
    a_lat, a_lng = a.get("start_lat"), a.get("start_lng")
    b_lat, b_lng = b.get("start_lat"), b.get("start_lng")
    if not all([a_lat, a_lng, b_lat, b_lng]):
        return True   # no location data — don't filter out, keep in cluster
    return abs(a_lat - b_lat) <= threshold and abs(a_lng - b_lng) <= threshold


def _fmt_pace(sec_per_km: float) -> str:
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}"


def _trend_label(delta_s: float) -> str:
    """delta_s = seconds/km improvement (positive = faster)."""
    if delta_s > 15:   return "strong improvement"
    if delta_s > 5:    return "improving"
    if delta_s > -5:   return "consistent"
    if delta_s > -15:  return "slightly slower"
    return "slower over time"


# ── Injury Risk Assessment ─────────────────────────────────────────────────────

def injury_risk_assessment(
    atl:          float,
    ctl:          float,
    daily_loads:  dict,
    activities:   list[dict],
) -> dict:
    """
    Multi-factor injury risk assessment using established sports science models.

    Metrics computed:
      ACWR (Acute:Chronic Workload Ratio) — primary predictor of injury risk
        Formula: ATL / CTL
        Safe zone: 0.8 – 1.3
        Elevated:  1.3 – 1.5
        Danger:    > 1.5

      Weekly km spike — current week vs 4-week rolling average
        > 30% increase: elevated risk
        > 50% increase: high risk

      Training monotony — repeating same effort daily reduces adaptation
        Monotony = weekly_avg / weekly_stdev (< 2.0 is healthy)

      Consecutive hard days — Z4-Z5 back-to-back without recovery

    Returns dict with all metrics, risk_level, flags, and recommendations.
    """
    flags = []
    recs  = []

    # ── ACWR ──────────────────────────────────────────────────────────────────
    acwr_val = round(atl / ctl, 2) if ctl > 0 else 0.0

    if acwr_val > 1.7:
        acwr_label = "DANGER ZONE"
        flags.append(f"ACWR {acwr_val:.2f} — very high injury risk")
        recs.append("Take 2-3 complete rest days immediately. Your acute load is dangerously "
                    "high relative to your fitness base.")
    elif acwr_val > 1.5:
        acwr_label = "High risk"
        flags.append(f"ACWR {acwr_val:.2f} — elevated injury risk")
        recs.append("Reduce intensity and volume for 5-7 days. Only easy Z1-Z2 running.")
    elif acwr_val > 1.3:
        acwr_label = "Slightly elevated"
        flags.append(f"ACWR {acwr_val:.2f} — slightly above ideal range")
        recs.append("One easy/rest day before your next hard session.")
    elif acwr_val >= 0.8:
        acwr_label = "Optimal (sweet spot)"
    elif acwr_val > 0:
        acwr_label = "Low (undertraining)"
        recs.append("Low training load relative to your base — safe to gradually increase volume.")
    else:
        acwr_label = "No data"

    # ── Weekly km spike ───────────────────────────────────────────────────────
    weekly_km: dict = {}
    for run in activities:
        sd = (run.get("start_date") or "")[:10]
        try:
            d = date.fromisoformat(sd)
            wk = str(d + timedelta(days=(6 - d.weekday()) % 7))
            weekly_km[wk] = weekly_km.get(wk, 0.0) + (run.get("distance_m") or 0) / 1000
        except Exception:
            pass

    sorted_weeks = sorted(weekly_km.items())
    spike_pct    = 0.0
    if len(sorted_weeks) >= 5:
        current_wk = sorted_weeks[-1][1]
        prior_4    = [v for _, v in sorted_weeks[-5:-1]]
        avg_prior  = statistics.mean(prior_4) if prior_4 else 0
        if avg_prior > 0:
            spike_pct = round((current_wk - avg_prior) / avg_prior * 100, 1)
            if spike_pct > 50:
                flags.append(f"Weekly km spike: +{spike_pct:.0f}% above 4-week average")
                recs.append(f"Current week is {spike_pct:.0f}% above your recent average — "
                             f"the 10% rule suggests max 10% increase per week.")
            elif spike_pct > 30:
                flags.append(f"Weekly km up {spike_pct:.0f}% — monitor carefully")

    # ── Training monotony ─────────────────────────────────────────────────────
    recent_loads = [v for _, v in sorted(daily_loads.items())[-14:] if v > 0]
    monotony     = 0.0
    if len(recent_loads) >= 4:
        avg_load = statistics.mean(recent_loads)
        std_load = statistics.stdev(recent_loads) if len(recent_loads) > 1 else 1
        monotony = round(avg_load / std_load, 2) if std_load > 0 else 0
        if monotony > 2.0:
            flags.append(f"High training monotony ({monotony:.1f}) — vary your effort levels")
            recs.append("Mix up your training intensity more. Alternating hard/easy days "
                        "produces better adaptation than similar daily efforts.")

    # ── Consecutive hard days ─────────────────────────────────────────────────
    recent_runs = sorted(
        [a for a in activities if a.get("avg_heartrate")],
        key=lambda a: a.get("start_date") or ""
    )[-10:]
    consecutive_hard = 0
    max_consecutive  = 0
    for run in recent_runs:
        hr  = run.get("avg_heartrate") or 0
        pct = hr / 185  # approximate
        if pct > 0.80:
            consecutive_hard += 1
            max_consecutive = max(max_consecutive, consecutive_hard)
        else:
            consecutive_hard = 0

    if max_consecutive >= 3:
        flags.append(f"{max_consecutive} consecutive hard days in recent runs")
        recs.append("At least one easy day between hard sessions is essential for "
                    "recovery and adaptation.")

    # ── Overall risk level ────────────────────────────────────────────────────
    if acwr_val > 1.5 or spike_pct > 50:
        risk_level = "HIGH"
    elif acwr_val > 1.3 or spike_pct > 30 or max_consecutive >= 3:
        risk_level = "MODERATE"
    elif flags:
        risk_level = "LOW-MODERATE"
    else:
        risk_level = "LOW"

    if not recs:
        recs.append("Training load looks well managed. Keep building consistently.")

    return {
        "risk_level":         risk_level,
        "acwr":               acwr_val,
        "acwr_label":         acwr_label,
        "weekly_km_spike_pct": spike_pct,
        "monotony":           monotony,
        "max_consecutive_hard": max_consecutive,
        "flags":              flags,
        "recommendations":    recs,
        "weekly_km":          {w: round(v, 1) for w, v in sorted_weeks[-8:]},
    }
