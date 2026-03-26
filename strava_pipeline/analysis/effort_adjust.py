"""
Effort-Adjusted Pace — normalize running pace for conditions.

Takes raw pace and adjusts for:
1. Heat & humidity (dew point-based, per sports science literature)
2. Elevation gain (gradient-adjusted pace correction)
3. Fatigue / TSB (training stress balance at time of run)

The result: "Your 5:30/km in 28°C and 80% humidity was equivalent to
~5:05/km in ideal conditions."

References:
- Cheuvront & Haymes (2001): heat degradation in distance running
- Vihma (2010): temperature effects on marathon performance
- Minetti et al. (2002): energy cost of gradient running
- intervals.icu GAP model for elevation adjustment baseline
"""

from __future__ import annotations
from dataclasses import dataclass


# ── Constants ──────────────────────────────────────────────

# "Ideal" conditions for running: 8-12°C, low humidity, no wind, flat
IDEAL_TEMP_C = 10.0
IDEAL_DEW_POINT_C = 5.0


@dataclass
class AdjustmentResult:
    """Result of effort-adjusting a pace."""
    raw_pace_secs_km: float          # Original pace in s/km
    adjusted_pace_secs_km: float     # Adjusted pace in s/km
    total_adjustment_secs: float     # Total seconds/km adjustment
    heat_adjustment_secs: float      # Heat/humidity component
    elevation_adjustment_secs: float # Elevation component
    fatigue_adjustment_secs: float   # TSB/fatigue component
    conditions_summary: str          # Human-readable summary

    @property
    def adjusted_faster(self) -> bool:
        return self.adjusted_pace_secs_km < self.raw_pace_secs_km

    def format_pace(self, secs_km: float) -> str:
        m, s = divmod(int(secs_km), 60)
        return f"{m}:{s:02d}"

    @property
    def raw_pace_str(self) -> str:
        return self.format_pace(self.raw_pace_secs_km)

    @property
    def adjusted_pace_str(self) -> str:
        return self.format_pace(self.adjusted_pace_secs_km)

    def summary(self) -> str:
        """One-line summary of the adjustment."""
        diff = abs(self.total_adjustment_secs)
        if diff < 2:
            return f"{self.raw_pace_str} /km (near-ideal conditions, no adjustment needed)"
        direction = "faster" if self.adjusted_faster else "slower"
        return (
            f"{self.raw_pace_str} /km → **{self.adjusted_pace_str} /km** adjusted "
            f"({diff:.0f}s/km {direction} in ideal conditions)"
        )

    def breakdown(self) -> str:
        """Multi-line breakdown of each adjustment factor."""
        lines = []
        if abs(self.heat_adjustment_secs) >= 1:
            lines.append(f"  Heat/humidity: {self.heat_adjustment_secs:+.0f}s/km")
        if abs(self.elevation_adjustment_secs) >= 1:
            lines.append(f"  Elevation: {self.elevation_adjustment_secs:+.0f}s/km")
        if abs(self.fatigue_adjustment_secs) >= 1:
            lines.append(f"  Fatigue (TSB): {self.fatigue_adjustment_secs:+.0f}s/km")
        if not lines:
            lines.append("  (no significant adjustments)")
        return "\n".join(lines)


# ── Heat / Humidity Adjustment ─────────────────────────────

def _heat_adjustment_pct(temp_c: float | None, dew_point_c: float | None,
                          humidity_pct: float | None) -> float:
    """
    Return pace degradation percentage due to heat/humidity.

    Based on dew point (best single metric for running heat stress):
    - Below 10°C dew point: minimal impact
    - 10-15°C: slight impact (1-3%)
    - 15-20°C: moderate (3-6%)
    - 20-25°C: significant (6-10%)
    - 25°C+: severe (10%+)

    Also considers cold: below 0°C, slight penalty from airway
    resistance and muscle stiffness.

    Returns positive % = pace should be faster in ideal conditions
    (i.e., the heat made you slower).
    """
    if temp_c is None:
        return 0.0

    # Use dew point if available (better metric)
    if dew_point_c is not None:
        dp = dew_point_c
        if dp <= 5:
            # Near-ideal or cold — check for cold penalty
            if temp_c < -5:
                return -1.5  # Severe cold penalty
            elif temp_c < 0:
                return -0.5  # Mild cold penalty
            return 0.0
        elif dp <= 10:
            return (dp - 5) * 0.4  # 0-2% linear ramp
        elif dp <= 15:
            return 2.0 + (dp - 10) * 0.6  # 2-5%
        elif dp <= 20:
            return 5.0 + (dp - 15) * 1.0  # 5-10%
        elif dp <= 25:
            return 10.0 + (dp - 20) * 1.0  # 10-15%
        else:
            return 15.0 + (dp - 25) * 0.5  # 15%+ (capped practically)

    # Fallback: use temp + humidity
    if humidity_pct is None:
        humidity_pct = 50.0  # Assume moderate

    # Approximate dew point from temp and humidity (Magnus formula simplified)
    if temp_c > 0:
        approx_dp = temp_c - ((100 - humidity_pct) / 5)
        return _heat_adjustment_pct(temp_c, approx_dp, humidity_pct)

    # Cold without dew point
    if temp_c < -5:
        return -1.5
    elif temp_c < 0:
        return -0.5
    return 0.0


