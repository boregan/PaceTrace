"""
Run Profile — pre-computed fingerprint for every run.

Extracts ~40 domain-specific metrics + catch22 time-series features
from second-by-second stream data. Computed once per run, stored in DB,
queried instantly across entire history.

Two layers:
  1. Domain metrics: pacing, HR, cadence, elevation — what runners ask about by name
  2. catch22 features: 22 canonical time-series characteristics per stream —
     captures the "shape" of data for similarity search and questions we haven't
     thought of yet

References:
  - catch22: Lubba et al. (2019) "catch22: CAnonical Time-series CHaracteristics"
  - Henderson & Fulcher (2021) empirical evaluation of time-series feature sets
  - TrainingPeaks, Golden Cheetah, Runalyze metric definitions
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, asdict
from typing import Any

try:
    import pycatch22
    HAS_CATCH22 = True
except ImportError:
    HAS_CATCH22 = False


# ── Domain Metrics ─────────────────────────────────────────

@dataclass
class RunProfile:
    """Pre-computed fingerprint for a single run."""

    activity_id: str
    athlete_id: str = ""

    # ── Pacing ──────────────────────────────
    negative_split_ratio: float | None = None      # 2nd half pace / 1st half pace (<1 = negative split)
    pace_cv: float | None = None                   # coefficient of variation of pace (lower = more consistent)
    fade_index: float | None = None                # last 25% pace vs first 25% (>1 = faded)
    variability_index: float | None = None         # normalized pace / avg pace
    best_1k_pace_secs: float | None = None         # fastest 1km segment (s/km)
    even_pace_score: float | None = None           # 0-100, how even the pacing was

    # Per-km splits stored as list
    km_splits: list[float] = field(default_factory=list)  # pace in s/km per km

    # ── Heart Rate ──────────────────────────
    hr_drift_pct: float | None = None              # 2nd half avg HR vs 1st half (positive = drifted up)
    hr_max_time_pct: float | None = None           # when in the run max HR occurred (0-100%)
    hr_above_90pct_secs: float | None = None       # seconds above 90% of max HR in run
    hr_above_threshold_secs: float | None = None   # seconds above user threshold (default 170)
    hr_zone_pcts: list[float] = field(default_factory=list)  # % time in Z1-Z5
    hr_cv: float | None = None                     # HR coefficient of variation
    hr_recovery_30s: float | None = None           # HR drop in last 30s of run

    # ── Cadence ─────────────────────────────
    cadence_cv: float | None = None                # cadence consistency
    cadence_avg: float | None = None
    stop_count: int = 0                            # times cadence dropped to ~0 for 5+ seconds
    total_stopped_secs: float = 0                  # total time stopped (cadence near zero)

    # ── Elevation ───────────────────────────
    elevation_profile: str = ""                    # flat/rolling/hilly/mountainous
    climb_score: float | None = None               # elevation gain per km
    max_gradient_pct: float | None = None          # steepest segment

    # ── Stop context (for classification) ────
    avg_stop_duration_secs: float | None = None    # avg duration per stop
    stop_regularity: float | None = None           # CV of stop durations (low = structured)
    hr_at_stop_onset_pct: float | None = None      # mean HR% at moment of each stop

    # ── Intensity metrics ─────────────────────
    pace_cv_moving: float | None = None            # pace CV excluding stopped time
    intensity_factor: float | None = None          # avg_pace / threshold_pace
    high_intensity_bouts: int = 0                  # segments where HR > 88% HRmax for >30s

    # ── Classification ────────────────────────
    run_type: str | None = None                    # auto-classified type
    run_type_confidence: float | None = None       # 0-1 confidence

    # ── Effort / Intensity ──────────────────
    intensity_distribution: str = ""               # polarised/threshold/pyramidal/junk
    time_in_easy_pct: float | None = None          # % time in easy zone
    time_in_hard_pct: float | None = None          # % time in hard zone

    # ── catch22 shape features ──────────────
    # Each is a dict of 22 named features, or None if catch22 unavailable
    catch22_pace: dict[str, float] = field(default_factory=dict)
    catch22_hr: dict[str, float] = field(default_factory=dict)
    catch22_cadence: dict[str, float] = field(default_factory=dict)
    catch22_altitude: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to flat dict for DB storage."""
        d = asdict(self)
        # Flatten catch22 dicts with prefixes
        for stream_name in ["pace", "hr", "cadence", "altitude"]:
            c22 = d.pop(f"catch22_{stream_name}", {})
            for feat_name, val in c22.items():
                d[f"c22_{stream_name}_{feat_name}"] = val
        return d

    def to_db_row(self) -> dict:
        """Convert to JSON-friendly dict for Supabase."""
        d = asdict(self)
        # Store catch22 as JSONB columns rather than flattening
        # Clean up NaN/Inf values
        for k, v in d.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


