-- Daily athlete wellness from Garmin Connect (HRV, sleep, body battery, stress)
-- Run manually in Supabase SQL editor

CREATE TABLE IF NOT EXISTS athlete_wellness (
    id               BIGSERIAL PRIMARY KEY,
    athlete_id       BIGINT        NOT NULL,
    date             DATE          NOT NULL,
    hrv_weekly_avg   INTEGER,
    hrv_last_night   INTEGER,
    hrv_status       TEXT,         -- BALANCED, UNBALANCED, POOR, LOW
    sleep_duration_s INTEGER,
    sleep_score      INTEGER,      -- 0-100
    body_battery_high INTEGER,     -- Garmin body battery peak (0-100)
    body_battery_low  INTEGER,
    stress_avg       INTEGER,      -- 0-100
    resting_hr       INTEGER,
    created_at       TIMESTAMPTZ   DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   DEFAULT NOW(),
    UNIQUE(athlete_id, date)
);

CREATE INDEX IF NOT EXISTS idx_wellness_athlete_date
    ON athlete_wellness(athlete_id, date DESC);
