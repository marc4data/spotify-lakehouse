-- spot_meta.rate_limit_event: one row per HTTP 429 a Spotify client received (spot-main-R-041).
-- R-040's extract-artists got `Retry-After: 44356` on its first call. Reading that as a per-day quota
-- (R-040 F1) is an inference from one header. The way to test it is to let normal operation accumulate
-- evidence, never to spend calls looking for the limit, so every command that talks to Spotify records each
-- 429 here, whatever it then does about it. The endpoint is a template (`/artists/{id}`): no id, no token.
--   slept    Retry-After within the cap (api.MAX_RETRY_AFTER_SECONDS); the client waited and retried
--   raised   Retry-After above the cap; the client raised without sleeping and the command stopped
--   gave_up  within the cap, but the client's retries were exhausted
create table spot_meta.rate_limit_event (
    id bigserial primary key,
    run_id text not null,
    command text not null,
    profile_slug text not null,
    endpoint text not null,
    retry_after_header text,
    retry_after_s numeric not null,
    action text not null check (action in ('slept', 'raised', 'gave_up')),
    observed_at timestamptz not null default now()
);

create index rate_limit_event_observed_idx on spot_meta.rate_limit_event (observed_at desc);
