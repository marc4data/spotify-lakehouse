-- dim_album, SCD Type 1 (data-contracts §3). release_date stays text; parse by precision at use.
with albums as (
    select * from {{ ref('int_albums__latest') }}
),

resolved as (
    select
        (row_number() over (order by album_id))::int as album_key,
        album_id,
        album_uri,
        album_name,
        album_type,
        album_release_date as release_date,
        album_release_date_precision as release_date_precision,
        album_total_tracks as total_tracks
    from albums
),

unknown_member as (
    select
        -1 as album_key,
        '(unknown)'::text as album_id,
        null::text as album_uri,
        'Unknown album'::text as album_name,
        null::text as album_type,
        null::text as release_date,
        null::text as release_date_precision,
        null::int as total_tracks
)

select * from resolved
union all
select * from unknown_member
