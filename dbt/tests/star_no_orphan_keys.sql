-- Required test 2 (R-004): every foreign key in the star resolves, including to the -1/-2 unknown
-- members. One query covering every FK, so a single failure lists every orphan with its key name.
with fct as (
    select * from {{ ref('fct_play_event') }}
),

orphans as (
    select 'fct_play_event.profile_key' as foreign_key, fct.profile_key::bigint as key_value
    from fct
    left join {{ ref('dim_profile') }} as d on d.profile_key = fct.profile_key
    where d.profile_key is null

    union all
    select 'fct_play_event.content_key', fct.content_key
    from fct
    left join {{ ref('dim_content') }} as d on d.content_key = fct.content_key
    where d.content_key is null

    union all
    select 'fct_play_event.date_key', fct.date_key
    from fct
    left join {{ ref('dim_date') }} as d on d.date_key = fct.date_key
    where d.date_key is null

    union all
    select 'fct_play_event.time_of_day_key', fct.time_of_day_key
    from fct
    left join {{ ref('dim_time_of_day') }} as d on d.time_of_day_key = fct.time_of_day_key
    where d.time_of_day_key is null

    union all
    select 'br_content_artist.content_key', br.content_key
    from {{ ref('br_content_artist') }} as br
    left join {{ ref('dim_content') }} as d on d.content_key = br.content_key
    where d.content_key is null

    union all
    select 'br_content_artist.artist_key', br.artist_key
    from {{ ref('br_content_artist') }} as br
    left join {{ ref('dim_artist') }} as d on d.artist_key = br.artist_key
    where d.artist_key is null

    union all
    select 'br_artist_genre.artist_key', br.artist_key
    from {{ ref('br_artist_genre') }} as br
    left join {{ ref('dim_artist') }} as d on d.artist_key = br.artist_key
    where d.artist_key is null

    union all
    select 'br_artist_genre.genre_key', br.genre_key
    from {{ ref('br_artist_genre') }} as br
    left join {{ ref('dim_genre') }} as d on d.genre_key = br.genre_key
    where d.genre_key is null

    union all
    select 'fct_playlist_membership.playlist_key', m.playlist_key
    from {{ ref('fct_playlist_membership') }} as m
    left join {{ ref('dim_playlist') }} as d on d.playlist_key = m.playlist_key
    where d.playlist_key is null

    union all
    -- R-054: a playlist can contain tracks nobody has played. This goes red if dim_content stops
    -- accepting playlist membership as a source — which is the whole point of adding it.
    select 'fct_playlist_membership.content_key', m.content_key
    from {{ ref('fct_playlist_membership') }} as m
    left join {{ ref('dim_content') }} as d on d.content_key = m.content_key
    where d.content_key is null

    union all
    select 'dim_track_detail.content_key', t.content_key
    from {{ ref('dim_track_detail') }} as t
    left join {{ ref('dim_content') }} as d on d.content_key = t.content_key
    where d.content_key is null

    union all
    select 'dim_track_detail.album_key', t.album_key
    from {{ ref('dim_track_detail') }} as t
    left join {{ ref('dim_album') }} as d on d.album_key = t.album_key
    where d.album_key is null
)

select * from orphans
