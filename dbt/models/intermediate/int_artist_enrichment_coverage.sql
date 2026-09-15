-- Spotify artist-object coverage, reported beside the allocation chain and not inside it (data-contracts §4,
-- spot-main-R-041). The GET /artists/{id} response carries no genre (CLAUDE.md §4) and gates no allocation
-- step. Nor does it supply names: those come from the track and album credit objects, which carry every key
-- the artist response has except `images` (R-041 F2, F7: 438 of 438 names identical). What the fetch adds is
-- `images`, and nothing else this project uses. One row per profile, over music plays.
with music as (
    select
        plays.profile_slug,
        primary_artist.artist_id,
        coalesce(artists.is_fetched, false) as is_fetched
    from {{ ref('int_play_events__deduped') }} as plays
    left join {{ ref('int_content_artists') }} as primary_artist
        on primary_artist.content_uri = plays.content_uri
        and primary_artist.is_primary
    left join {{ ref('int_artists__latest') }} as artists
        on artists.artist_id = primary_artist.artist_id
    where plays.content_type = 'track'
)

select
    profile_slug,
    count(*) as music_play_count,
    count(artist_id) as artist_identified_play_count,
    count(*) filter (where is_fetched) as artist_object_play_count,
    count(distinct artist_id) as primary_artists_identified,
    count(distinct artist_id) filter (where is_fetched) as primary_artists_with_object,
    round(100.0 * count(*) filter (where is_fetched) / nullif(count(artist_id), 0), 2)
        as pct_identified_plays_with_object
from music
group by profile_slug
