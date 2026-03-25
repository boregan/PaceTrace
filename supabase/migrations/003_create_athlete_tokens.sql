CREATE TABLE IF NOT EXISTS athlete_tokens (
    athlete_id   BIGINT PRIMARY KEY,
    username     TEXT   UNIQUE NOT NULL,
    display_name TEXT,
    access_token      TEXT    NOT NULL,
    refresh_token     TEXT    NOT NULL,
    token_expires_at  BIGINT  NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_athlete_tokens_username ON athlete_tokens(username);
