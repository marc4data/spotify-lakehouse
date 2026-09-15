-- Bucket allocation, the last step of the data-contracts §4 chain (spot-main-R-015):
--   ms_played -> primary artist (weight 1.0) -> each of that artist's genres (1/N) -> bucket (sum).
-- One row per (profile, month, bucket, genre, artist). Weights are br_artist_genre's 1/N WITHIN ONE tag_type
-- (var('allocation_tag_type')): never a denominator mixing curated genres and folksonomy tags (R-024, R-040 F4).
-- A play whose primary artist has no genre contributes to no bucket, and so to no row here: it is the
-- reconciliation's `unclassified` residual, never `other`. `other` holds only genres the seed maps to other, or
-- does not list. ms_played is NULL on API plays, so allocated_ms is export time; allocated_plays counts every play.
with plays as (
    select
        profiles.profile_slug,
        dates.year,
        dates.month_start,
        fct.ms_played,
        content_artist.artist_key
    from {{ ref('fct_play_event') }} as fct
    inner join {{ ref('dim_profile') }} as profiles
        on profiles.profile_key = fct.profile_key
    inner join {{ ref('dim_date') }} as dates
        on dates.date_key = fct.date_key
    inner join {{ ref('dim_content') }} as content
        on content.content_key = fct.content_key
    inner join {{ ref('br_content_artist') }} as content_artist
        on content_artist.content_key = fct.content_key
        and content_artist.is_primary
    where content.content_type = 'track'
),

genres as (
    select
        bridge.artist_key,
        bridge.genre_key,
        bridge.weight_factor,
        map.genre_name,
        map.genre_bucket_key,
        map.bucket_name,
        map.bucket_order
    from {{ ref('br_artist_genre') }} as bridge
    inner join {{ ref('int_genre_bucket_map') }} as map
        on map.genre_key = bridge.genre_key
    where bridge.tag_type = '{{ var("allocation_tag_type") }}'
)

select
    plays.profile_slug,
    plays.year,
    plays.month_start,
    genres.genre_bucket_key,
    genres.bucket_name,
    genres.bucket_order,
    genres.genre_key,
    genres.genre_name,
    plays.artist_key,
    artists.artist_name,
    count(*) as play_rows,
    sum(genres.weight_factor) as allocated_plays,
    count(plays.ms_played) as rows_with_duration,
    sum(plays.ms_played * genres.weight_factor) as allocated_ms
from plays
inner join genres
    on genres.artist_key = plays.artist_key
left join {{ ref('dim_artist') }} as artists
    on artists.artist_key = plays.artist_key
group by
    plays.profile_slug,
    plays.year,
    plays.month_start,
    genres.genre_bucket_key,
    genres.bucket_name,
    genres.bucket_order,
    genres.genre_key,
    genres.genre_name,
    plays.artist_key,
    artists.artist_name