# ── Feature Extraction ─────────────────────────────────────

def _compute_catch22(data: list[float], name: str) -> dict[str, float]:
    """Compute catch22 features for a single stream."""
    if not HAS_CATCH22 or not data or len(data) < 10:
        return {}

    # Filter out zeros/nulls and downsample if very long
    clean = [float(x) for x in data if x is not None and x > 0]
    if len(clean) < 10:
        return {}

    # Downsample to ~2000 points max (catch22 is fast but no need for 10k+ points)
    if len(clean) > 2000:
        step = len(clean) // 2000
        clean = clean[::step]

    try:
        result = pycatch22.catch22_all(clean)
        return dict(zip(result["names"], result["values"]))
    except Exception:
        return {}


def _coefficient_of_variation(data: list[float]) -> float | None:
    """CV = std_dev / mean. Lower = more consistent."""
    clean = [x for x in data if x and x > 0]
    if len(clean) < 5:
        return None
    mean = statistics.mean(clean)
    if mean == 0:
        return None
    return statistics.stdev(clean) / mean


def _detect_stops(cadence: list[float], time: list[float]) -> tuple[int, float]:
    """Detect stops from cadence dropping to near zero.

    Returns (stop_count, total_stopped_seconds).
    """
    if not cadence or not time or len(cadence) != len(time):
        return 0, 0.0

    stop_count = 0
    total_stopped = 0.0
    in_stop = False
    stop_start = 0

    for i, (cad, t) in enumerate(zip(cadence, time)):
        is_stopped = (cad is not None and cad < 10) or cad is None or cad == 0

        if is_stopped and not in_stop:
            in_stop = True
            stop_start = t
        elif not is_stopped and in_stop:
            stop_duration = t - stop_start
            if stop_duration >= 5:  # Only count stops > 5 seconds
                stop_count += 1
                total_stopped += stop_duration
            in_stop = False

    # Handle stop at end of run
    if in_stop and time:
        stop_duration = time[-1] - stop_start
        if stop_duration >= 5:
            stop_count += 1
            total_stopped += stop_duration

    return stop_count, total_stopped


@dataclass
class StopInfo:
    """Detailed stop information for classification."""
    count: int
    total_secs: float
    avg_duration_secs: float
    regularity: float | None  # CV of stop durations (None if < 2 stops)
    hr_at_onset_pcts: list[float]  # HR as %HRmax at each stop onset


