"""
Race prediction — predict finish times at any distance from recent performances.

Three models:
1. VDOT (Daniels-Gilbert): Uses VO2max proxy from race performances
2. Riegel: Simple fatigue-factor formula (T2 = T1 * (D2/D1)^1.06)
3. Cameron: Nonlinear regression from world records

Plus "Marathon Shape" readiness metric from Runalyze:
- Required endurance = distance^1.23
- Compares actual training volume to required endurance
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math


# Standard race distances in metres
DISTANCES = {
    "400m": 400,
    "800m": 800,
    "1K": 1000,
    "Mile": 1609.34,
    "3K": 3000,
    "5K": 5000,
    "10K": 10000,
    "15K": 15000,
    "Half": 21097.5,
    "Marathon": 42195,
}


@dataclass
class RacePrediction:
    """Prediction for a single distance."""
    distance_m: float
    distance_label: str
    time_secs: float
    pace_secs_km: float
    model: str  # "vdot", "riegel", "cameron"

    @property
    def time_str(self) -> str:
        h, rem = divmod(int(self.time_secs), 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def pace_str(self) -> str:
        m, s = divmod(int(self.pace_secs_km), 60)
        return f"{m}:{s:02d}"


@dataclass
class RaceReadiness:
    """Marathon shape / endurance readiness for a target distance."""
    target_distance_m: float
    target_label: str
    shape_pct: float  # 0-100+, 100% = fully ready
    weekly_avg_km: float
    longest_run_km: float
    required_weekly_km: float
    verdict: str  # "Ready", "Building", "Insufficient"


@dataclass
class PredictionResult:
    """Full race prediction from a seed performance."""
    seed_distance_m: float
    seed_time_secs: float
    seed_pace_str: str
    vdot: float
    predictions: dict[str, list[RacePrediction]]  # model -> predictions
    readiness: list[RaceReadiness]  # marathon shape for key distances

    def consensus(self, distance_label: str) -> tuple[float, float] | None:
        """Average time across all models for a distance."""
        times = []
        for model_preds in self.predictions.values():
            for p in model_preds:
                if p.distance_label == distance_label:
                    times.append(p.time_secs)
        if not times:
            return None
        avg = sum(times) / len(times)
        spread = max(times) - min(times)
        return avg, spread


# ── VDOT (Daniels-Gilbert) ─────────────────────────────────

def _vo2_from_velocity(v_m_min: float) -> float:
    """VO2 cost of running at velocity v (m/min)."""
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _vo2max_fraction(t_min: float) -> float:
    """Fraction of VO2max sustainable for duration t (minutes)."""
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + \
           0.2989558 * math.exp(-0.1932605 * t_min)


def compute_vdot(distance_m: float, time_secs: float) -> float:
    """Compute VDOT from a race performance."""
    t_min = time_secs / 60.0
    v_m_min = distance_m / t_min
    vo2 = _vo2_from_velocity(v_m_min)
    frac = _vo2max_fraction(t_min)
    return vo2 / frac


def predict_from_vdot(vdot: float, target_distance_m: float) -> float:
    """Predict time (secs) for a distance given a VDOT score.

    Uses binary search since the VDOT equation isn't analytically invertible.
    """
    lo, hi = 30.0, 86400.0  # 30s to 24h search range
    for _ in range(100):  # binary search
        mid = (lo + hi) / 2
        t_min = mid / 60.0
        v_m_min = target_distance_m / t_min
        vo2 = _vo2_from_velocity(v_m_min)
        frac = _vo2max_fraction(t_min)
        computed_vdot = vo2 / frac
        if computed_vdot > vdot:
            lo = mid  # too fast, slow down
        else:
            hi = mid  # too slow, speed up
    return (lo + hi) / 2


# ── Riegel Formula ──────────────────────────────────────────

def predict_riegel(seed_distance_m: float, seed_time_secs: float,
                    target_distance_m: float, exponent: float = 1.06) -> float:
    """Predict time using Riegel's formula: T2 = T1 * (D2/D1)^exponent."""
    return seed_time_secs * (target_distance_m / seed_distance_m) ** exponent


