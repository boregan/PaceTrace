"""
Run similarity search and auto-classification using catch22 fingerprints.

Uses the pre-computed catch22 shape features to:
1. Find runs with similar patterns (pacing, HR, cadence, elevation profiles)
2. Auto-classify runs into types (easy, tempo, interval, long, race)
3. Detect "anomaly" runs that don't match any typical pattern

Similarity is computed via cosine similarity on the catch22 feature vectors.
Classification uses domain metrics + catch22 cluster characteristics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ── Vector Operations ──────────────────────────────────────

def _extract_vector(profile: dict, streams: list[str] | None = None) -> list[float]:
    """Extract a flat feature vector from a profile's catch22 data.

    Args:
        profile: DB row dict with catch22_pace, catch22_hr, etc.
        streams: which streams to include (default: all four)
    """
    if streams is None:
        streams = ["pace", "hr", "cadence", "altitude"]

    vector = []
    for stream in streams:
        c22 = profile.get(f"catch22_{stream}", {}) or {}
        if isinstance(c22, dict):
            # Sort by key name for consistent ordering
            for key in sorted(c22.keys()):
                val = c22[key]
                if val is not None and not (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                    vector.append(float(val))
                else:
                    vector.append(0.0)
        # If empty dict, pad with zeros
        if not c22:
            vector.extend([0.0] * 22)

    return vector


def _normalize(vector: list[float]) -> list[float]:
    """L2-normalize a vector."""
    mag = math.sqrt(sum(v * v for v in vector))
    if mag == 0:
        return vector
    return [v / mag for v in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0-1 (1 = identical)."""
    if len(a) != len(b) or not a:
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return max(0.0, min(1.0, dot / (mag_a * mag_b)))


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """Euclidean distance between two normalized vectors."""
    if len(a) != len(b):
        return float('inf')
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ── Similarity Search ─────────────────────────────────────

@dataclass
class SimilarityResult:
    activity_id: str
    similarity: float  # 0-1, higher = more similar
    match_type: str    # what aspect matched best

    def __str__(self):
        return f"{self.activity_id}: {self.similarity:.1%} similar ({self.match_type})"


def find_similar(
    target: dict,
    candidates: list[dict],
    top_n: int = 10,
    streams: list[str] | None = None,
) -> list[SimilarityResult]:
    """
    Find the most similar runs to a target run.

    Args:
        target: profile dict for the run to match against
        candidates: list of profile dicts to search through
        top_n: number of results to return
        streams: which catch22 streams to compare (default: all)

    Returns:
        List of SimilarityResult sorted by similarity (highest first)
    """
    target_vec = _normalize(_extract_vector(target, streams))

    if not any(v != 0 for v in target_vec):
        return []

    results = []
    for cand in candidates:
        if cand["activity_id"] == target["activity_id"]:
            continue  # Skip self

        cand_vec = _normalize(_extract_vector(cand, streams))
        if not any(v != 0 for v in cand_vec):
            continue

        # Overall similarity
        overall_sim = cosine_similarity(target_vec, cand_vec)

        # Find which stream matches best
        best_stream = "overall"
        best_stream_sim = 0.0
        for stream in (streams or ["pace", "hr", "cadence", "altitude"]):
            t_stream = _normalize(_extract_vector(target, [stream]))
            c_stream = _normalize(_extract_vector(cand, [stream]))
            s = cosine_similarity(t_stream, c_stream)
            if s > best_stream_sim:
                best_stream_sim = s
                best_stream = stream

        match_desc = {
            "pace": "similar pacing pattern",
            "hr": "similar HR profile",
            "cadence": "similar cadence pattern",
            "altitude": "similar terrain",
            "overall": "similar overall profile",
        }.get(best_stream, best_stream)

        results.append(SimilarityResult(
            activity_id=cand["activity_id"],
            similarity=overall_sim,
            match_type=match_desc,
        ))

    results.sort(key=lambda r: r.similarity, reverse=True)
    return results[:top_n]


def find_similar_by_stream(
    target: dict,
    candidates: list[dict],
    stream: str,
    top_n: int = 10,
) -> list[SimilarityResult]:
    """Find runs with similar patterns for a specific stream only."""
    return find_similar(target, candidates, top_n, streams=[stream])


# ── Auto-Classification ───────────────────────────────────

