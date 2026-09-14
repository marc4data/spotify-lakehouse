-- One row per (track, credited artist), from the track's latest observation. The first credited artist
-- is primary (data-contracts §4: 100% of a play is attributed to the primary artist in phase 1).
-- An artist credited twice on one track keeps its first position.
with credited as (
    select
        tracks.content_uri,
        artist.value ->> 'id' as artist_id,
        artist.ordinality::int as artist_ordinal
    from {{ ref('int_tracks__latest') }} as tracks
    cross join lateral jsonb_array_elements(tracks.track_artists) with ordinality as artist (value, ordinality)
    where artist.value ->> 'id' is not null
),

first_credit as (
    select
        *,
        row_number() over (partition by content_uri, artist_id order by artist_ordinal) as credit_rank
    from credited
)

select
    content_uri,
    artist_id,
    artist_ordinal,
    artist_ordinal = min(artist_ordinal) over (partition by content_uri) as is_primary
from first_credit
where credit_rank = 1
