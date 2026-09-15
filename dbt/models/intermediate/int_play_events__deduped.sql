-- Deduplication contract, data-contracts §2 as amended by spot-main-R-037: the key depends on the source pair.
--   export vs export : (profile, content_uri, ended_at_utc)                      second precision
--   export vs api    : (profile, content_uri, date_trunc('minute', ended_at_utc)) export wins, api row discarded whole
--   api vs api       : (profile, content_uri, date_trunc('minute', ended_at_utc)) unchanged
-- Minute truncation exists because the export's stream-end clock and the API's played_at disagree by seconds.
-- Two export rows share one clock, so truncating them only collapsed genuine plays (R-003: 4,011 rows).
-- Within a key the earliest-ingested capture wins, so a play seen by several polls keeps the lineage of the
-- poll that first saw it.
with candidates as (
    select * from {{ ref('int_play_events__unioned') }}
),

export_ranked as (
    select
        *,
        row_number() over (
            partition by profile_slug, content_uri, ended_at_utc
            order by ingested_at, raw_response_id, item_ordinal
        ) as key_rank
    from candidates
    where source_system = 'export'
),

export_kept as (
    select * from export_ranked
    where key_rank = 1
),

api_ranked as (
    select
        *,
        row_number() over (
            partition by profile_slug, content_uri, date_trunc('minute', ended_at_utc)
            order by ingested_at, raw_response_id, item_ordinal
        ) as key_rank
    from candidates
    where source_system = 'api'
),

api_kept as (
    select api_ranked.*
    from api_ranked
    where api_ranked.key_rank = 1
        and not exists (
            select 1
            from export_kept
            where export_kept.profile_slug = api_ranked.profile_slug
                and export_kept.content_uri = api_ranked.content_uri
                and date_trunc('minute', export_kept.ended_at_utc)
                    = date_trunc('minute', api_ranked.ended_at_utc)
        )
),

kept as (
    select * from export_kept
    union all
    select * from api_kept
)

select
    profile_slug,
    content_uri,
    content_type,
    ended_at_utc,
    ms_played,
    is_ms_played_imputed,
    reason_start,
    reason_end,
    was_skipped,
    was_shuffled,
    was_offline,
    platform,
    conn_country,
    source_system,
    source_file,
    ingested_at,
    raw_response_id
from kept
