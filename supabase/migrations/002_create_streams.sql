create table if not exists streams (
    id          uuid primary key default gen_random_uuid(),
    activity_id bigint not null unique references activities(strava_id) on delete cascade,
    time_s      integer[]    default '{}',
    heartrate   integer[]    default '{}',
    velocity_ms double precision[] default '{}',
    altitude_m  double precision[] default '{}',
    distance_m  double precision[] default '{}',
    cadence     integer[]    default '{}',
    created_at  timestamptz default now()
);

create index if not exists streams_activity_id_idx on streams(activity_id);
