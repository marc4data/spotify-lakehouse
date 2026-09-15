-- One row per artist id: every artist credited on a track or album, whether or not GET /artists/{id}
-- has been called for it. Name prefers the artist's own response; `is_fetched` records whether one exists.
-- Credits come from recently-played items and, since R-037, GET /tracks/{id} lookups of export URIs.
with credited_tracks as (
    select track_artists, album_artists, ingested_at from {{ ref('stg_spotify__recently_played') }}
    union all
    select track_artists, album_artists, ingested_at from {{ ref('stg_spotify__track') }}
),

referenced as (
    select
        artist.value ->> 'id' as artist_id,
        artist.value ->> 'uri' as artist_uri,
        artist.value ->> 'name' as artist_name,
        credited_tracks.ingested_at
    from credited_tracks
    cross join lateral jsonb_array_elements(credited_tracks.track_artists || credited_tracks.album_artists)
        as artist (value)
    where artist.value ->> 'id' is not null
),

referenced_latest as (
    select distinct on (artist_id)
        artist_id,
        artist_uri,
        artist_name
    from referenced
    order by artist_id, ingested_at desc
),

fetched_latest as (
    select distinct on (artist_id)
        artist_id,
        artist_uri,
        artist_name,
        has_genres_key
    from {{ ref('stg_spotify__artist') }}
    order by artist_id, ingested_at desc
)

select
    coalesce(fetched_latest.artist_id, referenced_latest.artist_id) as artist_id,
    coalesce(fetched_latest.artist_uri, referenced_latest.artist_uri) as artist_uri,
    coalesce(fetched_latest.artist_name, referenced_latest.artist_name) as artist_name,
    fetched_latest.artist_id is not null as is_fetched,
    coalesce(fetched_latest.has_genres_key, false) as has_genres_key
from referenced_latest
full outer join fetched_latest
    on fetched_latest.artist_id = referenced_latest.artist_id
