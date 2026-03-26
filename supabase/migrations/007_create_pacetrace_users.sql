-- PaceTrace unified user identity
-- Supports Strava-only, intervals.icu-only, or both
CREATE TABLE IF NOT EXISTS pacetrace_users (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    display_name    TEXT,
    email           TEXT,

    -- intervals.icu connection (v2)
    icu_athlete_id  TEXT,
    icu_api_key     TEXT,

    -- strava connection (v1, links to existing athlete_tokens)
    strava_athlete_id BIGINT,

    -- preferences
    max_hr          INT DEFAULT 185,
    rest_hr         INT DEFAULT 55,
    gender          TEXT DEFAULT 'male',

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pacetrace_users_icu ON pacetrace_users(icu_athlete_id);
CREATE INDEX IF NOT EXISTS idx_pacetrace_users_strava ON pacetrace_users(strava_athlete_id);