# ── Elevation Adjustment ──────────────────────────────────

def _elevation_adjustment_pct(elevation_gain_m: float | None,
                                distance_m: float | None) -> float:
    """
    Return pace degradation percentage due to elevation.

    Uses average gradient to estimate impact. This is ADDITIONAL to
    intervals.icu's GAP — we use a conservative approach since GAP
    already accounts for some of this.

    If GAP data is available, we use that directly instead.

    Average gradient impact on pace:
    - 0-1% grade: negligible
    - 1-3%: 3-8% slower
    - 3-5%: 8-15% slower
    - 5%+: 15%+ slower

    We use ~60% of the theoretical impact since GAP already handles
    some of the adjustment, and net elevation (up and down) partially
    cancels.

    Returns positive % = pace should be faster on flat.
    """
    if not elevation_gain_m or not distance_m or distance_m < 500:
        return 0.0

    # Average uphill gradient percentage
    avg_grade_pct = (elevation_gain_m / distance_m) * 100

    if avg_grade_pct < 0.5:
        return 0.0
    elif avg_grade_pct < 1.5:
        # Gentle: 0-3%
        return (avg_grade_pct - 0.5) * 3.0
    elif avg_grade_pct < 3.0:
        # Moderate: 3-8%
        return 3.0 + (avg_grade_pct - 1.5) * 3.3
    elif avg_grade_pct < 5.0:
        # Hilly: 8-15%
        return 8.0 + (avg_grade_pct - 3.0) * 3.5
    else:
        # Very hilly / trail: cap at reasonable level
        return min(15.0 + (avg_grade_pct - 5.0) * 2.0, 25.0)


# ── Fatigue / TSB Adjustment ──────────────────────────────

def _fatigue_adjustment_pct(tsb: float | None) -> float:
    """
    Return pace adjustment percentage based on Training Stress Balance.

    TSB = CTL - ATL (fitness minus fatigue).
    - TSB > +15: very fresh, rested — could run faster than "normal"
    - TSB 0 to +15: fresh to neutral
    - TSB -10 to 0: slight fatigue
    - TSB -20 to -10: moderate fatigue (2-4% slower)
    - TSB < -20: heavy fatigue (4%+ slower)

    Returns positive % = pace should be faster when fresh (i.e., fatigue
    made you slower than your fitness would predict).
    """
    if tsb is None:
        return 0.0

    if tsb >= 10:
        # Very fresh — small negative adjustment (you were faster BECAUSE rested)
        return max(-2.0, -(tsb - 10) * 0.15)
    elif tsb >= 0:
        return 0.0  # Neutral zone
    elif tsb >= -10:
        return abs(tsb) * 0.15  # 0-1.5%
    elif tsb >= -25:
        return 1.5 + (abs(tsb) - 10) * 0.2  # 1.5-4.5%
    else:
        return min(4.5 + (abs(tsb) - 25) * 0.1, 8.0)  # Cap at 8%


# ── Main Function ─────────────────────────────────────────

