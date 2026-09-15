-- One row per MusicBrainz ISRC lookup response (spot-main-R-024). A 404 is kept, not filtered: it is the
-- "ISRC unknown to MusicBrainz" arrow of the coverage cascade, and request_key is the only place its ISRC lives.
with source as (
    select *
    from {{ source('raw', 'external_response') }}
    where source = 'musicbrainz'
        and feed = 'isrc_lookup'
),

renamed as (
    select
        id as raw_response_id,
        request_key as isrc,
        not (payload ? 'error') as is_found,
        coalesce(payload -> 'recordings', '[]'::jsonb) as recordings,
        ingested_at,
        source_file
    from source
)

select * from renamed