def classify_run(profile: dict) -> tuple[str, str, float]:
    """
    Evidence-based run classification using sports science thresholds.

    Returns (type, reasoning, confidence).

    Classification hierarchy (based on Seiler, Coggan, Firstbeat research):
    1. STRUCTURE first — detect intervals by pace CV + high-intensity bouts + stop context
    2. INTENSITY second — use HR zone distribution (%easy, %hard) and IF where available
    3. HR zones are the primary intensity classifier, not pace or stops
    4. Stops only matter for INTERVAL detection, not easy/tempo/race

    Stop classification (BJSM 2025, GPS stop detection research):
    - Traffic lights: avg <20s, HR at onset <78% HRmax, irregular spacing
    - Interval rests: avg >25s, HR at onset >80% HRmax, regular spacing

    Intensity Factor thresholds (Coggan/TrainingPeaks, validated):
    - IF < 0.75: recovery  |  0.75-0.85: easy  |  0.85-0.95: tempo
    - 0.95-1.05: threshold  |  >1.05: VO2max+

    HR zone thresholds (Seiler 3-zone, %HRmax):
    - Z1 (easy): <78%  |  Z2 (moderate): 78-88%  |  Z3 (hard): >88%
    """
    # ── Extract all available features ──────────────────────
    # Use pace_cv_moving (stops excluded) if available, fall back to raw pace_cv
    pace_cv = profile.get("pace_cv_moving") or profile.get("pace_cv") or 0
    stop_count = profile.get("stop_count") or 0
    total_stopped = profile.get("total_stopped_secs") or 0
    easy_pct = profile.get("time_in_easy_pct") or 0
    hard_pct = profile.get("time_in_hard_pct") or 0
    even_score = profile.get("even_pace_score") or 50
    intensity_dist = profile.get("intensity_distribution") or ""
    hr_drift = profile.get("hr_drift_pct") or 0

    # New enhanced metrics (may not exist on older profiles)
    avg_stop_dur = profile.get("avg_stop_duration_secs")
    stop_reg = profile.get("stop_regularity")
    hr_at_stops = profile.get("hr_at_stop_onset_pct")
    intensity_factor = profile.get("intensity_factor")
    hi_bouts = profile.get("high_intensity_bouts") or 0

    # Compute avg stop duration if not pre-computed
    if avg_stop_dur is None and stop_count > 0:
        avg_stop_dur = total_stopped / stop_count

    reasons = []
    confidence = 0.5  # baseline

    # ── STEP 1: Detect INTERVALS by structure ───────────────
    # Intervals need: high pace variation + evidence of structured work/rest
    # Key discriminator: HR was HIGH before stops (interval rest) vs LOW (traffic)
    is_interval = False

    if hi_bouts >= 3 and pace_cv > 0.12:
        # Multiple high-intensity bouts + variable pacing = intervals
        is_interval = True
        reasons.append(f"{hi_bouts} high-intensity bouts, pace CV {pace_cv:.2f}")
        confidence = 0.85

    elif pace_cv > 0.15 and stop_count >= 3:
        # High pace CV + stops — but are they interval rests or traffic?
        if hr_at_stops is not None and hr_at_stops > 80:
            # HR was high when stops began = recovering from hard effort
            is_interval = True
            reasons.append(f"pace CV {pace_cv:.2f}, HR {hr_at_stops:.0f}% at stop onset")
            confidence = 0.80
        elif avg_stop_dur is not None and avg_stop_dur >= 25:
            # Long deliberate stops (>25s avg) = structured rest
            is_interval = True
            reasons.append(f"pace CV {pace_cv:.2f}, {stop_count} rests avg {avg_stop_dur:.0f}s")
            confidence = 0.75
        elif hard_pct and hard_pct > 25:
            # High hard %, high CV = intervals even without stop context
            is_interval = True
            reasons.append(f"pace CV {pace_cv:.2f}, {hard_pct:.0f}% hard effort")
            confidence = 0.70

    if is_interval:
        return "interval", "; ".join(reasons), confidence

    # ── STEP 2: Classify steady-state runs by INTENSITY ─────
    # Primary signal: HR zone distribution (time_in_easy_pct, time_in_hard_pct)
    # Secondary signal: Intensity Factor (IF) where available

    # IF-based classification (most validated single metric)
    if intensity_factor is not None and intensity_factor > 0:
        if intensity_factor >= 0.95:
            reasons.append(f"IF {intensity_factor:.2f} (threshold+)")
            if even_score > 65 and stop_count <= 2:
                reasons.append(f"consistent pacing, minimal stops")
                return "race", "; ".join(reasons), 0.80
            return "threshold", "; ".join(reasons), 0.75
        elif intensity_factor >= 0.85:
            reasons.append(f"IF {intensity_factor:.2f} (tempo zone)")
            return "tempo", "; ".join(reasons), 0.80
        elif intensity_factor >= 0.75:
            reasons.append(f"IF {intensity_factor:.2f} (endurance zone)")
            # Fall through to HR-based checks for easy vs long
        elif intensity_factor < 0.65:
            reasons.append(f"IF {intensity_factor:.2f} (very easy)")
            return "recovery", "; ".join(reasons), 0.80

    # HR zone-based classification (Seiler model)
    # This is the CORE classifier — HR tells you how hard the effort was,
    # regardless of stops, pace variation, or GPS noise.

    # Race: high HR, consistent pacing, no/minimal stops
    if hard_pct and hard_pct > 30 and even_score > 65 and stop_count <= 2:
        reasons.append(f"{hard_pct:.0f}% hard effort, {even_score:.0f}/100 consistency, {stop_count} stops")
        return "race", "; ".join(reasons), 0.80

    # Tempo: sustained moderate-to-hard, relatively even pacing
    if hard_pct and hard_pct > 20 and pace_cv < 0.12:
        reasons.append(f"{hard_pct:.0f}% hard effort, steady pacing (CV {pace_cv:.2f})")
        return "tempo", "; ".join(reasons), 0.75

    # Threshold: high hard %, but not quite a race (less consistent or some stops)
    if hard_pct and hard_pct > 30:
        reasons.append(f"{hard_pct:.0f}% hard effort")
        return "threshold", "; ".join(reasons), 0.65

    # ── STEP 3: Easy / Recovery (the important one for urban runners) ──
    # HR is the ONLY signal that matters here. Stops and pace CV from
    # traffic lights, hills, wind etc. do NOT change the physiological
    # nature of the run. If your HR was in easy zones, it was an easy run.

    if easy_pct and easy_pct > 55:
        # This is an easy-intensity run. Period.
        # Add context about urban conditions but don't change the classification.
        qualifier_parts = []
        if stop_count > 0 and avg_stop_dur is not None and avg_stop_dur < 20:
            qualifier_parts.append(f"{stop_count} brief stops")
        if pace_cv > 0.10:
            qualifier_parts.append(f"pace variation from terrain/traffic")

        base_reason = f"{easy_pct:.0f}% in easy HR zones"
        if qualifier_parts:
            base_reason += f" ({', '.join(qualifier_parts)})"
        reasons.append(base_reason)

        # Distinguish recovery from easy: recovery = very easy + very smooth
        if easy_pct > 85 and pace_cv < 0.06 and (not hard_pct or hard_pct < 5):
            return "recovery", "; ".join(reasons), 0.75

        return "easy", "; ".join(reasons), 0.80

    # Fartlek: moderate intensity variation without structured intervals
    # Must have BOTH easy and hard portions + deliberate pace changes
    if hard_pct and 15 < hard_pct < 40 and easy_pct and easy_pct > 30:
        if pace_cv > 0.10:
            reasons.append(f"mixed intensity ({easy_pct:.0f}% easy, {hard_pct:.0f}% hard), variable pacing")
            return "fartlek", "; ".join(reasons), 0.65

    # ── STEP 4: Fallback ────────────────────────────────────
    # If we got here, signals are ambiguous
    parts = [f"CV {pace_cv:.2f}"]
    if easy_pct:
        parts.append(f"{easy_pct:.0f}% easy")
    if hard_pct:
        parts.append(f"{hard_pct:.0f}% hard")
    if stop_count:
        parts.append(f"{stop_count} stops")
    reasons.append(", ".join(parts))
    return "mixed", "; ".join(reasons), 0.30


