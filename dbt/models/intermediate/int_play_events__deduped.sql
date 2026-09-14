-- Deduplication contract, data-contracts §2.
--   natural key : (profile, content_uri, date_trunc('minute', ended_at_utc))
--   precedence  : export beats api, always; the losing row is discarded whole, never merged.
-- Within one source the earliest-ingested capture wins, so a play seen by several polls keeps the
-- lineage of the poll that first saw it.
with candidates as (
    select
        *,
        case source_system
            when 'export' then 1
            when 'api' then 2
        end as source_precedence
    from {{ ref('int_play_events__unioned') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by profile_slug, content_uri, date_trunc('minute', ended_at_utc)
            order by source_precedence, ingested_at, raw_response_id, item_ordinal
        ) as natural_key_rank
    from candidates
)

select
    profile_slug,
    content_uri,
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
from ranked
where natural_key_rank = 1
