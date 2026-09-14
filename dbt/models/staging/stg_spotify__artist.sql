-- One row per GET /artists/{id} response. This app receives no `genres` key (measured 45/45, R-004);
-- `has_genres_key` records that per observation, so a reappearing field is visible without a code change.
with source as (
    select *
    from {{ source('raw', 'api_response') }}
    where feed = 'artist'
),

renamed as (
    select
        id as raw_response_id,
        profile_slug,
        payload ->> 'id' as artist_id,
        payload ->> 'uri' as artist_uri,
        payload ->> 'name' as artist_name,
        payload ? 'genres' as has_genres_key,
        ingested_at,
        source_file
    from source
)

select * from renamed
