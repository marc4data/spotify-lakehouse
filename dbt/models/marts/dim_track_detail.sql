-- dim_track_detail: track subtype of dim_content (data-contracts §3). ISRC is the tier-2 match key (§5, F9).
with tracks as (
    select * from {{ ref('int_tracks__latest') }}
    where content_type = 'track'
)

select
    content.content_key,
    coalesce(album.album_key, -1) as album_key,
    tracks.is_explicit,
    tracks.disc_number,
    tracks.track_number,
    tracks.is_local,
    tracks.isrc
from tracks
inner join {{ ref('dim_content') }} as content
    on content.content_uri = tracks.content_uri
left join {{ ref('dim_album') }} as album
    on album.album_id = tracks.album_id
