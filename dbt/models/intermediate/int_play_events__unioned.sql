-- Every play-event candidate from every source, before deduplication (data-contracts §2).
-- Only the API path exists this round. The export path (R-003) adds one more union branch here with
-- source_system = 'export' and the export-only columns populated: no migration, no fact-table change.
with api as (
    select
        profile_slug,
        content_uri,
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
)

select * from api
