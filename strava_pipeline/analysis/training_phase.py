"""
Training phase detection — auto-detect base/build/peak/taper/recovery phases.

Analyzes rolling trends in volume, intensity, CTL/ATL/TSB, and session types
to classify the current training phase and show phase history.

Phase definitions:
- BASE: High volume, low intensity (>80% easy), CTL rising slowly
- BUILD: Moderate-high volume, increasing intensity, intervals 2-3x/wk
- PEAK: Moderate volume, high intensity ratio, CTL plateau
- TAPER: Dropping volume (40-60% reduction), maintained intensity, TSB rising
- RECOVERY: Very low volume, all easy, TSB positive and rising
- MAINTENANCE: Stable volume and intensity, CTL flat

Based on Friel's periodization model and TrainingPeaks ATP methodology.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
import statistics


@dataclass
class TrainingWeek:
    """Summary of one training week."""
    start_date: date
    total_km: float
    run_count: int
    avg_pace_secs_km: float | None
    easy_pct: float         # % of time in easy zones
    hard_pct: float         # % of time in hard zones (tempo+threshold+VO2max)
    interval_sessions: int  # count of interval-type sessions
    long_run_km: float      # longest single run
    training_load: float    # total training load (TRIMP/TSS)
    ctl: float | None       # end-of-week CTL
    atl: float | None       # end-of-week ATL
    tsb: float | None       # end-of-week TSB


@dataclass
class PhaseDetection:
    """Detected training phase for a time window."""
    phase: str          # BASE, BUILD, PEAK, TAPER, RECOVERY, MAINTENANCE
    confidence: float   # 0-1
    start_date: date
    end_date: date
    signals: list[str]  # What signals led to this classification

    @property
    def label(self) -> str:
        icons = {
            "BASE": "\U0001f3d7\ufe0f", "BUILD": "\U0001f4c8", "PEAK": "\u26a1",
            "TAPER": "\U0001f53b", "RECOVERY": "\U0001f6cc", "MAINTENANCE": "\u27a1\ufe0f"
        }
        default = "\u2753"
        return f"{icons.get(self.phase, default)} {self.phase}"


@dataclass
class PhaseReport:
    """Full periodization analysis."""
    current_phase: PhaseDetection
    phase_history: list[PhaseDetection]
    weeks: list[TrainingWeek]
    volume_trend: str     # "rising", "falling", "stable"
    intensity_trend: str  # "rising", "falling", "stable"
    fitness_trend: str    # "rising", "falling", "stable"


def _trend(values: list[float], min_change_pct: float = 10.0) -> str:
    """Detect trend in a series of values."""
    if len(values) < 3:
        return "stable"

    # Compare first third to last third
    third = max(len(values) // 3, 1)
    first = statistics.mean(values[:third])
    last = statistics.mean(values[-third:])

    if first <= 0:
        return "rising" if last > 0 else "stable"

    change_pct = ((last - first) / first) * 100

    if change_pct > min_change_pct:
        return "rising"
    elif change_pct < -min_change_pct:
        return "falling"
    return "stable"


def _classify_phase(
    weeks: list[TrainingWeek],
    volume_trend: str,
    intensity_trend: str,
    fitness_trend: str,
) -> PhaseDetection:
    """
    Classify the current training phase from recent weeks.

    Uses a scoring system across multiple signals.
    """
    if len(weeks) < 2:
        return PhaseDetection(
            "MAINTENANCE", 0.3, weeks[0].start_date if weeks else date.today(),
            weeks[-1].start_date if weeks else date.today(),
            ["Insufficient data for phase detection"]
        )

    recent = weeks[-3:] if len(weeks) >= 3 else weeks

    avg_easy_pct = statistics.mean(w.easy_pct for w in recent)
    avg_hard_pct = statistics.mean(w.hard_pct for w in recent)
    avg_km = statistics.mean(w.total_km for w in recent)
    avg_intervals = statistics.mean(w.interval_sessions for w in recent)
    avg_load = statistics.mean(w.training_load for w in recent)

    # Volume relative to peak (max weekly km in data)
    peak_km = max(w.total_km for w in weeks) if weeks else 1
    vol_pct = (avg_km / peak_km * 100) if peak_km > 0 else 0

    # TSB trend
    tsb_vals = [w.tsb for w in recent if w.tsb is not None]
    tsb_trend = _trend(tsb_vals, 5.0) if tsb_vals else "stable"
    latest_tsb = tsb_vals[-1] if tsb_vals else 0

    # Score each phase
    scores: dict[str, tuple[float, list[str]]] = {}

    # RECOVERY
    recovery_score = 0.0
    recovery_signals = []
    if vol_pct < 40:
        recovery_score += 0.3
        recovery_signals.append(f"Low volume ({vol_pct:.0f}% of peak)")
    if avg_easy_pct > 90:
        recovery_score += 0.2
        recovery_signals.append(f"Almost all easy ({avg_easy_pct:.0f}%)")
    if latest_tsb > 10:
        recovery_score += 0.2
        recovery_signals.append(f"High freshness (TSB {latest_tsb:+.0f})")
    if avg_intervals < 0.5:
        recovery_score += 0.1
        recovery_signals.append("No interval sessions")
    scores["RECOVERY"] = (recovery_score, recovery_signals)

    # TAPER
    taper_score = 0.0
    taper_signals = []
    if volume_trend == "falling" and vol_pct < 70:
        taper_score += 0.3
        taper_signals.append(f"Volume dropping ({vol_pct:.0f}% of peak)")
    if tsb_trend == "rising":
        taper_score += 0.2
        taper_signals.append("TSB rising (freshening up)")
    if avg_hard_pct > 15:
        taper_score += 0.1
        taper_signals.append(f"Maintaining some intensity ({avg_hard_pct:.0f}% hard)")
    if fitness_trend != "falling":
        taper_score += 0.1
        taper_signals.append("Fitness maintained")
    scores["TAPER"] = (taper_score, taper_signals)

    # BASE
    base_score = 0.0
    base_signals = []
    if avg_easy_pct > 80:
        base_score += 0.25
        base_signals.append(f"Mostly easy running ({avg_easy_pct:.0f}%)")
    if volume_trend == "rising":
        base_score += 0.25
        base_signals.append("Volume trending up")
    if fitness_trend == "rising":
        base_score += 0.15
        base_signals.append("CTL rising")
    if avg_intervals < 1.5:
        base_score += 0.1
        base_signals.append(f"Low interval frequency ({avg_intervals:.1f}/wk)")
    scores["BASE"] = (base_score, base_signals)

    # BUILD
    build_score = 0.0
    build_signals = []
    if avg_intervals >= 2:
        build_score += 0.25
        build_signals.append(f"Regular intervals ({avg_intervals:.1f}/wk)")
    if intensity_trend == "rising":
        build_score += 0.2
        build_signals.append("Intensity rising")
    if vol_pct >= 70:
        build_score += 0.15
        build_signals.append(f"High volume ({vol_pct:.0f}% of peak)")
    if avg_hard_pct > 20:
        build_score += 0.1
        build_signals.append(f"Significant hard work ({avg_hard_pct:.0f}%)")
    if fitness_trend == "rising":
        build_score += 0.1
        build_signals.append("CTL rising")
    scores["BUILD"] = (build_score, build_signals)

    # PEAK
    peak_score = 0.0
    peak_signals = []
    if avg_hard_pct > 25:
        peak_score += 0.25
        peak_signals.append(f"High intensity ratio ({avg_hard_pct:.0f}% hard)")
    if fitness_trend == "stable" and vol_pct >= 60:
        peak_score += 0.2
        peak_signals.append("CTL stable at moderate-high volume")
    if avg_intervals >= 2:
        peak_score += 0.15
        peak_signals.append(f"Frequent intervals ({avg_intervals:.1f}/wk)")
    if volume_trend in ("stable", "falling") and vol_pct >= 60:
        peak_score += 0.1
        peak_signals.append("Volume maintained or slightly reducing")
    scores["PEAK"] = (peak_score, peak_signals)

    # MAINTENANCE
    maint_score = 0.0
    maint_signals = []
    if volume_trend == "stable":
        maint_score += 0.2
        maint_signals.append("Stable volume")
    if intensity_trend == "stable":
        maint_score += 0.2
        maint_signals.append("Stable intensity")
    if fitness_trend == "stable":
        maint_score += 0.2
        maint_signals.append("Stable fitness")
    scores["MAINTENANCE"] = (maint_score, maint_signals)

    # Pick highest scoring phase
    best_phase = max(scores, key=lambda k: scores[k][0])
    confidence, signals = scores[best_phase]

    # Normalize confidence
    total = sum(s[0] for s in scores.values())
    confidence = confidence / total if total > 0 else 0.3

    return PhaseDetection(
        phase=best_phase,
        confidence=round(min(confidence, 1.0), 2),
        start_date=recent[0].start_date,
        end_date=recent[-1].start_date + timedelta(days=6),
        signals=signals,
    )


def build_training_weeks(
    activities: list[dict],
    wellness_data: list[dict] | None = None,
) -> list[TrainingWeek]:
    """
    Build weekly summaries from activity data.

    Activities should have: start_date, distance (m), moving_time (s),
    icu_training_load, average_speed, icu_hr_zone_times, intervals (optional).
    """
    if not activities:
        return []

    # Group by ISO week
    weeks: dict[tuple[int, int], list[dict]] = {}
    for a in activities:
        dt_str = a.get("start_date_local") or a.get("start_date", "")
        if not dt_str:
            continue
        try:
            dt = date.fromisoformat(dt_str[:10])
        except (ValueError, TypeError):
            continue

        iso_year, iso_week, _ = dt.isocalendar()
        key = (iso_year, iso_week)
        weeks.setdefault(key, []).append(a)

    # Build wellness lookup for CTL/ATL/TSB
    wellness_by_date: dict[str, dict] = {}
    if wellness_data:
        for w in wellness_data:
            d = w.get("id") or w.get("date", "")
            if d:
                wellness_by_date[d[:10]] = w

    result = []
    for (year, week_num), acts in sorted(weeks.items()):
        # Week start (Monday)
        jan4 = date(year, 1, 4)
        start = jan4 + timedelta(weeks=week_num - jan4.isocalendar()[1])
        start -= timedelta(days=start.weekday())  # Monday

        total_km = sum((a.get("distance") or a.get("icu_distance") or 0) / 1000 for a in acts)
        total_load = sum(a.get("icu_training_load") or 0 for a in acts)

        # Pace
        total_time = sum(a.get("moving_time") or 0 for a in acts)
        total_dist = sum(a.get("distance") or a.get("icu_distance") or 0 for a in acts)
        avg_pace = (total_time / (total_dist / 1000)) if total_dist > 0 else None

        # Longest run
        long_km = max((a.get("distance") or a.get("icu_distance") or 0) / 1000 for a in acts) if acts else 0

        # Intensity from HR zone times
        easy_time = 0
        hard_time = 0
        for a in acts:
            zones = a.get("icu_hr_zone_times") or a.get("hr_zone_times")
            if zones and len(zones) >= 5:
                # zones: [z1, z2, z3, z4, z5] in seconds
                easy_time += zones[0] + zones[1]  # Z1 + Z2
                hard_time += zones[2] + zones[3] + zones[4]  # Z3 + Z4 + Z5
            else:
                # Fallback: estimate from pace/HR
                mt = a.get("moving_time") or 0
                intensity = a.get("icu_intensity") or 0
                if intensity > 85:
                    hard_time += mt
                else:
                    easy_time += mt

        total_zone_time = easy_time + hard_time
        easy_pct = (easy_time / total_zone_time * 100) if total_zone_time > 0 else 80
        hard_pct = (hard_time / total_zone_time * 100) if total_zone_time > 0 else 20

        # Interval sessions (check for interval-type activities)
        interval_count = 0
        for a in acts:
            # Check if activity has intervals with intensity > 90%
            intervals = a.get("icu_intervals") or a.get("intervals") or []
            if isinstance(intervals, list) and len(intervals) >= 3:
                interval_count += 1
            elif a.get("icu_intensity") and a["icu_intensity"] > 85:
                interval_count += 1

        # CTL/ATL/TSB from wellness or activity data
        end_date = start + timedelta(days=6)
        end_str = end_date.isoformat()
        w = wellness_by_date.get(end_str, {})
        ctl = w.get("ctl") or (acts[-1].get("icu_ctl") if acts else None)
        atl = w.get("atl") or (acts[-1].get("icu_atl") if acts else None)
        tsb = (ctl - atl) if (ctl is not None and atl is not None) else None

        result.append(TrainingWeek(
            start_date=start,
            total_km=round(total_km, 1),
            run_count=len(acts),
            avg_pace_secs_km=round(avg_pace, 1) if avg_pace else None,
            easy_pct=round(easy_pct, 1),
            hard_pct=round(hard_pct, 1),
            interval_sessions=interval_count,
            long_run_km=round(long_km, 1),
            training_load=round(total_load, 1),
            ctl=round(ctl, 1) if ctl is not None else None,
            atl=round(atl, 1) if atl is not None else None,
            tsb=round(tsb, 1) if tsb is not None else None,
        ))

    return result


def detect_phases(
    weeks: list[TrainingWeek],
    window_size: int = 3,
) -> PhaseReport:
    """
    Detect training phases from weekly summaries.

    Args:
        weeks: List of TrainingWeek summaries (chronological)
        window_size: Number of weeks to consider for current phase
    """
    if not weeks:
        return PhaseReport(
            current_phase=PhaseDetection("MAINTENANCE", 0.0, date.today(), date.today(), ["No data"]),
            phase_history=[], weeks=[], volume_trend="stable",
            intensity_trend="stable", fitness_trend="stable",
        )

    # Overall trends
    volumes = [w.total_km for w in weeks]
    intensities = [w.hard_pct for w in weeks]
    ctl_vals = [w.ctl for w in weeks if w.ctl is not None]

    volume_trend = _trend(volumes)
    intensity_trend = _trend(intensities)
    fitness_trend = _trend(ctl_vals) if ctl_vals else "stable"

    # Current phase (last window_size weeks)
    current = _classify_phase(weeks, volume_trend, intensity_trend, fitness_trend)

    # Phase history (sliding window)
    history = []
    step = max(window_size, 2)
    for i in range(0, len(weeks) - window_size + 1, step):
        window = weeks[i:i + window_size]
        w_vols = [w.total_km for w in window]
        w_ints = [w.hard_pct for w in window]
        w_ctls = [w.ctl for w in window if w.ctl is not None]

        phase = _classify_phase(
            window,
            _trend(w_vols),
            _trend(w_ints),
            _trend(w_ctls) if w_ctls else "stable",
        )
        history.append(phase)

    # Deduplicate consecutive same phases
    deduped = []
    for phase in history:
        if deduped and deduped[-1].phase == phase.phase:
            deduped[-1] = PhaseDetection(
                phase=phase.phase,
                confidence=max(deduped[-1].confidence, phase.confidence),
                start_date=deduped[-1].start_date,
                end_date=phase.end_date,
                signals=deduped[-1].signals,
            )
        else:
            deduped.append(phase)

    return PhaseReport(
        current_phase=current,
        phase_history=deduped,
        weeks=weeks,
        volume_trend=volume_trend,
        intensity_trend=intensity_trend,
        fitness_trend=fitness_trend,
    )


def format_phase_report(report: PhaseReport) -> str:
    """Format phase report for display."""
    lines = [
        "# Training Phase Analysis",
        "",
        f"**Current Phase: {report.current_phase.label}** (confidence: {report.current_phase.confidence:.0%})",
        "",
    ]

    if report.current_phase.signals:
        lines.append("**Signals:**")
        for s in report.current_phase.signals:
            lines.append(f"- {s}")
        lines.append("")

    lines.extend([
        "## Trends",
        f"- Volume: {report.volume_trend}",
        f"- Intensity: {report.intensity_trend}",
        f"- Fitness (CTL): {report.fitness_trend}",
        "",
    ])

    # Weekly summary table
    if report.weeks:
        lines.extend([
            "## Weekly Summary",
            "| Week | Distance | Runs | Easy% | Hard% | Intervals | Load | CTL | TSB |",
            "|------|----------|------|-------|-------|-----------|------|-----|-----|",
        ])
        for w in report.weeks[-8:]:  # Last 8 weeks
            ctl_str = f"{w.ctl:.0f}" if w.ctl is not None else "\u2014"
            tsb_str = f"{w.tsb:+.0f}" if w.tsb is not None else "\u2014"
            lines.append(
                f"| {w.start_date} | {w.total_km:.0f}km | {w.run_count} | "
                f"{w.easy_pct:.0f}% | {w.hard_pct:.0f}% | {w.interval_sessions} | "
                f"{w.training_load:.0f} | {ctl_str} | {tsb_str} |"
            )
        lines.append("")

    # Phase history
    if report.phase_history:
        lines.append("## Phase History")
        for p in report.phase_history:
            lines.append(
                f"- {p.start_date} \u2192 {p.end_date}: {p.label} ({p.confidence:.0%})"
            )

    return "\n".join(lines)
