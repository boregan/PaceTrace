# PaceTrace

An MCP server that gives Claude deep access to your running data. Connects to intervals.icu for activities, fitness metrics, and planned workouts — augmented with weather data, pre-computed run fingerprints, and sports-science analysis models. Ask Claude anything about your training: how fit you are, where your weaknesses are, what you could race, whether you're at risk of injury.

Deployed at `pacetrace.fly.dev` (Fly.io, London region).

---

## Tools

### Activity Data

**`get_activity`** `activity_id`
Full details for a single run: pace, Grade Adjusted Pace, effort-adjusted pace (normalised for heat/humidity/elevation/fatigue), weather conditions, HR zones, auto-detected intervals, efficiency factor, aerobic decoupling, training load, gear, and elevation.

**`get_recent`** `days` `limit`
Recent runs with key metrics — pace, distance, HR, training load, form (TSB), and shoes.

**`get_week`** `date`
Weekly summary: total distance, time, runs, average pace/HR, daily breakdown, and CTL/ATL/TSB trend for the week.

**`search_activities`** `query` `date_from` `date_to` `limit`
Search by name, tag, or date range. Find specific workouts, races, or training blocks.

**`get_intervals`** `activity_id`
Auto-detected intervals within a run: warmup, work intervals, recovery, cooldown. Each with pace, GAP, HR, cadence, stride, decoupling, and intensity.

**`get_streams`** `activity_id` `types`
Second-by-second time-series data: HR, pace, GAP, cadence, altitude, GPS. Use for splits, drift patterns, and pacing strategy analysis.

---

### Fitness & Form

**`get_fitness`** `days`
CTL (fitness), ATL (fatigue), TSB (form), ramp rate, and daily training load history. Shows whether you're building, recovering, or overreaching.

**`get_athlete_profile`**
Running zones (pace, HR, power), thresholds, FTP, LTHR, threshold pace, GAP model, current CTL/ATL/TSB, and shoes.

**`get_day_readiness`** `date`
Today's snapshot: current form, recent load, HRV, sleep, resting HR, and subjective scores.

**`get_training_load`** `weeks`
Weekly load analysis — load per week, intensity distribution, acute:chronic workload ratio, and polarisation index.

**`get_training_phase`** `weeks`
Auto-detect current training phase: BASE, BUILD, PEAK, TAPER, RECOVERY, or MAINTENANCE. Analyses volume trends, intensity distribution, and CTL/ATL/TSB to show where you are in the training cycle.

**`get_wellness`** `days`
Daily wellness from Garmin: HRV, resting HR, sleep duration/score, weight, readiness, stress, fatigue, and mood.

**`get_planned_workouts`** `days_ahead`
Upcoming planned sessions, races, and goals from the intervals.icu calendar.

---

### Performance & Racing

**`get_pace_curves`** `days` `gap`
Best pace efforts across all runs — fastest times at every distance from 400m to marathon. Optionally gradient-adjusted.

**`get_pace_progression`** `days` `distances`
How your pace at key distances (1km, 5km, 10km, half marathon) has changed over time. Spots fitness gains and plateaus.

**`predict_race`** `seed_distance` `seed_time_secs` `auto`
Predict finish times at all standard distances using three models (VDOT/Daniels, Riegel, Cameron). Also computes marathon shape — whether your training volume and long run distance support the target race.

**`get_critical_speed`**
Compute Critical Speed (CS) and D' — the FTP equivalent for runners. CS is the pace sustainable for ~30–60 minutes; D' is your anaerobic distance reserve above it. Fitted from best efforts at multiple distances.

**`get_effort_adjusted`** `activity_id`
Effort-adjusted pace for a run: normalises for weather (temperature, humidity, dew point), elevation, and fatigue to show what it equates to in ideal conditions (10°C, flat, fresh).

**`compare_runs`** `id1` `id2`
Side-by-side comparison: pace, GAP, HR, cadence, efficiency, aerobic decoupling, training load, elevation, form at the time, and gear.

---

### Pattern Recognition

**`analyse_runs`** `days` `query`
Analyse multiple runs across a date range — finds runs with stops, fastest, longest, hardest, hilliest, or highest HR. Uses activity-level metrics so it's efficient across large date ranges.

**`query_run_profiles`** `query` `limit`
Query pre-computed fingerprints across your entire history. Each run has been profiled for pacing pattern (negative/positive splits, consistency), HR drift, stops, elevation profile, and intensity distribution. Instant results across hundreds of runs.

Options: `negative_splits`, `positive_splits`, `most_consistent`, `least_consistent`, `stops`, `hr_drift`, `steady_hr`, `hilly`, `flat`, `fastest_1k`, `all`

**`find_similar_runs`** `activity_id` `stream` `limit`
Runs with similar patterns to a given run. Uses catch22 time-series fingerprints to match pacing shape, HR profile, cadence, and elevation across your full history. Match on a specific stream or overall similarity.

**`classify_my_runs`**
Auto-classify all profiled runs into types (easy, tempo, interval, long, race, recovery, fartlek). Shows training distribution percentages and flags anomaly runs.

**`find_similar_intervals`** `min_duration_secs` `max_duration_secs` `min_intensity` `max_intensity`
Find runs containing intervals of a specific duration and intensity. Compare interval quality across training blocks.

---

### Injury & Risk

**`get_injury_risk`** `days`
Evidence-based injury risk assessment. Analyses ACWR (0.8–1.3 safe zone), training monotony, session spikes, week-over-week ramp rate, and consecutive hard days. Returns a risk score (0–100) with specific flags and recommendations.

---

### Gear & Routes

**`get_shoes`**
All shoes with total distance, activity count, and retirement reminders.

**`get_routes`**
Recurring routes with activity counts, last run date, and most-used routes.