def _detect_stops_detailed(
    cadence: list[float],
    time: list[float],
    heartrate: list[float] | None = None,
    max_hr: float = 185.0,
) -> StopInfo:
    """Detect stops with detailed per-stop metrics.

    Like _detect_stops but also tracks individual stop durations and HR at onset.
    """
    if not cadence or not time or len(cadence) != len(time):
        return StopInfo(count=0, total_secs=0.0, avg_duration_secs=0.0,
                        regularity=None, hr_at_onset_pcts=[])

    has_hr = heartrate is not None and len(heartrate) == len(cadence)

    stop_durations: list[float] = []
    hr_at_onset: list[float] = []
    in_stop = False
    stop_start = 0
    stop_start_idx = 0

    for i, (cad, t) in enumerate(zip(cadence, time)):
        is_stopped = (cad is not None and cad < 10) or cad is None or cad == 0

        if is_stopped and not in_stop:
            in_stop = True
            stop_start = t
            stop_start_idx = i
        elif not is_stopped and in_stop:
            stop_duration = t - stop_start
            if stop_duration >= 5:  # Only count stops > 5 seconds
                stop_durations.append(stop_duration)
                if has_hr and stop_start_idx < len(heartrate):
                    hr_val = heartrate[stop_start_idx]
                    if hr_val and hr_val > 0 and max_hr > 0:
                        hr_at_onset.append((hr_val / max_hr) * 100.0)
            in_stop = False

    # Handle stop at end of run
    if in_stop and time:
        stop_duration = time[-1] - stop_start
        if stop_duration >= 5:
            stop_durations.append(stop_duration)
            if has_hr and stop_start_idx < len(heartrate):
                hr_val = heartrate[stop_start_idx]
                if hr_val and hr_val > 0 and max_hr > 0:
                    hr_at_onset.append((hr_val / max_hr) * 100.0)

    count = len(stop_durations)
    total_secs = sum(stop_durations)
    avg_dur = (total_secs / count) if count > 0 else 0.0

    # CV of stop durations (regularity)
    regularity: float | None = None
    if count >= 2:
        mean_dur = statistics.mean(stop_durations)
        if mean_dur > 0:
            regularity = statistics.stdev(stop_durations) / mean_dur

    return StopInfo(
        count=count,
        total_secs=total_secs,
        avg_duration_secs=round(avg_dur, 1),
        regularity=round(regularity, 4) if regularity is not None else None,
        hr_at_onset_pcts=hr_at_onset,
    )


def _compute_pace_cv_moving(
    velocity: list[float],
    time: list[float],
    min_speed: float = 1.0,
) -> float | None:
    """Compute pace CV from speed data, excluding stopped/near-stopped points.

    Applies a 10-second rolling average to smooth GPS noise before computing CV.
    Points with speed < min_speed are excluded.
    """
    if not velocity or not time or len(velocity) != len(time):
        return None

    # Filter to moving points only
    moving = [(t, v) for t, v in zip(time, velocity)
              if v is not None and v >= min_speed]
    if len(moving) < 10:
        return None

    # Apply 10-second rolling average
    smoothed: list[float] = []
    for i, (t_i, _) in enumerate(moving):
        window_vals = []
        for j in range(max(0, i - 10), min(len(moving), i + 11)):
            t_j = moving[j][0]
            if abs(t_j - t_i) <= 5.0:  # within +/- 5s = 10s window
                window_vals.append(moving[j][1])
        if window_vals:
            smoothed.append(statistics.mean(window_vals))

    if len(smoothed) < 5:
        return None

    mean_v = statistics.mean(smoothed)
    if mean_v <= 0:
        return None

    return round(statistics.stdev(smoothed) / mean_v, 4)


def _count_high_intensity_bouts(
    heartrate: list[float],
    time: list[float],
    max_hr: float = 185.0,
    threshold_pct: float = 0.88,
    min_duration_secs: float = 30.0,
) -> int:
    """Count segments where HR exceeds threshold_pct of max_hr for at least min_duration_secs."""
    if not heartrate or not time or len(heartrate) != len(time):
        return 0

    hr_thresh = max_hr * threshold_pct
    bout_count = 0
    bout_start: float | None = None

    for i, (hr, t) in enumerate(zip(heartrate, time)):
        if hr is not None and hr >= hr_thresh:
            if bout_start is None:
                bout_start = t
        else:
            if bout_start is not None:
                if t - bout_start >= min_duration_secs:
                    bout_count += 1
                bout_start = None

    # Handle bout at end of data
    if bout_start is not None and time:
        if time[-1] - bout_start >= min_duration_secs:
            bout_count += 1

    return bout_count


