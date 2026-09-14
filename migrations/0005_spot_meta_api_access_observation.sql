-- spot_meta.api_access_observation (spot-main-R-033, R-008 F5).
--
-- data-contracts §3 defines dim_profile.has_api_access as "a stored refresh token AND at least one ok row
-- in spot_meta.poll_run". The token lives in ~/.config/spot/tokens.json, which dbt cannot read. So every
-- `spot refresh` records, for each profile in the registry, whether a token was stored at that moment,
-- and dbt reads the latest observation. Only a boolean is recorded — never the token, never its contents.
create table spot_meta.api_access_observation (
    id bigserial primary key,
    run_id text not null,
    profile_slug text not null,
    has_stored_token boolean not null,
    observed_at timestamptz not null default now(),
    constraint api_access_observation_run_profile unique (run_id, profile_slug)
);

create index api_access_observation_profile_observed_idx
    on spot_meta.api_access_observation (profile_slug, observed_at desc);
