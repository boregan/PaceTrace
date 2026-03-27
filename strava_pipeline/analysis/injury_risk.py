"""
Injury risk detection — evidence-based metrics that predict running injuries.

Metrics:
1. ACWR (Acute:Chronic Workload Ratio) — sweet spot 0.8-1.3
2. Training Monotony — high monotony + high load = illness/injury
3. Training Strain — weekly load × monotony
4. Session Spikes — single runs exceeding 110% of 30-day max
5. Ramp Rate — week-over-week load increase (>10% flagged)
6. Consecutive Hard Days — lack of recovery

References:
- Gabbett (2016): Training-injury prevention paradox, ACWR
- Foster (1998): Training monotony and strain
- Nielsen et al. (2014): Predictors of running-related injuries
- BJSM 2025: Session spikes strongest predictor
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta
import math
import statistics


@dataclass
class InjuryRiskFlag:
    """A single risk flag with severity and detail."""
    metric: str
    severity: str   # "low", "moderate", "high", "critical"
    value: float
    threshold: str   # What the threshold is
    message: str
    recommendation: str


@dataclass
class InjuryRiskReport:
    """Full injury risk assessment."""
    overall_risk: str      # "low", "moderate", "high", "critical"
    risk_score: int        # 0-100
    flags: list[InjuryRiskFlag]

    # Individual metrics
    acwr: float | None
    monotony: float | None
    strain: float | None
    ramp_rate_pct: float | None
    max_session_spike_pct: float | None
    consecutive_hard_days: int

    # Context
    acute_load: float   # 7-day
    chronic_load: float  # 28-day avg
    this_week_km: float
    last_week_km: float


def _compute_acwr(daily_loads: list[float]) -> float | None:
    """
    Compute Acute:Chronic Workload Ratio.

    Acute = sum of last 7 days
    Chronic = average of last 4 weeks (28 days) per-week sum

    Uses rolling averages (simpler, well-validated).
    """
    if len(daily_loads) < 28:
        return None

    acute = sum(daily_loads[-7:])

    # Chronic = average weekly load over 28 days
    chronic_total = sum(daily_loads[-28:])
    chronic_weekly_avg = chronic_total / 4  # 4 weeks

    if chronic_weekly_avg <= 0:
        return None

    return acute / chronic_weekly_avg


def _compute_monotony(daily_loads: list[float], window: int = 7) -> float | None:
    """
    Training monotony = mean(daily load) / stdev(daily load).

    High monotony (>2.0) with high load = elevated risk.
    Rest days (load=0) MUST be included.
    """
    if len(daily_loads) < window:
        return None

    recent = daily_loads[-window:]
    mean_load = statistics.mean(recent)

    if mean_load <= 0:
        return None

    try:
        std_load = statistics.stdev(recent)
    except statistics.StatisticsError:
        return None

    if std_load <= 0:
        return mean_load / 0.1  # Effectively infinite monotony (all same load)

    return mean_load / std_load


def _compute_strain(daily_loads: list[float], window: int = 7) -> float | None:
    """Training strain = weekly load × monotony."""
    if len(daily_loads) < window:
        return None

    monotony = _compute_monotony(daily_loads, window)
    if monotony is None:
        return None

    weekly_load = sum(daily_loads[-window:])
    return weekly_load * monotony


def _detect_session_spikes(
    activity_distances_km: list[float],
    activity_dates: list[date],
) -> tuple[float, list[tuple[date, float]]]:
    """
    Detect session spikes: runs exceeding 110% of 30-day max.

    Returns: (max_spike_pct, list of (date, spike_pct) for flagged sessions)

    Evidence (BJSM 2025):
    - 10-30% spike: 64% higher injury risk (HR 1.64)
    - 30-100% spike: 52% higher risk (HR 1.52)
    - >100% spike (doubling): 128% higher risk (HR 2.28)
    """
    if not activity_distances_km or len(activity_distances_km) < 2:
        return 0.0, []

    spikes = []
    max_spike = 0.0

    for i in range(len(activity_distances_km)):
        current_date = activity_dates[i]
        current_dist = activity_distances_km[i]

        # Find max distance in 30 days before this activity
        past_max = 0.0
        for j in range(i):
            days_diff = (current_date - activity_dates[j]).days
            if 0 < days_diff <= 30:
                past_max = max(past_max, activity_distances_km[j])

        if past_max <= 0:
            continue

        spike_pct = ((current_dist - past_max) / past_max) * 100
        if spike_pct > 10:  # >110% of 30-day max
            spikes.append((current_date, round(spike_pct, 1)))
            max_spike = max(max_spike, spike_pct)

    return max_spike, spikes


def _count_consecutive_hard_days(
    daily_loads: list[float],
    threshold_pct: float = 0.7,
) -> int:
    """
    Count consecutive days above threshold % of average daily load.

    Running without rest days increases overuse injury risk.
    """
    if not daily_loads or len(daily_loads) < 7:
        return 0

    avg_load = statistics.mean(daily_loads[-28:]) if len(daily_loads) >= 28 else statistics.mean(daily_loads)
    threshold = avg_load * threshold_pct

    max_consecutive = 0
    current_streak = 0

    for load in daily_loads[-14:]:  # Check last 2 weeks
        if load > threshold:
            current_streak += 1
            max_consecutive = max(max_consecutive, current_streak)
        else:
            current_streak = 0

    return max_consecutive


def assess_injury_risk(
    daily_loads: list[float],
    activity_distances_km: list[float],
    activity_dates: list[date],
    weekly_distances: list[float] | None = None,
) -> InjuryRiskReport:
    """
    Comprehensive injury risk assessment.

    Args:
        daily_loads: Training load per day (28+ days, 0 for rest days)
        activity_distances_km: Distance of each run
        activity_dates: Date of each run
        weekly_distances: Weekly total distances (for ramp rate)

    Returns:
        InjuryRiskReport with flags and recommendations
    """
    flags: list[InjuryRiskFlag] = []
    risk_score = 0

    # 1. ACWR
    acwr = _compute_acwr(daily_loads)
    if acwr is not None:
        if acwr > 2.0:
            flags.append(InjuryRiskFlag(
                "ACWR", "critical", acwr, ">2.0",
                f"ACWR is {acwr:.2f} — extreme spike in training load",
                "Reduce volume immediately. Add extra rest days this week."
            ))
            risk_score += 35
        elif acwr > 1.5:
            flags.append(InjuryRiskFlag(
                "ACWR", "high", acwr, ">1.5",
                f"ACWR is {acwr:.2f} — significant load spike",
                "Reduce intensity this week. Consider replacing a hard session with easy running."
            ))
            risk_score += 25
        elif acwr > 1.3:
            flags.append(InjuryRiskFlag(
                "ACWR", "moderate", acwr, ">1.3",
                f"ACWR is {acwr:.2f} — approaching upper safe limit",
                "Monitor how you feel. Don't add more volume this week."
            ))
            risk_score += 10
        elif acwr < 0.8:
            flags.append(InjuryRiskFlag(
                "ACWR", "moderate", acwr, "<0.8",
                f"ACWR is {acwr:.2f} — detraining zone",
                "You've significantly reduced training. Rebuild gradually."
            ))
            risk_score += 5

    # 2. Monotony
    monotony = _compute_monotony(daily_loads)
    if monotony is not None:
        if monotony > 2.5:
            flags.append(InjuryRiskFlag(
                "Monotony", "high", monotony, ">2.5",
                f"Training monotony is {monotony:.1f} — very repetitive load pattern",
                "Vary your training: add a rest day, mix easy and hard sessions."
            ))
            risk_score += 20
        elif monotony > 2.0:
            flags.append(InjuryRiskFlag(
                "Monotony", "moderate", monotony, ">2.0",
                f"Training monotony is {monotony:.1f} — similar daily loads",
                "Consider more variation: easy/hard alternation or an extra rest day."
            ))
            risk_score += 10

    # 3. Strain
    strain = _compute_strain(daily_loads)

    # 4. Session spikes
    max_spike, spike_list = _detect_session_spikes(activity_distances_km, activity_dates)
    if max_spike > 100:
        flags.append(InjuryRiskFlag(
            "Session Spike", "critical", max_spike, ">100%",
            f"A run was {max_spike:.0f}% longer than your 30-day max — more than double",
            "This dramatically increases injury risk. Build up to longer runs over 3-4 weeks."
        ))
        risk_score += 30
    elif max_spike > 30:
        flags.append(InjuryRiskFlag(
            "Session Spike", "high", max_spike, ">30%",
            f"A run was {max_spike:.0f}% longer than your 30-day max",
            "Large jumps in single-run distance increase injury risk. Progress by max 20% per week."
        ))
        risk_score += 15
    elif max_spike > 10:
        flags.append(InjuryRiskFlag(
            "Session Spike", "low", max_spike, ">10%",
            f"A run was {max_spike:.0f}% longer than your 30-day max (minor spike)",
            "Keep an eye on how you recover."
        ))
        risk_score += 5

    # 5. Ramp rate
    ramp_rate = None
    if weekly_distances and len(weekly_distances) >= 2:
        this_week = weekly_distances[-1]
        last_week = weekly_distances[-2]
        if last_week > 0:
            ramp_rate = ((this_week - last_week) / last_week) * 100

            if ramp_rate > 30:
                flags.append(InjuryRiskFlag(
                    "Ramp Rate", "high", ramp_rate, ">30%",
                    f"Weekly volume increased {ramp_rate:.0f}% week-over-week",
                    "Very aggressive ramp. Consider a recovery week soon."
                ))
                risk_score += 20
            elif ramp_rate > 15:
                flags.append(InjuryRiskFlag(
                    "Ramp Rate", "moderate", ramp_rate, ">15%",
                    f"Weekly volume increased {ramp_rate:.0f}% week-over-week",
                    "Moderate ramp — sustainable for 1-2 weeks max before a recovery week."
                ))
                risk_score += 10

    # 6. Consecutive hard days
    consecutive = _count_consecutive_hard_days(daily_loads)
    if consecutive >= 7:
        flags.append(InjuryRiskFlag(
            "Consecutive Days", "high", consecutive, "≥7",
            f"{consecutive} consecutive hard days with no recovery",
            "Take a rest day. Even elite runners need periodic recovery."
        ))
        risk_score += 20
    elif consecutive >= 5:
        flags.append(InjuryRiskFlag(
            "Consecutive Days", "moderate", consecutive, "≥5",
            f"{consecutive} consecutive hard days — recovery needed soon",
            "Schedule an easy day or full rest day in the next 1-2 days."
        ))
        risk_score += 10

    # Overall risk
    risk_score = min(risk_score, 100)
    if risk_score >= 60:
        overall = "critical"
    elif risk_score >= 40:
        overall = "high"
    elif risk_score >= 20:
        overall = "moderate"
    else:
        overall = "low"

    # Context
    acute_load = sum(daily_loads[-7:]) if len(daily_loads) >= 7 else sum(daily_loads)
    chronic_load = sum(daily_loads[-28:]) / 4 if len(daily_loads) >= 28 else sum(daily_loads) / max(len(daily_loads) // 7, 1)
    this_week_km = weekly_distances[-1] if weekly_distances else 0
    last_week_km = weekly_distances[-2] if weekly_distances and len(weekly_distances) >= 2 else 0

    return InjuryRiskReport(
        overall_risk=overall,
        risk_score=risk_score,
        flags=flags,
        acwr=round(acwr, 2) if acwr is not None else None,
        monotony=round(monotony, 1) if monotony is not None else None,
        strain=round(strain, 0) if strain is not None else None,
        ramp_rate_pct=round(ramp_rate, 1) if ramp_rate is not None else None,
        max_session_spike_pct=round(max_spike, 1) if max_spike > 0 else None,
        consecutive_hard_days=consecutive,
        acute_load=round(acute_load, 1),
        chronic_load=round(chronic_load, 1),
        this_week_km=round(this_week_km, 1),
        last_week_km=round(last_week_km, 1),
    )


def format_injury_risk(report: InjuryRiskReport) -> str:
    """Format injury risk report for display."""
    # Risk indicator
    indicator = {
        "low": "🟢", "moderate": "🟡", "high": "🟠", "critical": "🔴"
    }

    lines = [
        f"# Injury Risk Assessment",
        f"",
        f"**Overall: {indicator.get(report.overall_risk, '⚪')} {report.overall_risk.upper()}** (score: {report.risk_score}/100)",
        f"",
        f"## Key Metrics",
    ]

    if report.acwr is not None:
        zone = "sweet spot ✓" if 0.8 <= report.acwr <= 1.3 else "⚠ outside safe range"
        lines.append(f"- **ACWR:** {report.acwr:.2f} ({zone})")
    if report.monotony is not None:
        lines.append(f"- **Monotony:** {report.monotony:.1f} {'(⚠ high)' if report.monotony > 2.0 else '(ok)'}")
    if report.strain is not None:
        lines.append(f"- **Strain:** {report.strain:.0f}")
    if report.ramp_rate_pct is not None:
        lines.append(f"- **Ramp Rate:** {report.ramp_rate_pct:+.0f}% week-over-week ({report.last_week_km:.0f}→{report.this_week_km:.0f} km)")
    if report.max_session_spike_pct is not None:
        lines.append(f"- **Max Session Spike:** +{report.max_session_spike_pct:.0f}% above 30-day max")
    lines.append(f"- **Consecutive Hard Days:** {report.consecutive_hard_days}")
    lines.append(f"- **7-day load:** {report.acute_load:.0f} | **28-day avg/wk:** {report.chronic_load:.0f}")

    if report.flags:
        lines.extend(["", "## Flags"])
        for f in sorted(report.flags, key=lambda x: {"critical": 0, "high": 1, "moderate": 2, "low": 3}[x.severity]):
            sev = indicator.get(f.severity, "⚪")
            lines.append(f"- {sev} **{f.metric}** ({f.severity}): {f.message}")
            lines.append(f"  → {f.recommendation}")
    else:
        lines.extend(["", "No flags — training load looks well-managed. ✓"])

    return "\n".join(lines)
