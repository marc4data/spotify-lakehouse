-- One row per MusicBrainz `GET /artist/{mbid}?inc=genres+tags` response (spot-main-R-024).
-- genres: MusicBrainz's curated genre list. tags: the open folksonomy (a superset in practice).
with source as (
    select *
    from {{ source('raw', 'external_response') }}
    where source = 'musicbrainz'
        and feed = 'artist'
),

renamed as (
    select
        id as raw_response_id,
        request_key as artist_mbid,
        not (payload ? 'error') as is_found,
        payload ->> 'name' as musicbrainz_artist_name,
        coalesce(payload -> 'genres', '[]'::jsonb) as genres,
        coalesce(payload -> 'tags', '[]'::jsonb) as tags,
        ingested_at,
        source_file
    from source
)

select * from renamed