def adjust_pace(
    speed_ms: float | None,
    temp_c: float | None = None,
    dew_point_c: float | None = None,
    humidity_pct: float | None = None,
    elevation_gain_m: float | None = None,
    distance_m: float | None = None,
    gap_speed_ms: float | None = None,
    ctl: float | None = None,
    atl: float | None = None,
) -> AdjustmentResult | None:
    """
    Adjust a raw pace for heat, elevation, and fatigue.

    Args:
        speed_ms: Average speed in m/s
        temp_c: Temperature in Celsius
        dew_point_c: Dew point in Celsius (preferred over humidity)
        humidity_pct: Relative humidity percentage
        elevation_gain_m: Total elevation gain in metres
        distance_m: Total distance in metres
        gap_speed_ms: Grade-adjusted pace speed (if available, replaces elevation calc)
        ctl: Chronic Training Load (fitness) at time of run
        atl: Acute Training Load (fatigue) at time of run

    Returns:
        AdjustmentResult with raw and adjusted paces, or None if no speed data.
    """
    if not speed_ms or speed_ms <= 0:
        return None

    raw_pace_secs_km = 1000.0 / speed_ms

    # Calculate each adjustment component
    heat_pct = _heat_adjustment_pct(temp_c, dew_point_c, humidity_pct)

    # For elevation: if GAP is available, use the difference between
    # actual pace and GAP as the elevation adjustment — BUT only if
    # the GAP difference is proportionate to actual elevation.
    # For interval sessions, GAP can diverge wildly from average speed
    # due to mixed intensities, giving false "elevation" adjustments.
    use_gap = False
    elev_secs = 0.0
    if gap_speed_ms and gap_speed_ms > 0:
        gap_pace_secs_km = 1000.0 / gap_speed_ms
        gap_diff = raw_pace_secs_km - gap_pace_secs_km

        # Sanity check: compare GAP-based adjustment to what gradient predicts
        gradient_pct = _elevation_adjustment_pct(elevation_gain_m, distance_m)
        gradient_secs = raw_pace_secs_km * (gradient_pct / 100) if gradient_pct else 0

        # If GAP says >3x what gradient predicts, GAP is likely polluted
        # by intensity variation (intervals, fartleks) — use gradient instead
        if gradient_secs > 0 and abs(gap_diff) > gradient_secs * 3:
            use_gap = False
        elif abs(gap_diff) > 30 and (not elevation_gain_m or not distance_m
                                      or (elevation_gain_m / distance_m) < 0.01):
            # GAP says >30s/km adjustment but grade is <1% — suspicious
            use_gap = False
        else:
            use_gap = True
            elev_secs = gap_diff
            elev_pct = (elev_secs / raw_pace_secs_km) * 100

    if not use_gap:
        elev_pct = _elevation_adjustment_pct(elevation_gain_m, distance_m)

    tsb = (ctl - atl) if (ctl is not None and atl is not None) else None
    fatigue_pct = _fatigue_adjustment_pct(tsb)

    # Total adjustment: how much faster the pace would be in ideal conditions
    total_pct = heat_pct + elev_pct + fatigue_pct

    # Convert percentages to seconds/km
    heat_secs = raw_pace_secs_km * (heat_pct / 100)
    elev_secs_adj = elev_secs if use_gap else raw_pace_secs_km * (elev_pct / 100)
    fatigue_secs = raw_pace_secs_km * (fatigue_pct / 100)
    total_secs = heat_secs + elev_secs_adj + fatigue_secs

    adjusted_pace = raw_pace_secs_km - total_secs

    # Build conditions summary
    parts = []
    if abs(heat_pct) >= 0.5:
        if heat_pct > 0:
            parts.append(f"heat/humidity ({temp_c:.0f}°C)")
        else:
            parts.append(f"cold ({temp_c:.0f}°C)")
    if abs(elev_pct) >= 0.5:
        if elevation_gain_m:
            parts.append(f"+{elevation_gain_m:.0f}m elevation")
        elif gap_speed_ms:
            parts.append("hilly (from GAP)")
    if abs(fatigue_pct) >= 0.5:
        if tsb is not None:
            parts.append(f"TSB {tsb:+.0f}")

    summary = ", ".join(parts) if parts else "near-ideal conditions"

    return AdjustmentResult(
        raw_pace_secs_km=raw_pace_secs_km,
        adjusted_pace_secs_km=adjusted_pace,
        total_adjustment_secs=total_secs,
        heat_adjustment_secs=heat_secs,
        elevation_adjustment_secs=elev_secs_adj,
        fatigue_adjustment_secs=fatigue_secs,
        conditions_summary=summary,
    )


def format_adjusted_pace(result: AdjustmentResult | None) -> str:
    """Format an adjustment result for MCP output."""
    if result is None:
        return "—"
    return result.summary()


def format_adjusted_pace_detail(result: AdjustmentResult | None) -> str:
    """Detailed multi-line format for activity view."""
    if result is None:
        return "—"
    lines = [result.summary()]
    if abs(result.total_adjustment_secs) >= 2:
        lines.append(result.breakdown())
    return "\n".join(lines)
