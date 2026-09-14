-- data-contracts §3 (amended e3c62dd), made observable by R-033:
--   has_api_access     = the latest recorded token presence AND at least one ok poll_run row
--   api_coverage_start = the first ok poll_run.finished_at
-- Recomputed here straight from the spot_meta sources, independently of dim_profile's own SQL.
-- Every row returned is a profile where the built dimension disagrees with the contract.
with latest_token as (
    select distinct on (profile_slug)
        profile_slug,
        has_stored_token
    from {{ source('spot_meta', 'api_access_observation') }}
    order by profile_slug, observed_at desc, id desc
),

ok_polls as (
    select
        profile_slug,
        min(finished_at) as first_ok_poll_at
    from {{ source('spot_meta', 'poll_run') }}
    where status = 'ok'
    group by profile_slug
),

expected as (
    select
        registry.profile_slug,
        coalesce(latest_token.has_stored_token, false)
        and ok_polls.first_ok_poll_at is not null as has_api_access,
        ok_polls.first_ok_poll_at as api_coverage_start
    from {{ source('spot_meta', 'profile_registry') }} as registry
    left join latest_token
        on latest_token.profile_slug = registry.profile_slug
    left join ok_polls
        on ok_polls.profile_slug = registry.profile_slug
)

select
    expected.profile_slug,
    expected.has_api_access as expected_has_api_access,
    built.has_api_access as built_has_api_access,
    expected.api_coverage_start as expected_api_coverage_start,
    built.api_coverage_start as built_api_coverage_start
from expected
left join {{ ref('dim_profile') }} as built
    on built.profile_slug = expected.profile_slug
where built.profile_slug is null
    or built.has_api_access is distinct from expected.has_api_access
    or built.api_coverage_start is distinct from expected.api_coverage_start