def _compute_intensity_factor(
    velocity: list[float],
    threshold_speed: float = 3.33,
    min_speed: float = 1.0,
) -> float | None:
    """Intensity Factor = avg moving speed / threshold speed.

    Only includes points where speed >= min_speed (i.e. actually moving).
    """
    if not velocity:
        return None

    moving_speeds = [v for v in velocity if v is not None and v >= min_speed]
    if len(moving_speeds) < 10:
        return None

    if threshold_speed <= 0:
        return None

    avg_moving = statistics.mean(moving_speeds)
    return round(avg_moving / threshold_speed, 3)


def _compute_km_splits(distance: list[float], time: list[float],
                        velocity: list[float]) -> list[float]:
    """Compute per-km splits in seconds/km."""
    if not distance or not time:
        return []

    total_dist = distance[-1] if distance else 0
    if total_dist < 500:  # Less than 500m, skip
        return []

    num_kms = int(total_dist / 1000)
    splits = []
    km_idx = 0

    for km in range(1, num_kms + 1):
        target_dist = km * 1000
        # Find the time index where we crossed this km
        for i in range(km_idx, len(distance)):
            if distance[i] >= target_dist:
                # Interpolate time at exact km mark
                if i > 0 and distance[i] != distance[i - 1]:
                    frac = (target_dist - distance[i - 1]) / (distance[i] - distance[i - 1])
                    t_at_km = time[i - 1] + frac * (time[i] - time[i - 1])
                else:
                    t_at_km = time[i]

                if km == 1:
                    split_time = t_at_km
                else:
                    split_time = t_at_km - prev_t

                splits.append(split_time)
                prev_t = t_at_km
                km_idx = i
                break

    return splits


