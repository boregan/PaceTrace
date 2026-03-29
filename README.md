# PaceTrace

An MCP server that gives Claude deep access to your running data. Connects to intervals.icu for activities, fitness metrics, and planned workouts — augmented with weather data, pre-computed run fingerprints, and sports-science analysis models. Ask Claude anything about your training: how fit you are, where your weaknesses are, what you could race, whether you're at risk of injury.

Deployed at `pacetrace.fly.dev` (Fly.io, London region).

---

## Setup

### 1. Prerequisites

- Python 3.12+
- [intervals.icu](https://intervals.icu) account with API key
- [Supabase](https://supabase.com) project (free tier is fine)
- [flyctl](https://fly.io/docs/hands-on/install-flyctl/) for deployment

### 2. Environment variables

```env
# intervals.icu
INTERVALS_ICU_API_KEY=your_api_key
INTERVALS_ICU_ATHLETE_ID=iXXXXXX

# Supabase (for run profiles + wellness data)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key

# Athlete config
PACETRACE_USER=ben
PACETRACE_MAX_HR=185
PACETRACE_REST_HR=55

# Optional: Garmin wellness sync
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=your_password

# Optional: API auth for web endpoints
API_SECRET=pick_a_random_string
```

### 3. Supabase migrations

Run all migrations in order via the Supabase SQL editor or CLI:

```
supabase/migrations/001_create_activities.sql
supabase/migrations/002_create_streams.sql
supabase/migrations/003_create_athlete_tokens.sql
supabase/migrations/004_add_enrichment_columns.sql
supabase/migrations/005_create_wellness.sql
supabase/migrations/007_create_pacetrace_users.sql
supabase/migrations/20260326_run_profiles.sql
supabase/migrations/20260327_improve_profiles.sql
```

### 4. Compute run profiles

Run profiles power the pattern recognition tools (`query_run_profiles`, `find_similar_runs`, `classify_my_runs`). They're computed locally and stored in Supabase:

```bash
# Process all runs
python scripts/compute_profiles.py --user ben

# Incremental update (last 30 days only)
python scripts/compute_profiles.py --user ben --days 30

# Force reprocess everything
python scripts/compute_profiles.py --user ben --force
```

### 5. Garmin wellness sync (optional)

```bash
python scripts/sync_garmin.py --user ben
```

### 6. Deploy to Fly.io

```bash
flyctl deploy
```

Set secrets in Fly:

```bash
flyctl secrets set INTERVALS_ICU_API_KEY=... INTERVALS_ICU_ATHLETE_ID=... \
  SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
  PACETRACE_USER=ben PACETRACE_MAX_HR=185
```

### 7. Connect to Claude

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pacetrace": {
      "command": "/path/to/.venv_mcp/bin/python",
      "args": ["/path/to/strava-pipeline/mcp_server_v2.py"],
      "env": {
        "PACETRACE_USER": "ben",
        "PACETRACE_MAX_HR": "185"
      }
    }
  }
}
```

**Claude.ai** — add the SSE endpoint: `https://pacetrace.fly.dev/sse`

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
