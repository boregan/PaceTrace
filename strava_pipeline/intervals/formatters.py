"""
Display formatters for intervals.icu data → human-readable MCP output.
"""

from datetime import timedelta


def fmt_pace(speed_ms: float | None) -> str:
    """Convert m/s → min:sec /km string."""
    if not speed_ms or speed_ms <= 0:
        return "—"
    secs_per_km = 1000 / speed_ms
    m, s = divmod(int(secs_per_km), 60)
    return f"{m}:{s:02d} /km"


def fmt_pace_value(pace_val: float | None) -> str:
    """Convert pace value (s/m or s/km depending on API) → min:sec /km."""
    if not pace_val or pace_val <= 0:
        return "—"
    # intervals.icu pace values are typically in s/km
    m, s = divmod(int(pace_val), 60)
    return f"{m}:{s:02d} /km"


def fmt_duration(seconds: int | float | None) -> str:
    """Seconds → human-friendly string like '45:23' or '1:12:05'."""
    if not seconds:
        return "—"
    seconds = int(seconds)
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}:{m:02d}:{s:02d}"


def fmt_distance(metres: float | None) -> str:
    """Metres → km string with appropriate precision."""
    if not metres:
        return "—"
    km = metres / 1000
    if km >= 10:
        return f"{km:.1f} km"
    return f"{km:.2f} km"


def fmt_elevation(metres: float | None) -> str:
    if not metres:
        return "—"
    return f"{int(metres)}m"


def fmt_hr(bpm: int | float | None) -> str:
    if not bpm:
        return "—"
    return f"{int(bpm)} bpm"


def fmt_cadence(spm: float | None) -> str:
    """Format cadence, auto-detecting single-leg and doubling."""
    if not spm:
        return "—"
    spm = float(spm)
    if spm < 120:
        spm *= 2  # single-leg → bilateral
    return f"{int(spm)} spm"


def fmt_percent(value: float | None, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}%"


def fmt_load(value: int | float | None) -> str:
    if value is None:
        return "—"
    return str(int(value))


def fmt_efficiency(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def fmt_decoupling(value: float | None) -> str:
    """Format aerobic decoupling with interpretation."""
    if value is None:
        return "—"
    pct = value * 100 if abs(value) < 1 else value  # handle both ratio and pct
    label = "excellent" if abs(pct) < 5 else "good" if abs(pct) < 8 else "significant drift"
    return f"{pct:+.1f}% ({label})"


def fmt_tsb(ctl: float | None, atl: float | None) -> str:
    """Interpret TSB (form = CTL - ATL)."""
    if ctl is None or atl is None:
        return "—"
    tsb = ctl - atl
    if tsb > 15:
        status = "very fresh / detrained risk"
    elif tsb > 5:
        status = "fresh — good for race/hard session"
    elif tsb > -10:
        status = "neutral — productive training"
    elif tsb > -20:
        status = "fatigued — absorbing load"
    else:
        status = "very fatigued — rest soon"
    return f"TSB {tsb:+.0f} ({status})"


def fmt_ramp_rate(rate: float | None) -> str:
    """Interpret fitness ramp rate."""
    if rate is None:
        return "—"
    if rate > 8:
        label = "aggressive — injury risk"
    elif rate > 5:
        label = "high — monitor closely"
    elif rate > 2:
        label = "good progression"
    elif rate > 0:
        label = "maintenance"
    elif rate > -2:
        label = "slight detraining"
    else:
        label = "significant detraining"
    return f"{rate:+.1f} CTL/week ({label})"


def interpret_hrv(hrv: float | None, hrv_avg_7d: float | None = None) -> str:
    """Interpret HRV value with optional 7-day baseline."""
    if hrv is None:
        return "—"
    text = f"{hrv:.0f} ms"
    if hrv_avg_7d and hrv_avg_7d > 0:
        pct_diff = ((hrv - hrv_avg_7d) / hrv_avg_7d) * 100
        if pct_diff < -15:
            text += f" (⚠️ {pct_diff:+.0f}% vs 7-day avg — consider easy day)"
        elif pct_diff < -5:
            text += f" ({pct_diff:+.0f}% vs 7-day avg — slightly low)"
        elif pct_diff > 10:
            text += f" ({pct_diff:+.0f}% vs 7-day avg — well recovered)"
        else:
            text += f" ({pct_diff:+.0f}% vs 7-day avg — normal)"
    return text


def interpret_sleep(sleep_secs: int | None, sleep_score: float | None = None) -> str:
    """Interpret sleep duration with optional score."""
    if not sleep_secs:
        return "—"
    hours = sleep_secs / 3600
    text = f"{hours:.1f}h"
    if sleep_score:
        text += f" (score: {sleep_score:.0f}/100)"
    if hours < 6:
        text += " — limited"
    elif hours < 7:
        text += " — could be more"
    elif hours >= 8:
        text += " — great"
    return text


def zone_label(zone_num: int, sport: str = "run") -> str:
    """Default zone name for display."""
    labels = {1: "Recovery", 2: "Endurance", 3: "Tempo", 4: "Threshold", 5: "VO2max", 6: "Anaerobic"}
    return labels.get(zone_num, f"Z{zone_num}")


def format_zone_times(zone_times: list | None, zone_names: list | None = None) -> str:
    """Format zone time distribution as a compact table."""
    if not zone_times:
        return "—"
    lines = []
    total = sum(zone_times)
    for i, secs in enumerate(zone_times):
        if secs <= 0:
            continue
        name = zone_names[i] if zone_names and i < len(zone_names) else zone_label(i + 1)
        pct = (secs / total * 100) if total else 0
        lines.append(f"  Z{i+1} {name}: {fmt_duration(secs)} ({pct:.0f}%)")
    return "\n".join(lines) if lines else "—"


def format_interval_summary(interval: dict) -> str:
    """One-line summary of an auto-detected interval."""
    dist = fmt_distance(interval.get("distance"))
    dur = fmt_duration(interval.get("moving_time") or interval.get("elapsed_time"))
    pace = fmt_pace(interval.get("average_speed"))
    gap = fmt_pace(interval.get("gap")) if interval.get("gap") else ""
    hr = fmt_hr(interval.get("average_heartrate"))
    cad = fmt_cadence(interval.get("average_cadence"))
    intensity = interval.get("intensity", "")
    label = interval.get("label", interval.get("type", ""))

    parts = [f"{label}: {dist} in {dur}"]
    parts.append(f"pace {pace}")
    if gap:
        parts.append(f"GAP {gap}")
    parts.append(hr)
    if intensity:
        parts.append(f"{intensity}% intensity")
    return " | ".join(parts)
