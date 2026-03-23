"""
Strava API rate limiter.

Limits:
  - 100 requests per 15-minute window
  - 1000 requests per day

State is persisted to a JSON file so it survives process restarts.
"""

import json
import time
from pathlib import Path
from threading import Lock

STATE_FILE = Path("rate_limit_state.json")
LOCK = Lock()

WINDOW_15MIN = 15 * 60     # seconds
WINDOW_DAY = 24 * 60 * 60  # seconds
LIMIT_15MIN = 95            # leave 5 in reserve
LIMIT_DAY = 950             # leave 50 in reserve


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"requests": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def _prune(requests: list[float], now: float) -> list[float]:
    cutoff = now - WINDOW_DAY
    return [t for t in requests if t > cutoff]


def throttle() -> None:
    """
    Block until a request can be safely made within both rate limits.
    Call this before every Strava API request.
    """
    with LOCK:
        while True:
            state = _load_state()
            now = time.time()
            requests = _prune(state["requests"], now)

            window_15min_start = now - WINDOW_15MIN
            recent = [t for t in requests if t > window_15min_start]

            if len(recent) >= LIMIT_15MIN:
                oldest_in_window = min(recent)
                sleep_time = (oldest_in_window + WINDOW_15MIN) - now + 0.5
                print(f"[rate_limiter] 15-min limit reached. Sleeping {sleep_time:.0f}s...")
                time.sleep(sleep_time)
                continue

            if len(requests) >= LIMIT_DAY:
                oldest = min(requests)
                sleep_time = (oldest + WINDOW_DAY) - now + 0.5
                print(f"[rate_limiter] Daily limit reached. Sleeping {sleep_time:.0f}s...")
                time.sleep(sleep_time)
                continue

            requests.append(now)
            _save_state({"requests": requests})
            return


def remaining() -> dict:
    """Return current remaining request counts for both windows."""
    now = time.time()
    state = _load_state()
    requests = _prune(state["requests"], now)
    recent = [t for t in requests if t > now - WINDOW_15MIN]
    return {
        "15min_used": len(recent),
        "15min_remaining": LIMIT_15MIN - len(recent),
        "day_used": len(requests),
        "day_remaining": LIMIT_DAY - len(requests),
    }
