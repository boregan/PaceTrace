"""
Intervals.icu async API client for PaceTrace v2.

Wraps the intervals.icu REST API with typed responses.
Uses httpx for async HTTP, Basic Auth with API key.
"""

import httpx
from datetime import date, datetime, timedelta
from typing import Any, Optional


BASE_URL = "https://intervals.icu/api/v1"


class ICUClient:
    """Async context-manager client for the intervals.icu API."""

    def __init__(self, api_key: str, athlete_id: str = "0"):
        self.api_key = api_key
        self.athlete_id = athlete_id
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            auth=httpx.BasicAuth(username="API_KEY", password=self.api_key),
            timeout=30.0,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    # ── helpers ────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _put(self, path: str, json: dict | None = None) -> Any:
        resp = await self._client.put(path, json=json)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def _post(self, path: str, json: dict | None = None, data: dict | None = None) -> Any:
        resp = await self._client.post(path, json=json, data=data)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def _delete(self, path: str) -> None:
        resp = await self._client.delete(path)
        resp.raise_for_status()

    def _ath(self) -> str:
        return f"/athlete/{self.athlete_id}"

    # ── athlete / profile ──────────────────────────────────

    async def get_athlete(self) -> dict:
        """Full athlete profile with sport settings, zones, shoes."""
        return await self._get(self._ath())

    async def get_sport_settings(self, sport: str = "Run") -> dict:
        """Running-specific thresholds, zones, GAP model."""
        return await self._get(f"{self._ath()}/sport-settings/{sport}")

    # ── activities ─────────────────────────────────────────

    async def list_activities(
        self,
        oldest: str | date | None = None,
        newest: str | date | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """List activities in date range (newest first)."""
        params = {}
        if oldest:
            params["oldest"] = str(oldest)
        else:
            params["oldest"] = str(date.today() - timedelta(days=90))
        if newest:
            params["newest"] = str(newest)
        if limit:
            params["limit"] = limit
        return await self._get(f"{self._ath()}/activities", params)

    async def get_activity(self, activity_id: str, intervals: bool = True) -> dict:
        """Single activity — set intervals=True for auto-detected interval breakdown."""
        params = {"intervals": "true"} if intervals else {}
        return await self._get(f"/activity/{activity_id}", params)

    async def search_activities(self, query: str, limit: int = 20) -> list[dict]:
        """Search activities by name or tag."""
        return await self._get(
            f"{self._ath()}/activities/search",
            {"q": query, "limit": limit},
        )

    async def search_intervals(
        self,
        min_secs: int,
        max_secs: int,
        min_intensity: int = 80,
        max_intensity: int = 120,
        sport_type: str = "Run",
        limit: int = 20,
    ) -> list[dict]:
        """Find activities with matching interval durations/intensities."""
        return await self._get(
            f"{self._ath()}/activities/interval-search",
            {
                "minSecs": min_secs,
                "maxSecs": max_secs,
                "minIntensity": min_intensity,
                "maxIntensity": max_intensity,
                "type": "PACE",
                "limit": limit,
            },
        )

    # ── streams ────────────────────────────────────────────

    async def get_streams(
        self,
        activity_id: str,
        types: list[str] | None = None,
    ) -> list[dict]:
        """Second-by-second time-series data for an activity.

        Available types: heartrate, watts, cadence, pace, gap, altitude,
        latlng, distance, time, temp, velocity_smooth, respiration, smo2, dfa_a1
        """
        params = {}
        if types:
            params["types"] = ",".join(types)
        return await self._get(f"/activity/{activity_id}/streams.json", params)

    # ── intervals ──────────────────────────────────────────

    async def get_intervals(self, activity_id: str) -> dict:
        """Auto-detected intervals with per-interval metrics."""
        return await self._get(f"/activity/{activity_id}/intervals")

    # ── best efforts / curves ──────────────────────────────

    async def get_activity_pace_curve(
        self, activity_id: str, gap: bool = True
    ) -> dict:
        """Best pace at each distance for an activity."""
        return await self._get(
            f"/activity/{activity_id}/pace-curve.json",
            {"gap": "true" if gap else "false"},
        )

    async def get_activity_hr_curve(self, activity_id: str) -> dict:
        """Best sustained HR at each duration."""
        return await self._get(f"/activity/{activity_id}/hr-curve.json")

    async def get_athlete_pace_curves(
        self,
        sport: str = "Run",
        gap: bool = True,
        days_back: int = 365,
    ) -> dict:
        """All-time / period best pace curves across all runs."""
        newest = date.today()
        return await self._get(
            f"{self._ath()}/pace-curves.json",
            {
                "type": sport,
                "gap": "true" if gap else "false",
                "newest": str(newest),
                "curves": f"all:{days_back}d",
            },
        )

    async def get_athlete_power_curves(
        self,
        sport: str = "Run",
        days_back: int = 365,
    ) -> dict:
        """All-time / period best power curves (if running power available)."""
        newest = date.today()
        return await self._get(
            f"{self._ath()}/power-curves.json",
            {
                "type": sport,
                "newest": str(newest),
                "curves": f"all:{days_back}d",
            },
        )

    async def get_activity_pace_curves_over_time(
        self,
        distances_m: list[int] | None = None,
        oldest: str | None = None,
        newest: str | None = None,
        sport: str = "Run",
        gap: bool = True,
    ) -> list[dict]:
        """Per-activity best pace at key distances — track progression."""
        if distances_m is None:
            distances_m = [1000, 5000, 10000, 21097, 42195]
        if oldest is None:
            oldest = str(date.today() - timedelta(days=365))
        if newest is None:
            newest = str(date.today())
        return await self._get(
            f"{self._ath()}/activity-pace-curves.json",
            {
                "oldest": oldest,
                "newest": newest,
                "distances": ",".join(str(d) for d in distances_m),
                "type": sport,
                "gap": "true" if gap else "false",
            },
        )

    async def get_best_efforts(
        self,
        activity_id: str,
        stream: str = "heartrate",
        duration: int | None = None,
        distance: float | None = None,
        count: int = 5,
    ) -> list[dict]:
        """Find N best efforts in an activity for a stream/duration."""
        params: dict = {"stream": stream, "count": count}
        if duration:
            params["duration"] = duration
        if distance:
            params["distance"] = distance
        return await self._get(f"/activity/{activity_id}/best-efforts", params)

    # ── wellness / fitness ─────────────────────────────────

    async def get_wellness(
        self,
        oldest: str | date | None = None,
        newest: str | date | None = None,
    ) -> list[dict]:
        """Daily wellness: CTL, ATL, HRV, sleep, resting HR, weight, readiness."""
        params = {}
        if oldest:
            params["oldest"] = str(oldest)
        else:
            params["oldest"] = str(date.today() - timedelta(days=90))
        if newest:
            params["newest"] = str(newest)
        else:
            params["newest"] = str(date.today())
        return await self._get(f"{self._ath()}/wellness", params)

    async def get_wellness_for_date(self, d: str | date) -> dict:
        """Single day's wellness."""
        return await self._get(f"{self._ath()}/wellness/{d}")

    async def update_wellness(self, d: str | date, data: dict) -> dict:
        """Update a day's wellness (only provided fields changed)."""
        return await self._put(f"{self._ath()}/wellness/{d}", json=data)

    # ── histograms ─────────────────────────────────────────

    async def get_pace_histogram(self, activity_id: str) -> dict:
        return await self._get(f"/activity/{activity_id}/pace-histogram")

    async def get_gap_histogram(self, activity_id: str) -> dict:
        return await self._get(f"/activity/{activity_id}/gap-histogram")

    async def get_hr_histogram(self, activity_id: str, bucket_size: int = 5) -> dict:
        return await self._get(
            f"/activity/{activity_id}/hr-histogram",
            {"bucketSize": bucket_size},
        )

    # ── power vs HR (cardiac drift analysis) ───────────────

    async def get_power_vs_hr(self, activity_id: str) -> dict:
        return await self._get(f"/activity/{activity_id}/power-vs-hr.json")

    # ── gear / shoes ───────────────────────────────────────

    async def get_gear(self) -> list[dict]:
        """All gear with distance, time, activity count, reminders."""
        return await self._get(f"{self._ath()}/gear.json")

    # ── fitness model events ───────────────────────────────

    async def get_fitness_model_events(self) -> list[dict]:
        """Fitness model configuration (time constants, manual CTL/ATL sets)."""
        return await self._get(f"{self._ath()}/fitness-model-events")

    # ── calendar / planned workouts ────────────────────────

    async def get_events(
        self,
        oldest: str | date | None = None,
        newest: str | date | None = None,
    ) -> list[dict]:
        """Planned workouts, races, goals, notes."""
        params = {}
        if oldest:
            params["oldest"] = str(oldest)
        if newest:
            params["newest"] = str(newest)
        return await self._get(f"{self._ath()}/events", params)
