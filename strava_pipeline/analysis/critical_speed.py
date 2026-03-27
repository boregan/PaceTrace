"""
Critical Speed (CS) and D' (D-prime) — the fundamental aerobic/anaerobic model for runners.

CS is the speed you can sustain "indefinitely" (practically: 30-60 min).
D' is the anaerobic distance capacity above CS (like W' in cycling).

Together they define your speed-duration curve:
    Distance = CS * Time + D'

Or equivalently:
    Time_to_exhaustion = D' / (Speed - CS)

Fitted from pace curve data (best efforts at multiple durations/distances).

References:
- Poole et al. (2016): Critical Power concept
- Morton (1996): 3-parameter model
- Jones & Vanhatalo (2017): Critical Speed review
"""

from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass
class CriticalSpeedResult:
    """Result of CS/D' model fitting."""
    cs_ms: float           # Critical Speed in m/s
    cs_pace_secs_km: float # CS as pace (s/km)
    d_prime_m: float       # D' in metres (anaerobic reserve)
    r_squared: float       # Goodness of fit

    # Derived zones
    easy_pace_secs_km: float    # ~75-80% CS
    tempo_pace_secs_km: float   # ~85-90% CS
    threshold_pace_secs_km: float  # ~95-100% CS (≈ CS itself)
    vo2max_pace_secs_km: float     # ~105-110% CS

    # Model data points used
    n_points: int
    distances_used: list[float]  # metres

    @property
    def cs_pace_str(self) -> str:
        m, s = divmod(int(self.cs_pace_secs_km), 60)
        return f"{m}:{s:02d}"

    def pace_str(self, secs_km: float) -> str:
        m, s = divmod(int(secs_km), 60)
        return f"{m}:{s:02d}"

    def time_to_exhaustion(self, speed_ms: float) -> float | None:
        """Predict time to exhaustion at a given speed (seconds).
        Returns None if speed <= CS (theoretically infinite).
        """
        if speed_ms <= self.cs_ms:
            return None  # Can sustain "indefinitely"
        return self.d_prime_m / (speed_ms - self.cs_ms)

    def max_distance(self, duration_secs: float) -> float:
        """Predict maximum distance coverable in a given time."""
        return self.cs_ms * duration_secs + self.d_prime_m

    def d_prime_pct_used(self, distance_m: float, time_secs: float) -> float:
        """How much D' was used during an effort."""
        if time_secs <= 0:
            return 0.0
        speed = distance_m / time_secs
        if speed <= self.cs_ms:
            return 0.0
        d_used = (speed - self.cs_ms) * time_secs
        return min(d_used / self.d_prime_m * 100, 100.0)


def fit_critical_speed(
    distances_m: list[float],
    times_secs: list[float],
    min_duration_secs: float = 120,
    max_duration_secs: float = 1800,
) -> CriticalSpeedResult | None:
    """
    Fit CS and D' from distance-time pairs using linear regression.

    The model: Distance = CS * Time + D'
    This is a simple linear regression of distance vs time.

    Args:
        distances_m: Best effort distances in metres
        times_secs: Corresponding times in seconds
        min_duration_secs: Minimum duration to include (default 2 min)
        max_duration_secs: Maximum duration to include (default 30 min)

    Returns:
        CriticalSpeedResult or None if insufficient data
    """
    # Filter to valid duration range
    pairs = [
        (d, t) for d, t in zip(distances_m, times_secs)
        if min_duration_secs <= t <= max_duration_secs and d > 0 and t > 0
    ]

    if len(pairs) < 3:
        return None

    dists = [p[0] for p in pairs]
    times = [p[1] for p in pairs]

    # Linear regression: Distance = CS * Time + D'
    # y = mx + b where y=distance, x=time, m=CS, b=D'
    n = len(pairs)
    sum_t = sum(times)
    sum_d = sum(dists)
    sum_tt = sum(t * t for t in times)
    sum_td = sum(t * d for t, d in zip(times, dists))

    denom = n * sum_tt - sum_t ** 2
    if abs(denom) < 1e-10:
        return None

    cs = (n * sum_td - sum_t * sum_d) / denom
    d_prime = (sum_d - cs * sum_t) / n

    # Validate results
    if cs <= 0 or d_prime < 0:
        return None

    # R-squared
    mean_d = sum_d / n
    ss_tot = sum((d - mean_d) ** 2 for d in dists)
    ss_res = sum((d - (cs * t + d_prime)) ** 2 for d, t in zip(dists, times))
    r_sq = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    cs_pace = 1000.0 / cs if cs > 0 else 0

    return CriticalSpeedResult(
        cs_ms=round(cs, 3),
        cs_pace_secs_km=cs_pace,
        d_prime_m=round(d_prime, 1),
        r_squared=round(r_sq, 4),
        easy_pace_secs_km=1000.0 / (cs * 0.78),
        tempo_pace_secs_km=1000.0 / (cs * 0.88),
        threshold_pace_secs_km=cs_pace,
        vo2max_pace_secs_km=1000.0 / (cs * 1.08),
        n_points=n,
        distances_used=[p[0] for p in pairs],
    )


