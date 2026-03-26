-- Run profiles: pre-computed fingerprint for every run
-- Enables instant queries across entire history without touching raw streams

CREATE TABLE IF NOT EXISTS run_profiles (
    activity_id TEXT PRIMARY KEY,
    athlete_id TEXT NOT NULL,
    computed_at TIMESTAMPTZ DEFAULT NOW(),

    -- Pacing
    negative_split_ratio REAL,        -- <1 = negative split
    pace_cv REAL,                     -- coefficient of variation (lower = more consistent)
    fade_index REAL,                  -- last 25% vs first 25% (>1 = faded)
    variability_index REAL,           -- normalized pace / avg pace
    best_1k_pace_secs REAL,           -- fastest 1km segment
    even_pace_score REAL,             -- 0-100 pacing consistency
    km_splits JSONB,                  -- per-km paces in s/km

    -- Heart Rate
    hr_drift_pct REAL,                -- 2nd half vs 1st half avg HR
    hr_max_time_pct REAL,             -- when max HR occurred (0-100% of run)
    hr_above_90pct_secs REAL,         -- seconds above 90% of max HR
    hr_above_threshold_secs REAL,     -- seconds above threshold
    hr_zone_pcts JSONB,              -- [z1%, z2%, z3%, z4%, z5%]
    hr_cv REAL,                       -- HR coefficient of variation
    hr_recovery_30s REAL,             -- HR drop in last 30s

    -- Cadence
    cadence_cv REAL,
    cadence_avg REAL,
    stop_count INTEGER DEFAULT 0,
    total_stopped_secs REAL DEFAULT 0,

    -- Elevation
    elevation_profile TEXT,           -- flat/rolling/hilly/mountainous
    climb_score REAL,                 -- m ascent per km
    max_gradient_pct REAL,

    -- Intensity
    intensity_distribution TEXT,      -- polarised/threshold/pyramidal/junk/mixed
    time_in_easy_pct REAL,
    time_in_hard_pct REAL,

    -- catch22 shape features (22 features per stream)
    catch22_pace JSONB,
    catch22_hr JSONB,
    catch22_cadence JSONB,
    catch22_altitude JSONB
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_run_profiles_athlete ON run_profiles(athlete_id);
CREATE INDEX IF NOT EXISTS idx_run_profiles_stops ON run_profiles(stop_count) WHERE stop_count > 0;
CREATE INDEX IF NOT EXISTS idx_run_profiles_negative_split ON run_profiles(negative_split_ratio);
CREATE INDEX IF NOT EXISTS idx_run_profiles_fade ON run_profiles(fade_index);
CREATE INDEX IF NOT EXISTS idx_run_profiles_hr_drift ON run_profiles(hr_drift_pct);
CREATE INDEX IF NOT EXISTS idx_run_profiles_elevation ON run_profiles(elevation_profile);
CREATE INDEX IF NOT EXISTS idx_run_profiles_pace_cv ON run_profiles(pace_cv);