def _elevation_profile(altitude: list[float], distance: list[float]) -> tuple[str, float | None, float | None]:
    """Classify elevation profile and compute climb score.

    Returns (profile_label, climb_score_m_per_km, max_gradient_pct).
    """
    if not altitude or not distance or len(altitude) < 20:
        return "", None, None

    total_dist = distance[-1] if distance else 0
    if total_dist < 500:
        return "", None, None

    # Compute total ascent
    total_ascent = 0.0
    # Smooth altitude to avoid GPS noise (simple moving average)
    window = min(10, len(altitude) // 5)
    if window < 3:
        smoothed = altitude
    else:
        smoothed = []
        for i in range(len(altitude)):
            start = max(0, i - window // 2)
            end = min(len(altitude), i + window // 2 + 1)
            smoothed.append(sum(altitude[start:end]) / (end - start))

    for i in range(1, len(smoothed)):
        diff = smoothed[i] - smoothed[i - 1]
        if diff > 0:
            total_ascent += diff

    climb_score = (total_ascent / total_dist) * 1000  # metres per km

    # Max gradient (over 100m segments)
    max_grad = 0.0
    seg_length = 100  # metres
    for i in range(len(distance)):
        for j in range(i + 1, len(distance)):
            if distance[j] - distance[i] >= seg_length:
                d_dist = distance[j] - distance[i]
                d_alt = smoothed[j] - smoothed[i]
                grad = abs(d_alt / d_dist) * 100
                max_grad = max(max_grad, grad)
                break

    # Classify
    if climb_score < 5:
        profile = "flat"
    elif climb_score < 15:
        profile = "rolling"
    elif climb_score < 30:
        profile = "hilly"
    else:
        profile = "mountainous"

    return profile, round(climb_score, 1), round(max_grad, 1)


def _classify_intensity(velocity: list[float], heartrate: list[float],
                         threshold_pace_ms: float = 3.5) -> tuple[str, float | None, float | None]:
    """Classify training intensity distribution.

    Returns (distribution_type, easy_pct, hard_pct).
    """
    if not velocity or len(velocity) < 60:
        return "", None, None

    # Use velocity relative to threshold
    easy = 0
    moderate = 0
    hard = 0
    total = 0

    for v in velocity:
        if v is None or v <= 0:
            continue
        total += 1
        ratio = v / threshold_pace_ms
        if ratio < 0.80:
            easy += 1
        elif ratio < 0.95:
            moderate += 1
        else:
            hard += 1

    if total == 0:
        return "", None, None

    easy_pct = (easy / total) * 100
    mod_pct = (moderate / total) * 100
    hard_pct = (hard / total) * 100

    # Classify (Seiler's training distribution model)
    if easy_pct > 70 and hard_pct > 15:
        dist_type = "polarised"
    elif mod_pct > 40:
        dist_type = "threshold"
    elif easy_pct > 60 and mod_pct > hard_pct:
        dist_type = "pyramidal"
    elif mod_pct > 30 and hard_pct < 10:
        dist_type = "junk"  # too much moderate, not enough easy or hard
    else:
        dist_type = "mixed"

    return dist_type, round(easy_pct, 1), round(hard_pct, 1)


def compute_profile(
    activity_id: str,
    athlete_id: str,
    streams: dict[str, list],
    activity_data: dict | None = None,
    hr_threshold: int = 170,
) -> RunProfile:
    """
    Compute a full RunProfile from stream data.

    Args:
        activity_id: intervals.icu activity ID
        athlete_id: intervals.icu athlete ID
        streams: dict mapping stream type -> list of values
                 Expected keys: time, velocity_smooth, heartrate, cadence, altitude, distance
        activity_data: optional activity summary dict for supplementary data
        hr_threshold: HR threshold for "above threshold" calculation
    """
    profile = RunProfile(activity_id=activity_id, athlete_id=athlete_id)

    time = streams.get("time", [])
    velocity = streams.get("velocity_smooth", [])
    heartrate = streams.get("heartrate", [])
    cadence = streams.get("cadence", [])
    altitude = streams.get("altitude", [])
    distance = streams.get("distance", [])

    n = len(time)
    if n < 30:
        return profile  # Too short to analyse

    # ── Pacing metrics ─────────────────────────────────

    # Filter valid velocity points (non-zero, moving)
    valid_v = [(i, v) for i, v in enumerate(velocity) if v and v > 0.5]

    if valid_v:
        mid = len(valid_v) // 2
        first_half_speeds = [v for _, v in valid_v[:mid]]
        second_half_speeds = [v for _, v in valid_v[mid:]]

        if first_half_speeds and second_half_speeds:
            avg_first = statistics.mean(first_half_speeds)
            avg_second = statistics.mean(second_half_speeds)
            if avg_first > 0:
                # Ratio of 2nd half pace to 1st half pace
                # pace = 1/speed, so pace_ratio = speed_first / speed_second
                profile.negative_split_ratio = round(avg_first / avg_second, 3)

        # Fade index: last 25% vs first 25%
        q1 = len(valid_v) // 4
        q4_start = len(valid_v) - q1
        if q1 > 5:
            first_q_speeds = [v for _, v in valid_v[:q1]]
            last_q_speeds = [v for _, v in valid_v[q4_start:]]
            avg_first_q = statistics.mean(first_q_speeds)
            avg_last_q = statistics.mean(last_q_speeds)
            if avg_last_q > 0:
                profile.fade_index = round(avg_first_q / avg_last_q, 3)

        # Pace CV
        all_speeds = [v for _, v in valid_v]
        profile.pace_cv = _coefficient_of_variation(all_speeds)
        if profile.pace_cv is not None:
            profile.pace_cv = round(profile.pace_cv, 4)

        # Variability index: normalized pace / avg pace
        # Normalized pace = average of pace^4, then ^(1/4)  (similar to NP in power)
        if all_speeds:
            avg_speed = statistics.mean(all_speeds)
            norm_speed = (statistics.mean([v ** 4 for v in all_speeds])) ** 0.25
            if avg_speed > 0:
                profile.variability_index = round(norm_speed / avg_speed, 3)

        # Even pace score (0-100, based on CV)
        if profile.pace_cv is not None:
            # CV of 0 = perfect (100), CV of 0.3+ = terrible (0)
            score = max(0, min(100, 100 * (1 - profile.pace_cv / 0.3)))
            profile.even_pace_score = round(score, 1)

    # Per-km splits
    profile.km_splits = _compute_km_splits(distance, time, velocity)

    # Best 1km pace
    if distance and time and distance[-1] >= 1000:
        best_1k = float('inf')
        for i in range(len(distance)):
            for j in range(i + 1, len(distance)):
                if distance[j] - distance[i] >= 1000:
                    seg_time = time[j] - time[i]
                    seg_dist = distance[j] - distance[i]
                    pace_s_km = seg_time / (seg_dist / 1000)
                    best_1k = min(best_1k, pace_s_km)
                    break
        if best_1k < float('inf'):
            profile.best_1k_pace_secs = round(best_1k, 1)

    # ── Heart Rate metrics ─────────────────────────────

    valid_hr = [(i, h) for i, h in enumerate(heartrate) if h and h > 40]

    if valid_hr:
        all_hr = [h for _, h in valid_hr]
        max_hr_val = max(all_hr)

        # HR drift
        mid = len(valid_hr) // 2
        first_half_hr = [h for _, h in valid_hr[:mid]]
        second_half_hr = [h for _, h in valid_hr[mid:]]
        if first_half_hr and second_half_hr:
            avg_first_hr = statistics.mean(first_half_hr)
            avg_second_hr = statistics.mean(second_half_hr)
            if avg_first_hr > 0:
                profile.hr_drift_pct = round(
                    ((avg_second_hr - avg_first_hr) / avg_first_hr) * 100, 1
                )

        # When max HR occurred (as % of run)
        max_hr_idx = max(valid_hr, key=lambda x: x[1])[0]
        if n > 0:
            profile.hr_max_time_pct = round((max_hr_idx / n) * 100, 1)

        # Time above 90% of run's max HR
        threshold_90 = max_hr_val * 0.9
        above_90 = sum(1 for _, h in valid_hr if h >= threshold_90)
        profile.hr_above_90pct_secs = above_90  # approx seconds (1 point ≈ 1 sec)

        # Time above absolute threshold
        above_thresh = sum(1 for _, h in valid_hr if h >= hr_threshold)
        profile.hr_above_threshold_secs = above_thresh

        # HR CV
        profile.hr_cv = _coefficient_of_variation(all_hr)
        if profile.hr_cv is not None:
            profile.hr_cv = round(profile.hr_cv, 4)

        # HR recovery: drop in last 30 data points
        if len(all_hr) > 60:
            last_30_avg = statistics.mean(all_hr[-30:])
            peak_near_end = max(all_hr[-90:-30]) if len(all_hr) > 90 else max_hr_val
            profile.hr_recovery_30s = round(peak_near_end - last_30_avg, 1)

        # HR zone distribution (5 zones based on max HR)
        zone_bounds = [0.6, 0.7, 0.8, 0.9, 1.0]  # % of max
        zone_counts = [0] * 5
        for _, h in valid_hr:
            pct = h / max_hr_val
            for z in range(4, -1, -1):
                if pct >= zone_bounds[z] * 0.95:  # slight buffer
                    zone_counts[z] += 1
                    break
        total_hr = sum(zone_counts)
        if total_hr > 0:
            profile.hr_zone_pcts = [round((c / total_hr) * 100, 1) for c in zone_counts]

    # ── Cadence metrics ────────────────────────────────

    valid_cad = [c for c in cadence if c is not None]
    moving_cad = [c for c in valid_cad if c > 10]

    if moving_cad:
        # Double if single-leg
        if statistics.mean(moving_cad) < 120:
            moving_cad = [c * 2 for c in moving_cad]

        profile.cadence_avg = round(statistics.mean(moving_cad), 1)
        profile.cadence_cv = _coefficient_of_variation(moving_cad)
        if profile.cadence_cv is not None:
            profile.cadence_cv = round(profile.cadence_cv, 4)

    # Stop detection (basic)
    profile.stop_count, profile.total_stopped_secs = _detect_stops(cadence, time)

    # Stop detection (detailed — for classification)
    max_hr_for_stops = 185.0
    if valid_hr:
        all_hr_vals = [h for _, h in valid_hr]
        max_hr_for_stops = max(all_hr_vals)

    stop_info = _detect_stops_detailed(
        cadence, time, heartrate=heartrate, max_hr=max_hr_for_stops,
    )
    profile.avg_stop_duration_secs = stop_info.avg_duration_secs if stop_info.count > 0 else None
    profile.stop_regularity = stop_info.regularity
    if stop_info.hr_at_onset_pcts:
        profile.hr_at_stop_onset_pct = round(
            statistics.mean(stop_info.hr_at_onset_pcts), 1
        )

    # ── Moving pace CV ────────────────────────────────
    profile.pace_cv_moving = _compute_pace_cv_moving(velocity, time)

    # ── High-intensity bouts ──────────────────────────
    profile.high_intensity_bouts = _count_high_intensity_bouts(
        heartrate, time, max_hr=max_hr_for_stops,
    )

    # ── Intensity factor ──────────────────────────────
    threshold_speed = 3.33  # default 5:00/km
    if activity_data and "threshold_pace" in activity_data:
        threshold_speed = activity_data["threshold_pace"]
    profile.intensity_factor = _compute_intensity_factor(velocity, threshold_speed)

    # ── Elevation metrics ──────────────────────────────

    profile.elevation_profile, profile.climb_score, profile.max_gradient_pct = \
        _elevation_profile(altitude, distance)

    # ── Intensity distribution ─────────────────────────

    profile.intensity_distribution, profile.time_in_easy_pct, profile.time_in_hard_pct = \
        _classify_intensity(velocity, heartrate)

    # ── catch22 shape features ─────────────────────────

    profile.catch22_pace = _compute_catch22(velocity, "pace")
    profile.catch22_hr = _compute_catch22(heartrate, "hr")
    profile.catch22_cadence = _compute_catch22(
        [c for c in cadence if c and c > 10],  # only moving cadence
        "cadence"
    )
    profile.catch22_altitude = _compute_catch22(altitude, "altitude")

    return profile


def profile_summary(p: RunProfile) -> str:
    """One-line summary of a run profile for display."""
    parts = []

    # Pacing character
    if p.negative_split_ratio is not None:
        if p.negative_split_ratio < 0.97:
            parts.append("negative split")
        elif p.negative_split_ratio > 1.05:
            parts.append("positive split (faded)")
        else:
            parts.append("even pacing")

    if p.even_pace_score is not None:
        parts.append(f"consistency {p.even_pace_score:.0f}/100")

    # HR
    if p.hr_drift_pct is not None:
        if abs(p.hr_drift_pct) < 3:
            parts.append("steady HR")
        elif p.hr_drift_pct > 0:
            parts.append(f"HR drifted +{p.hr_drift_pct:.0f}%")

    # Stops
    if p.stop_count > 0:
        parts.append(f"{p.stop_count} stops ({p.total_stopped_secs:.0f}s)")

    # Elevation
    if p.elevation_profile:
        parts.append(p.elevation_profile)

    return " | ".join(parts) if parts else "—"
