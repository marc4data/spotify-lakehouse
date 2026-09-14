-- dim_profile, SCD Type 1 (data-contracts §3). One row per person in the registry, not per account.
-- has_api_access and api_coverage_start follow the contract as amended after R-017 (e3c62dd), made
-- observable by R-033: the poller records token presence in spot_meta, because dbt cannot read the token.
with registry as (
    select * from {{ ref('stg_spot_meta__profile_registry') }}
),

latest_me as (
    select distinct on (profile_slug)
        profile_slug,
        spotify_user_id,
        display_name
    from {{ ref('stg_spotify__me') }}
    order by profile_slug, ingested_at desc
),

latest_token as (
    select distinct on (profile_slug)
        profile_slug,
        has_stored_token
    from {{ ref('stg_spot_meta__api_access_observation') }}
    order by profile_slug, observed_at desc, api_access_observation_id desc
),

ok_polls as (
    select
        profile_slug,
        min(finished_at) as first_ok_poll_at
    from {{ ref('stg_spot_meta__poll_run') }}
    where status = 'ok'
    group by profile_slug
),

profiles as (
    select
        (row_number() over (order by registry.profile_slug))::int as profile_key,
        registry.profile_slug,
        latest_me.display_name,
        latest_me.spotify_user_id,
        registry.household_role,
        registry.home_timezone,
        -- a stored refresh token AND at least one ok poll (data-contracts §3)
        coalesce(latest_token.has_stored_token, false)
        and ok_polls.first_ok_poll_at is not null as has_api_access,
        null::timestamptz as export_coverage_start,  -- derived from the export in R-003
        null::timestamptz as export_coverage_end,
        -- the first ok poll; probe captures before it are data, not coverage (data-contracts §3)
        ok_polls.first_ok_poll_at as api_coverage_start
    from registry
    left join latest_me
        on latest_me.profile_slug = registry.profile_slug
    left join latest_token
        on latest_token.profile_slug = registry.profile_slug
    left join ok_polls
        on ok_polls.profile_slug = registry.profile_slug
),

unknown_member as (
    select
        -1 as profile_key,
        '(unknown)'::text as profile_slug,
        'Unknown profile'::text as display_name,
        null::text as spotify_user_id,
        null::text as household_role,
        null::text as home_timezone,
        false as has_api_access,
        null::timestamptz as export_coverage_start,
        null::timestamptz as export_coverage_end,
        null::timestamptz as api_coverage_start
)

select * from profiles
union all
select * from unknown_member
