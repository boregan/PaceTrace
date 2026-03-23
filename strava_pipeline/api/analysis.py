from __future__ import annotations

"""
Interprets run stream data to produce plain-English analysis.

Detects:
- Activity type: easy / tempo / intervals / long run / 5-a-side / race
- Cardiac drift: HR rising while pace holds
- Pacing pattern: even / positive split / negative split / progression / intervals
"""

import statistics
from typing import Optional


# ── HR zone helpers ────────────────────────────────────────────────────────────

def hr_zone(hr: float, max_hr: int = 185) -> int:
    pct = hr / max_hr
    if pct < 0.60: return 1
    if pct < 0.70: return 2
    if pct < 0.80: return 3
    if pct < 0.90: return 4
    return 5


def zone_distribution(hr_series: list, max_hr: int = 185) -> dict[str, float]:
    """Return % of time spent in each HR zone."""
    valid = [h for h in hr_series if h]
    if not valid:
        return {}
    counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for h in valid:
        counts[hr_zone(h, max_hr)] += 1
    n = len(valid)
    return {f"Z{z}": round(c / n * 100, 1) for z, c in counts.items()}


# ── Activity type detection ────────────────────────────────────────────────────

def detect_activity_type(
    hr_series: list,
    vel_series: list,
    time_series: list,
    max_hr: int = 185,
) -> str:
    """
    Classify the session into a human-readable activity type.
    Returns one of: easy run / tempo / intervals / long run / 5-a-side / race
    """
    valid_hr = [h for h in hr_series if h]
    valid_vel = [v for v in vel_series if v and v > 0.5]  # ignore near-stops

    if not valid_hr or not valid_vel:
        return "run (no HR data)"

    duration_min = max(time_series) / 60 if time_series else 0
    avg_hr = statistics.mean(valid_hr)
    hr_stdev = statistics.stdev(valid_hr) if len(valid_hr) > 1 else 0
    vel_stdev = statistics.stdev(valid_vel) if len(valid_vel) > 1 else 0
    avg_vel = statistics.mean(valid_vel)

    zones = zone_distribution(valid_hr, max_hr)
    z1z2_pct = zones.get("Z1", 0) + zones.get("Z2", 0)
    z4z5_pct = zones.get("Z4", 0) + zones.get("Z5", 0)

    # Detect 5-a-side / stop-start sport: frequent near-zero velocity
    stops = sum(1 for v in vel_series if v is not None and v < 0.5)
    stop_pct = stops / len(vel_series) if vel_series else 0
    if stop_pct > 0.25 and hr_stdev > 15:
        return "5-a-side / stop-start sport"

    # Intervals: high variance in both HR and pace
    if hr_stdev > 18 and vel_stdev > 0.8:
        return "interval session"

    # Race: high HR sustained, negative or even split
    if z4z5_pct > 55 and duration_min < 60:
        return "race / time trial"

    # Tempo: sustained Z4, low pace variance
    if zones.get("Z4", 0) > 40 and vel_stdev < 0.5 and duration_min < 75:
        return "tempo run"

    # Long run: >75 min, mostly Z2
    if duration_min > 75 and z1z2_pct > 50:
        return "long run"

    # Easy run: mostly Z1-Z2
    if z1z2_pct > 60:
        return "easy run"

    # Default
    if z4z5_pct > 35:
        return "threshold / hard run"
    return "moderate run"


# ── Cardiac drift ─────────────────────────────────────────────────────────────

def detect_cardiac_drift(
    hr_series: list,
    vel_series: list,
    time_series: list,
) -> Optional[str]:
    """
    Detect cardiac drift: HR rising while pace stays roughly constant.
    Returns a description string, or None if not detected.
    """
    if not hr_series or not vel_series or len(hr_series) < 20:
        return None

    n = len(hr_series)
    third = n // 3

    # Segment into thirds
    def avg(series, start, end):
        vals = [x for x in series[start:end] if x is not None]
        return statistics.mean(vals) if vals else None

    hr_early = avg(hr_series, 0, third)
    hr_late = avg(hr_series, 2 * third, n)
    vel_early = avg(vel_series, 0, third)
    vel_late = avg(vel_series, 2 * third, n)

    if any(v is None for v in [hr_early, hr_late, vel_early, vel_late]):
        return None

    hr_rise = hr_late - hr_early
    pace_change_pct = abs(vel_late - vel_early) / vel_early if vel_early else 0

    # Drift: HR rose >8 bpm while pace changed <10%
    if hr_rise > 8 and pace_change_pct < 0.10:
        early_pace = _vel_to_pace(vel_early)
        late_pace = _vel_to_pace(vel_late)
        return (
            f"Cardiac drift detected: HR rose {hr_rise:.0f} bpm "
            f"({hr_early:.0f} → {hr_late:.0f}) while pace stayed consistent "
            f"({early_pace} → {late_pace} /km). "
            f"Likely fatigue or heat accumulation."
        )

    if hr_rise < -5 and pace_change_pct < 0.10:
        return (
            f"HR dropped {abs(hr_rise):.0f} bpm over the session at consistent pace — "
            f"possible warm-up effect or aerobic adaptation."
        )

    return None


