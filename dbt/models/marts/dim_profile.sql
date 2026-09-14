-- dim_profile, SCD Type 1 (data-contracts §3). One row per person in the registry, not per account.
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

api_coverage as (
    select
        profile_slug,
        min(ingested_at) as api_coverage_start
    from {{ ref('stg_spotify__recently_played') }}
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
        latest_me.profile_slug is not null as has_api_access,
        null::timestamptz as export_coverage_start,  -- derived from the export in R-003
        null::timestamptz as export_coverage_end,
        api_coverage.api_coverage_start
    from registry
    left join latest_me
        on latest_me.profile_slug = registry.profile_slug
    left join api_coverage
        on api_coverage.profile_slug = registry.profile_slug
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
