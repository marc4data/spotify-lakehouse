-- dim_artist, SCD Type 1 (data-contracts §3, amended R-004). Returns to Type 2 on sourced genres when
-- spot-main-R-024 lands; genre_source is NULL until then.
with artists as (
    select * from {{ ref('int_artists__latest') }}
),

resolved as (
    select
        (row_number() over (order by artist_id))::int as artist_key,
        artist_id,
        artist_uri,
        artist_name,
        null::text as genre_source
    from artists
),

unknown_member as (
    select
        -1 as artist_key,
        '(unknown)'::text as artist_id,
        null::text as artist_uri,
        'Unknown artist'::text as artist_name,
        null::text as genre_source
)

select * from resolved
union all
select * from unknown_member