# ── Cameron Model ───────────────────────────────────────────

def _cameron_factor(distance_miles: float) -> float:
    """Cameron's pace adjustment factor for a distance (miles)."""
    return 13.49681 - 0.048865 * distance_miles + \
           2.438936 / (distance_miles ** 0.7905)


def predict_cameron(seed_distance_m: float, seed_time_secs: float,
                     target_distance_m: float) -> float:
    """Predict time using Cameron's model (nonlinear regression from records)."""
    d1_miles = seed_distance_m / 1609.34
    d2_miles = target_distance_m / 1609.34

    # Avoid issues with very short distances
    if d1_miles < 0.25 or d2_miles < 0.25:
        return predict_riegel(seed_distance_m, seed_time_secs, target_distance_m)

    a = _cameron_factor(d1_miles)
    b = _cameron_factor(d2_miles)

    pace_per_mile_1 = seed_time_secs / d1_miles
    pace_per_mile_2 = pace_per_mile_1 * (a / b)

    return pace_per_mile_2 * d2_miles


# ── Marathon Shape (Endurance Readiness) ────────────────────

def compute_readiness(
    target_distance_m: float,
    target_label: str,
    weekly_distances_km: list[float],
    longest_run_km: float,
) -> RaceReadiness:
    """
    Compute marathon shape / endurance readiness for a target distance.

    Based on Runalyze's formula: required_endurance = distance^1.23
    Compares actual weekly volume to required weekly volume.
    """
    target_km = target_distance_m / 1000

    # Required weekly volume scales with target distance
    # For marathon (42.195km): ~100% shape = ~60-70km/week
    # For half (21.1km): ~42.5% shape needed
    required_weekly = target_km ** 1.23

    # Average weekly distance over recent weeks
    if weekly_distances_km:
        avg_weekly = sum(weekly_distances_km) / len(weekly_distances_km)
    else:
        avg_weekly = 0.0

    # Shape percentage
    shape_pct = (avg_weekly / required_weekly * 100) if required_weekly > 0 else 0

    # Also factor in longest run (should be ≥ 50% of race distance for halfs,
    # ≥ 75% for marathon at minimum)
    long_run_factor = min(longest_run_km / (target_km * 0.75), 1.0) if target_km > 0 else 0

    # Blend: 70% volume-based, 30% long-run-based
    blended_pct = shape_pct * 0.7 + (long_run_factor * 100) * 0.3

    if blended_pct >= 90:
        verdict = "Ready"
    elif blended_pct >= 60:
        verdict = "Building"
    else:
        verdict = "Insufficient"

    return RaceReadiness(
        target_distance_m=target_distance_m,
        target_label=target_label,
        shape_pct=round(blended_pct, 1),
        weekly_avg_km=round(avg_weekly, 1),
        longest_run_km=round(longest_run_km, 1),
        required_weekly_km=round(required_weekly, 1),
        verdict=verdict,
    )


# ── Main Prediction Function ───────────────────────────────

