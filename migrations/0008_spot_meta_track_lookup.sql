-- spot_meta.track_lookup: one row per GET /tracks/{id} attempt by `spot resolve-tracks` (spot-main-R-037).
-- Not raw: raw holds only what Spotify returned. A 404 body carries no track id, so this is where "the API
-- does not know this URI" is remembered: without it an unresolvable id would be re-requested on every run,
-- and at ~47,000 ids an interruption must cost nothing.
--   ok         the response is in raw.api_response (feed = 'track'), raw_response_id points at it
--   not_found  terminal: 404 or 400 for this id; never re-requested
--   error      transient (5xx / 429 after retries); re-requested on the next run
create table spot_meta.track_lookup (
    id bigserial primary key,
    run_id text not null,
    track_id text not null,
    status text not null check (status in ('ok', 'not_found', 'error')),
    http_status int,
    raw_response_id bigint references raw.api_response (id),
    returned_track_id text,
    error_message text,
    looked_up_at timestamptz not null default now(),
    constraint track_lookup_ok_has_raw check ((status = 'ok') = (raw_response_id is not null))
);

create index track_lookup_track_status_idx on spot_meta.track_lookup (track_id, status);
