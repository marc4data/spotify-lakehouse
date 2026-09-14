-- One row per item in a GET /me/player/recently-played response.
-- Absent keys (popularity, available_markets, preview_url) are simply not selected; `->>` on a missing
-- key or a null `context` yields NULL, so no coalescing is needed here.
with source as (
    select *
    from {{ source('raw', 'api_response') }}
    where feed = 'recently_played'
),

items as (
    select
        source.id as raw_response_id,
        source.profile_slug,
        source.ingested_at,
        source.source_file,
        item.ordinality::int as item_ordinal,
        item.value as item
    from source
    cross join lateral jsonb_array_elements(source.payload -> 'items') with ordinality as item (value, ordinality)
),

renamed as (
    select
        raw_response_id,
        profile_slug,
        ingested_at,
        source_file,
        item_ordinal,
        (item ->> 'played_at')::timestamptz as played_at_utc,
        item -> 'track' ->> 'uri' as content_uri,
        item -> 'track' ->> 'id' as content_id,
        item -> 'track' ->> 'type' as content_type,
        item -> 'track' ->> 'name' as content_name,
        (item -> 'track' ->> 'duration_ms')::int as duration_ms,
        (item -> 'track' ->> 'explicit')::boolean as is_explicit,
        (item -> 'track' ->> 'disc_number')::int as disc_number,
        (item -> 'track' ->> 'track_number')::int as track_number,
        (item -> 'track' ->> 'is_local')::boolean as is_local,
        item -> 'track' -> 'external_ids' ->> 'isrc' as isrc,
        item -> 'track' -> 'album' ->> 'id' as album_id,
        item -> 'track' -> 'album' ->> 'uri' as album_uri,
        item -> 'track' -> 'album' ->> 'name' as album_name,
        item -> 'track' -> 'album' ->> 'album_type' as album_type,
        -- text, never date: precision varies (data-contracts §3)
        item -> 'track' -> 'album' ->> 'release_date' as album_release_date,
        item -> 'track' -> 'album' ->> 'release_date_precision' as album_release_date_precision,
        (item -> 'track' -> 'album' ->> 'total_tracks')::int as album_total_tracks,
        item -> 'context' ->> 'type' as context_type,
        item -> 'context' ->> 'uri' as context_uri,
        coalesce(item -> 'track' -> 'artists', '[]'::jsonb) as track_artists,
        coalesce(item -> 'track' -> 'album' -> 'artists', '[]'::jsonb) as album_artists
    from items
    -- Structurally invalid rows only: without a timestamp and a content uri an item cannot be a play.
    where item ->> 'played_at' is not null
        and item -> 'track' ->> 'uri' is not null
)

select * from renamed
