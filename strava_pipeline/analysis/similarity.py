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

def classify_run(profile: dict) -> tuple[str, str]:
    """
    Auto-classify a run into a type based on its profile features.

    Returns (type, reasoning).

    Types:
        - easy: low intensity, even pacing, moderate HR
        - tempo: sustained moderate-hard effort, even pacing
        - interval: high pace variance, stops between efforts, long avg stop
        - long: high distance relative to others, even pacing
        - race: fast pace, low CV, high HR, no stops
        - recovery: very slow, very low HR, short
        - fartlek: moderate variance, deliberate pace changes, mixed intensity

    Key distinction: traffic light stops (short, 5-15s each) are NOT the same
    as interval rest stops (long, 30-90s each). Average stop duration matters
    more than stop count for classification.
    """
    pace_cv = profile.get("pace_cv") or 0
    stop_count = profile.get("stop_count") or 0
    total_stopped = profile.get("total_stopped_secs") or 0
    hr_drift = profile.get("hr_drift_pct") or 0
    fade_index = profile.get("fade_index") or 1.0
    neg_split = profile.get("negative_split_ratio") or 1.0
    easy_pct = profile.get("time_in_easy_pct") or 0
    hard_pct = profile.get("time_in_hard_pct") or 0
    even_score = profile.get("even_pace_score") or 50
    intensity = profile.get("intensity_distribution") or ""
    elevation = profile.get("elevation_profile") or ""

    reasons = []

    # Average stop duration is the key discriminator:
    # - Traffic lights / crossings: 5-15s per stop
    # - Interval rest periods: 30-90s per stop
    # - Aid stations / long breaks: 90s+ per stop
    avg_stop_secs = (total_stopped / stop_count) if stop_count > 0 else 0

    # Are the stops "deliberate rests" (intervals) or "incidental" (traffic)?
    has_deliberate_stops = avg_stop_secs >= 25 and stop_count >= 3
    has_incidental_stops = stop_count > 0 and avg_stop_secs < 25

    # Interval detection: high pace variance + deliberate rest stops
    # Must have long average stops (not traffic lights) AND high intensity work
    if pace_cv > 0.15 and has_deliberate_stops and total_stopped > 60:
        reasons.append(f"high pace variation (CV {pace_cv:.2f}), {stop_count} rest stops (avg {avg_stop_secs:.0f}s)")
        return "interval", "; ".join(reasons)

    # Also catch intervals with extreme pace CV even with fewer stops
    if pace_cv > 0.25 and stop_count >= 2 and avg_stop_secs >= 20:
        reasons.append(f"very high pace variation (CV {pace_cv:.2f}), structured rest stops")
        return "interval", "; ".join(reasons)

    # Race: fast, consistent, no/minimal stops, high HR
    if even_score > 70 and stop_count <= 2 and hard_pct and hard_pct > 20:
        reasons.append(f"consistent ({even_score:.0f}/100), {hard_pct:.0f}% hard effort, minimal stops")
        return "race", "; ".join(reasons)

    # Tempo: sustained moderate-hard, relatively even pacing
    if intensity in ("threshold", "polarised") and even_score > 45 and not has_deliberate_stops:
        reasons.append(f"{intensity} intensity, {even_score:.0f}/100 consistency")
        return "tempo", "; ".join(reasons)

    # Easy: mostly easy zone — the primary check is HR intensity, NOT stops/pace CV.
    # Urban runners will have traffic light stops and pace variation that doesn't
    # change the fundamental nature of the run: it's still an easy effort.
    if easy_pct and easy_pct > 60:
        # This is an easy run. Stops from traffic don't change that.
        if even_score > 40 or has_incidental_stops:
            qualifier = ""
            if has_incidental_stops and stop_count >= 5:
                qualifier = f" (urban: {stop_count} brief stops, avg {avg_stop_secs:.0f}s)"
            reasons.append(f"{easy_pct:.0f}% easy zone{qualifier}")
            return "easy", "; ".join(reasons)

    # Recovery: very easy, short distance, very low effort
    # Distinguished from easy by being notably shorter/slower
    if easy_pct and easy_pct > 85 and pace_cv < 0.08:
        reasons.append(f"{easy_pct:.0f}% in easy zone, very smooth pacing")
        return "recovery", "; ".join(reasons)

    # Fartlek: deliberate pace changes but not full interval structure
    # Needs evidence of intentional surges, not just traffic variation
    if 0.12 < pace_cv < 0.25 and has_deliberate_stops and hard_pct and hard_pct > 15:
        reasons.append(f"pace surges (CV {pace_cv:.2f}), {hard_pct:.0f}% hard effort, some rest stops")
        return "fartlek", "; ".join(reasons)

    # Easy catch-all: if mostly easy zone, it's easy regardless of pace variation
    if easy_pct and easy_pct > 50:
        reasons.append(f"{easy_pct:.0f}% easy zone, some variation")
        return "easy", "; ".join(reasons)

    # Default
    reasons.append(f"CV {pace_cv:.2f}, {stop_count} stops (avg {avg_stop_secs:.0f}s), {even_score:.0f}/100 consistency")
    return "mixed", "; ".join(reasons)


def classify_all(profiles: list[dict]) -> list[tuple[str, dict, str]]:
    """Classify all profiles and return (type, profile, reasoning) tuples."""
    results = []
    for p in profiles:
        run_type, reasoning = classify_run(p)
        results.append((run_type, p, reasoning))
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
        run_type, _ = classify_run(p)
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
