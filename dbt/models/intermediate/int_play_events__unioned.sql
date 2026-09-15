-- Every play-event candidate from every source, before deduplication (data-contracts §2).
-- raw_response_id / item_ordinal address the row in its own raw table: raw.api_response for api rows,
-- raw.export_record (id, record_index) for export rows. They order ties, nothing more.
-- Audiobook chapters are plays too (R-037 amended §3's supertype): total listening that omits a content
-- type is not total.
with api as (
    select
        profile_slug,
        content_uri,
        content_type,
        played_at_utc as ended_at_utc,
        null::int as ms_played,
        false as is_ms_played_imputed,
        null::text as reason_start,
        null::text as reason_end,
        null::boolean as was_skipped,
        null::boolean as was_shuffled,
        null::boolean as was_offline,
        null::text as platform,
        null::text as conn_country,
        'api'::text as source_system,
        source_file,
        ingested_at,
        raw_response_id,
        item_ordinal
    from {{ ref('stg_spotify__recently_played') }}
),

export as (
    select
        profile_slug,
        content_uri,
        content_type,
        ended_at_utc,
        ms_played,
        false as is_ms_played_imputed,
        reason_start,
        reason_end,
        was_skipped,
        was_shuffled,
        was_offline,
        platform,
        conn_country,
        'export'::text as source_system,
        source_file,
        ingested_at,
        raw_record_id as raw_response_id,
        record_index as item_ordinal
    from {{ ref('stg_export__play_record') }}
    where content_type in ('track', 'episode', 'audiobook_chapter')
)

select * from api
union all
select * from export
