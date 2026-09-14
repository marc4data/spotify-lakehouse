-- The poller's own record (spot-main-R-017). Not `raw`: raw holds only what Spotify returned.
--
-- poll_run      one row per profile per `spot refresh`, successful or not.
-- overflow_gap  one row per poll whose window's oldest play is newer than the newest play captured
--               before it: plays may have left the 50-item window uncaptured. recently-played does not
--               page backwards (R-018), so this row is the only evidence such a loss ever happened.

create table spot_meta.poll_run (
    id bigserial primary key,
    run_id text not null,
    profile_slug text not null,
    started_at timestamptz not null,
    finished_at timestamptz not null default now(),
    status text not null check (status in ('ok', 'error')),
    raw_response_id bigint references raw.api_response (id),
    items_returned int,
    window_oldest_played_at timestamptz,
    window_newest_played_at timestamptz,
    previous_high_water_mark timestamptz,
    error_message text,
    constraint poll_run_ok_has_raw check ((status = 'ok') = (raw_response_id is not null))
);

create index poll_run_profile_finished_idx on spot_meta.poll_run (profile_slug, finished_at desc);

create table spot_meta.overflow_gap (
    id bigserial primary key,
    poll_run_id bigint not null unique references spot_meta.poll_run (id),
    profile_slug text not null,
    gap_start timestamptz not null,  -- newest play captured before this poll (previous high-water mark)
    gap_end timestamptz not null,  -- oldest play in this poll's window
    items_returned int not null,
    detected_at timestamptz not null default now(),
    constraint overflow_gap_ordered check (gap_end > gap_start)
);
