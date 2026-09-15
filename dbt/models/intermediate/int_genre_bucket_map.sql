-- Genre -> bucket, data-contracts §3 (spot-main-R-015). Every curated MusicBrainz genre string (the tag_type in
-- var('allocation_tag_type')) gets exactly one bucket: the seed's, or `other` when the seed does not list it.
-- is_mapped records which, so drift into `other` is visible rather than silent.
with genres as (
    select
        genre_key,
        genre_name
    from {{ ref('dim_genre') }}
    where tag_type = '{{ var("allocation_tag_type") }}'
),

seed as (
    select
        genre_name,
        bucket_name
    from {{ ref('genre_bucket_map') }}
)

select
    genres.genre_key,
    genres.genre_name,
    buckets.genre_bucket_key,
    buckets.bucket_name,
    buckets.bucket_order,
    seed.genre_name is not null as is_mapped
from genres
left join seed
    on seed.genre_name = genres.genre_name
inner join {{ ref('dim_genre_bucket') }} as buckets
    on buckets.bucket_name = coalesce(seed.bucket_name, 'other')