def predict_races(
    seed_distance_m: float,
    seed_time_secs: float,
    weekly_distances_km: list[float] | None = None,
    longest_run_km: float = 0.0,
    target_distances: dict[str, float] | None = None,
) -> PredictionResult:
    """
    Generate race predictions from a seed performance.

    Args:
        seed_distance_m: Seed race distance in metres
        seed_time_secs: Seed race time in seconds
        weekly_distances_km: Recent weekly distances for readiness calc
        longest_run_km: Longest single run in recent training
        target_distances: Custom distances to predict (default: standard set)

    Returns:
        PredictionResult with predictions from all 3 models + readiness
    """
    if target_distances is None:
        target_distances = DISTANCES

    vdot = compute_vdot(seed_distance_m, seed_time_secs)

    # Generate predictions from each model
    predictions: dict[str, list[RacePrediction]] = {
        "vdot": [], "riegel": [], "cameron": []
    }

    for label, dist_m in sorted(target_distances.items(), key=lambda x: x[1]):
        # Skip distances very close to seed (not useful to predict yourself)
        # Also skip distances < 400m (models not valid)
        if dist_m < 400:
            continue

        # VDOT
        t = predict_from_vdot(vdot, dist_m)
        predictions["vdot"].append(RacePrediction(
            distance_m=dist_m, distance_label=label,
            time_secs=t, pace_secs_km=t / (dist_m / 1000), model="vdot"
        ))

        # Riegel
        t = predict_riegel(seed_distance_m, seed_time_secs, dist_m)
        predictions["riegel"].append(RacePrediction(
            distance_m=dist_m, distance_label=label,
            time_secs=t, pace_secs_km=t / (dist_m / 1000), model="riegel"
        ))

        # Cameron
        t = predict_cameron(seed_distance_m, seed_time_secs, dist_m)
        predictions["cameron"].append(RacePrediction(
            distance_m=dist_m, distance_label=label,
            time_secs=t, pace_secs_km=t / (dist_m / 1000), model="cameron"
        ))

    # Readiness for key distances
    readiness = []
    if weekly_distances_km:
        for label in ["10K", "Half", "Marathon"]:
            if label in target_distances:
                readiness.append(compute_readiness(
                    target_distances[label], label,
                    weekly_distances_km, longest_run_km
                ))

    seed_pace = seed_time_secs / (seed_distance_m / 1000)
    m, s = divmod(int(seed_pace), 60)

    return PredictionResult(
        seed_distance_m=seed_distance_m,
        seed_time_secs=seed_time_secs,
        seed_pace_str=f"{m}:{s:02d}",
        vdot=round(vdot, 1),
        predictions=predictions,
        readiness=readiness,
    )


def format_predictions(result: PredictionResult) -> str:
    """Format predictions as a readable table."""
    lines = [
        f"# Race Predictions",
        f"",
        f"**Seed:** {result.seed_pace_str}/km → VDOT {result.vdot}",
        f"",
        f"| Distance | VDOT | Riegel | Cameron | Consensus | Pace |",
        f"|----------|------|--------|---------|-----------|------|",
    ]

    # Get all distance labels from VDOT predictions
    for vp in result.predictions["vdot"]:
        label = vp.distance_label

        rp = next((p for p in result.predictions["riegel"] if p.distance_label == label), None)
        cp = next((p for p in result.predictions["cameron"] if p.distance_label == label), None)

        consensus = result.consensus(label)
        if consensus:
            avg_t, spread = consensus
            h, rem = divmod(int(avg_t), 3600)
            m, s = divmod(rem, 60)
            if h:
                cons_str = f"{h}:{m:02d}:{s:02d}"
            else:
                cons_str = f"{m}:{s:02d}"

            pace = avg_t / (vp.distance_m / 1000)
            pm, ps = divmod(int(pace), 60)
            pace_str = f"{pm}:{ps:02d}/km"
        else:
            cons_str = "—"
            pace_str = "—"

        lines.append(
            f"| {label:8s} | {vp.time_str:>8s} | {rp.time_str if rp else '—':>8s} | "
            f"{cp.time_str if cp else '—':>8s} | **{cons_str:>8s}** | {pace_str} |"
        )

    # Readiness section
    if result.readiness:
        lines.extend(["", "## Endurance Readiness (Marathon Shape)"])
        for r in result.readiness:
            bar_len = min(int(r.shape_pct / 5), 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(
                f"- **{r.target_label}**: {r.shape_pct:.0f}% [{bar}] — {r.verdict}"
            )
            lines.append(
                f"  (avg {r.weekly_avg_km:.0f}km/wk, longest {r.longest_run_km:.0f}km, "
                f"need ~{r.required_weekly_km:.0f}km/wk)"
            )

    return "\n".join(lines)
