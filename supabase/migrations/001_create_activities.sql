create table if not exists activities (
    id              uuid primary key default gen_random_uuid(),
    strava_id       bigint not null unique,
    athlete_id      bigint not null,
    name            text,
    sport_type      text,
    start_date      timestamptz,
    distance_m      double precision,
    elapsed_s       integer,
    moving_time_s   integer,
    avg_heartrate   double precision,
    max_heartrate   double precision,
    total_elevation_gain_m double precision,
    avg_speed_ms    double precision,
    gear_id         text,
    created_at      timestamptz default now()
);

create index if not exists activities_athlete_id_idx on activities(athlete_id);
create index if not exists activities_start_date_idx on activities(start_date);
