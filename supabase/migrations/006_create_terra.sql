-- Terra wearable integration
-- Run in Supabase SQL editor

-- Map athlete_id to Terra user connections
-- One athlete can have multiple devices (Garmin + Oura + Apple Watch etc.)
CREATE TABLE IF NOT EXISTS terra_users (
    id             SERIAL PRIMARY KEY,
    athlete_id     INTEGER NOT NULL,
    terra_user_id  TEXT NOT NULL UNIQUE,  -- Terra's UUID for this connection
    provider       TEXT NOT NULL,          -- GARMIN, OURA, WHOOP, APPLE, etc.
    reference_id   TEXT,                   -- username we passed to Terra
    connected_at   TIMESTAMPTZ DEFAULT NOW(),
    active         BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS terra_users_athlete_id    ON terra_users(athlete_id);
CREATE INDEX IF NOT EXISTS terra_users_terra_user_id ON terra_users(terra_user_id);
CREATE INDEX IF NOT EXISTS terra_users_reference_id  ON terra_users(reference_id);

-- Extend athlete_wellness with Terra-sourced fields
ALTER TABLE athlete_wellness
    ADD COLUMN IF NOT EXISTS source          TEXT DEFAULT 'garmin',
    ADD COLUMN IF NOT EXISTS readiness_score INTEGER,   -- 0-100 readiness/recovery
    ADD COLUMN IF NOT EXISTS sleep_light_s   INTEGER,   -- light sleep seconds
    ADD COLUMN IF NOT EXISTS sleep_deep_s    INTEGER,   -- deep sleep seconds
    ADD COLUMN IF NOT EXISTS sleep_rem_s     INTEGER;   -- REM sleep seconds