def classify_all(profiles: list[dict]) -> list[tuple[str, dict, str, float]]:
    """Classify all profiles and return (type, profile, reasoning, confidence) tuples."""
    results = []
    for p in profiles:
        result = classify_run(p)
        if len(result) == 3:
            run_type, reasoning, confidence = result
        else:
            # Backward compat with old 2-tuple return
            run_type, reasoning = result[0], result[1]
            confidence = 0.5
        results.append((run_type, p, reasoning, confidence))
    return results


# ── Cluster Analysis ──────────────────────────────────────

def compute_cluster_centroids(profiles: list[dict]) -> dict[str, list[float]]:
    """Compute average catch22 vector per run type for the athlete's runs.

    This creates a personal "fingerprint" for each run type — what
    YOUR easy runs look like vs YOUR tempos vs YOUR intervals.
    """
    from collections import defaultdict

    type_vectors: dict[str, list[list[float]]] = defaultdict(list)

    for p in profiles:
        run_type = classify_run(p)[0]
        vec = _extract_vector(p)
        if any(v != 0 for v in vec):
            type_vectors[run_type].append(vec)

    centroids = {}
    for run_type, vectors in type_vectors.items():
        if not vectors:
            continue
        n = len(vectors)
        dim = len(vectors[0])
        centroid = [sum(vectors[i][d] for i in range(n)) / n for d in range(dim)]
        centroids[run_type] = centroid

    return centroids


def detect_anomalies(
    profiles: list[dict],
    threshold: float = 0.3,
) -> list[tuple[dict, str]]:
    """Find runs that don't match any typical pattern for this athlete.

    These are interesting — might be breakthroughs, off days, or unique sessions.

    Returns list of (profile, reason) for anomalous runs.
    """
    centroids = compute_cluster_centroids(profiles)
    if not centroids:
        return []

    anomalies = []
    for p in profiles:
        vec = _normalize(_extract_vector(p))
        if not any(v != 0 for v in vec):
            continue

        # Find best matching centroid
        best_sim = 0.0
        best_type = ""
        for run_type, centroid in centroids.items():
            sim = cosine_similarity(vec, _normalize(centroid))
            if sim > best_sim:
                best_sim = sim
                best_type = run_type

        if best_sim < threshold:
            anomalies.append((
                p,
                f"only {best_sim:.0%} similar to nearest type ({best_type}) — unique session"
            ))

    return anomalies
