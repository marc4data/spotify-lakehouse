-- One row per GET /tracks/{id} response, the lookup of an export track URI (spot-main-R-037). Same columns as
-- stg_spotify__recently_played's track fields, so int_tracks__latest can treat both as observations of a track.
with source as (
    select *
    from {{ source('raw', 'api_response') }}
    where feed = 'track'
),

renamed as (
    select
        id as raw_response_id,
        profile_slug,
        ingested_at,
        source_file,
        1 as item_ordinal,
        null::timestamptz as played_at_utc,
        payload ->> 'uri' as content_uri,
        payload ->> 'id' as content_id,
        payload ->> 'type' as content_type,
        payload ->> 'name' as content_name,
        (payload ->> 'duration_ms')::int as duration_ms,
        (payload ->> 'explicit')::boolean as is_explicit,
        (payload ->> 'disc_number')::int as disc_number,
        (payload ->> 'track_number')::int as track_number,
        (payload ->> 'is_local')::boolean as is_local,
        payload -> 'external_ids' ->> 'isrc' as isrc,
        payload -> 'album' ->> 'id' as album_id,
        payload -> 'album' ->> 'uri' as album_uri,
        payload -> 'album' ->> 'name' as album_name,
        payload -> 'album' ->> 'album_type' as album_type,
        payload -> 'album' ->> 'release_date' as album_release_date,
        payload -> 'album' ->> 'release_date_precision' as album_release_date_precision,
        (payload -> 'album' ->> 'total_tracks')::int as album_total_tracks,
        coalesce(payload -> 'artists', '[]'::jsonb) as track_artists,
        coalesce(payload -> 'album' -> 'artists', '[]'::jsonb) as album_artists
    from source
    -- structurally invalid rows only
    where payload ->> 'uri' is not null
)

select * from renamed
