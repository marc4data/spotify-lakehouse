-- data-contracts §2 as amended by R-037, over real data: export rows are unique to the second, api rows to the
-- minute, and no surviving api row shares a minute key with an export row. Any row fails, naming the rule.
with plays as (
    select * from {{ ref('int_play_events__deduped') }}
)

select
    'export rows share a second' as problem,
    profile_slug,
    content_uri,
    ended_at_utc::text as at_key,
    count(*) as rows_at_key
from plays
where source_system = 'export'
group by profile_slug, content_uri, ended_at_utc
having count(*) > 1

union all

select
    'api rows share a minute',
    profile_slug,
    content_uri,
    date_trunc('minute', ended_at_utc)::text,
    count(*)
from plays
where source_system = 'api'
group by profile_slug, content_uri, date_trunc('minute', ended_at_utc)
having count(*) > 1

union all

select
    'api row shares a minute with an export row',
    api.profile_slug,
    api.content_uri,
    date_trunc('minute', api.ended_at_utc)::text,
    count(*)
from plays as api
inner join plays as export
    on export.source_system = 'export'
    and export.profile_slug = api.profile_slug
    and export.content_uri = api.content_uri
    and date_trunc('minute', export.ended_at_utc) = date_trunc('minute', api.ended_at_utc)
where api.source_system = 'api'
group by api.profile_slug, api.content_uri, date_trunc('minute', api.ended_at_utc)
