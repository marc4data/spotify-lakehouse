-- One row per (Spotify artist, tag_type, tag string) from the latest MusicBrainz observation of the artist's
-- mapped MBID (spot-main-R-024). The extract-side view of "what genres does this artist have now", for the
-- allocation reconciliation; dim_artist / br_artist_genre carry the full history.
with latest_response as (
    select distinct on (artist_mbid)
        artist_mbid,
        raw_response_id
    from {{ ref('stg_musicbrainz__artist') }}
    order by artist_mbid, ingested_at desc, raw_response_id desc
)

select
    mapping.artist_id,
    mapping.artist_mbid,
    tags.tag_type,
    tags.tag_name,
    tags.vote_count
from {{ ref('int_artist_musicbrainz') }} as mapping
inner join latest_response
    on latest_response.artist_mbid = mapping.artist_mbid
inner join {{ ref('int_musicbrainz__artist_tags') }} as tags
    on tags.raw_response_id = latest_response.raw_response_id
