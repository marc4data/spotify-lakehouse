-- Spotify artist -> MusicBrainz artist MBID, by identifier only (spot-main-R-024): each track the artist is
-- primary on -> its ISRC -> the recording's first artist credit. No name matching, anywhere. An artist maps
-- only when every one of its resolvable tracks names the same MBID; otherwise artist_mbid is NULL.
with primary_tracks as (
    select
        credits.artist_id,
        upper(replace(tracks.isrc, '-', '')) as isrc
    from {{ ref('int_content_artists') }} as credits
    inner join {{ ref('int_tracks__latest') }} as tracks
        on tracks.content_uri = credits.content_uri
    where credits.is_primary
        and tracks.isrc is not null
),

mapped as (
    select
        primary_tracks.artist_id,
        primary_tracks.isrc,
        isrc_artist.primary_artist_mbid
    from primary_tracks
    left join {{ ref('int_musicbrainz__isrc_artist') }} as isrc_artist
        on isrc_artist.isrc = primary_tracks.isrc
)

select
    artist_id,
    count(*) as primary_track_isrcs,
    count(primary_artist_mbid) as isrcs_resolved,
    count(distinct primary_artist_mbid) as distinct_mbids,
    case when count(distinct primary_artist_mbid) = 1 then min(primary_artist_mbid) end as artist_mbid
from mapped
group by artist_id