# ── Pacing pattern ─────────────────────────────────────────────────────────────

def detect_pacing_pattern(
    vel_series: list,
    time_series: list,
) -> str:
    """Describe the pacing pattern of the run."""
    valid = [(t, v) for t, v in zip(time_series, vel_series) if v and v > 0.5]
    if len(valid) < 10:
        return "insufficient data"

    n = len(valid)
    half = n // 2
    first_half_vel = [v for _, v in valid[:half]]
    second_half_vel = [v for _, v in valid[half:]]

    avg_first = statistics.mean(first_half_vel)
    avg_second = statistics.mean(second_half_vel)

    # Check for interval pattern: alternating fast/slow
    vels = [v for _, v in valid]
    stdev = statistics.stdev(vels) if len(vels) > 1 else 0
    if stdev > 0.9:
        return "interval / fartlek pattern — significant pace variation throughout"

    diff_pct = (avg_second - avg_first) / avg_first if avg_first else 0

    if diff_pct > 0.05:
        return (
            f"negative split — second half {abs(diff_pct)*100:.0f}% faster "
            f"({_vel_to_pace(avg_first)} → {_vel_to_pace(avg_second)} /km)"
        )
    if diff_pct < -0.05:
        return (
            f"positive split — second half {abs(diff_pct)*100:.0f}% slower "
            f"({_vel_to_pace(avg_first)} → {_vel_to_pace(avg_second)} /km)"
        )

    # Check progression: consistently getting faster
    segment_size = n // 5
    if segment_size > 2:
        seg_avgs = [
            statistics.mean([v for _, v in valid[i*segment_size:(i+1)*segment_size]])
            for i in range(5)
        ]
        if all(seg_avgs[i] <= seg_avgs[i+1] for i in range(4)):
            return (
                f"progression run — pace improved steadily from "
                f"{_vel_to_pace(seg_avgs[0])} to {_vel_to_pace(seg_avgs[-1])} /km"
            )

    return f"even pace — consistent {_vel_to_pace(statistics.mean(vels))} /km throughout"


# ── Full interpretation ────────────────────────────────────────────────────────

def interpret_activity(
    activity: dict,
    stream: dict,
    max_hr: int = 185,
) -> dict:
    """
    Run all analysis and return a structured interpretation dict.
    """
    hr = stream.get("heartrate") or []
    vel = stream.get("velocity_ms") or []
    time_s = stream.get("time_s") or []
    alt = stream.get("altitude_m") or []

    activity_type = detect_activity_type(hr, vel, time_s, max_hr)
    drift = detect_cardiac_drift(hr, vel, time_s)
    pacing = detect_pacing_pattern(vel, time_s)
    zones = zone_distribution(hr, max_hr)

    # Elevation summary
    elev_notes = None
    valid_alt = [a for a in alt if a is not None]
    if valid_alt:
        elev_range = max(valid_alt) - min(valid_alt)
        if elev_range > 50:
            elev_notes = f"Hilly — {elev_range:.0f}m total altitude range"

    # Build plain-text interpretation
    lines = [f"Session type: {activity_type}", f"Pacing: {pacing}"]
    if drift:
        lines.append(drift)
    if elev_notes:
        lines.append(elev_notes)
    if zones:
        zone_str = "  ".join(f"{z}: {pct}%" for z, pct in sorted(zones.items()))
        lines.append(f"HR zones: {zone_str}")

    return {
        "activity_type": activity_type,
        "pacing_pattern": pacing,
        "cardiac_drift": drift,
        "zone_distribution": zones,
        "interpretation": "\n".join(lines),
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _vel_to_pace(vel_ms: float) -> str:
    if not vel_ms or vel_ms <= 0:
        return "—"
    sec_per_km = 1000 / vel_ms
    return f"{int(sec_per_km // 60)}:{int(sec_per_km % 60):02d}"