def fit_from_pace_curve(
    pace_curve: list[dict],
) -> CriticalSpeedResult | None:
    """
    Fit CS/D' from an intervals.icu pace curve.

    Pace curve format: list of dicts with 'secs' (duration) and 'value' (speed m/s)
    or 'distance' (metres) and 'secs' (time).

    For pace curves indexed by distance:
    [{"distance": 400, "secs": 85}, {"distance": 1000, "secs": 230}, ...]

    For pace curves indexed by duration (best speed at each duration):
    [{"secs": 60, "value": 5.2}, {"secs": 120, "value": 4.8}, ...]
    """
    distances = []
    times = []

    for point in pace_curve:
        if "distance" in point and "secs" in point:
            # Distance-indexed curve
            distances.append(float(point["distance"]))
            times.append(float(point["secs"]))
        elif "secs" in point and "value" in point:
            # Duration-indexed curve (value = speed m/s)
            t = float(point["secs"])
            v = float(point["value"])
            if v > 0:
                d = v * t  # distance = speed * time
                distances.append(d)
                times.append(t)

    if not distances:
        return None

    return fit_critical_speed(distances, times)


def format_critical_speed(result: CriticalSpeedResult | None) -> str:
    """Format CS/D' result for display."""
    if result is None:
        return "Insufficient data to compute Critical Speed (need 3+ best efforts between 2-30 min)."

    lines = [
        "# Critical Speed Profile",
        "",
        f"**Critical Speed:** {result.cs_pace_str}/km ({result.cs_ms:.2f} m/s)",
        f"**D' (Anaerobic Reserve):** {result.d_prime_m:.0f}m",
        f"**Model fit:** R² = {result.r_squared:.3f} ({result.n_points} data points)",
        "",
        "## Training Zones (from CS)",
        f"- Easy (75-80% CS): {result.pace_str(result.easy_pace_secs_km)}/km",
        f"- Tempo (85-90% CS): {result.pace_str(result.tempo_pace_secs_km)}/km",
        f"- Threshold (~CS): **{result.cs_pace_str}/km**",
        f"- VO2max (105-110% CS): {result.pace_str(result.vo2max_pace_secs_km)}/km",
        "",
        "## What This Means",
        f"- You can sustain {result.cs_pace_str}/km for ~30-60 minutes",
        f"- Above that pace, you have {result.d_prime_m:.0f}m of anaerobic reserve",
    ]

    # Predict time to exhaustion at a few paces above CS
    for pct_above, label in [(5, "5%"), (10, "10%"), (20, "20%")]:
        speed = result.cs_ms * (1 + pct_above / 100)
        tte = result.time_to_exhaustion(speed)
        if tte and tte > 0:
            pace = 1000.0 / speed
            pm, ps = divmod(int(pace), 60)
            tm, ts = divmod(int(tte), 60)
            lines.append(f"- At {pm}:{ps:02d}/km ({label} above CS): ~{tm}:{ts:02d} to exhaustion")

    return "\n".join(lines)
