-- br_artist_genre (data-contracts §4, spot-main-R-024). Grain: one row per (artist_key, genre_key).
--
-- weight_factor = 1 / count(genres for that artist) WITHIN ONE tag_type. The curated `genre` list and the
-- `tag` folksonomy are two parallel allocations, never one denominator: an artist with 3 genres and 40 tags
-- would otherwise give each curated genre 1/43 and the folksonomy would drown it. Filter on tag_type before
-- summing; weights for one artist sum to 1.0 per tag_type (tests/br_artist_genre_weights_sum_to_one.sql).
-- Every artist version gets the genres of the observation that established it.
with versions as (
    select
        artist_key,
        genre_raw_response_id
    from {{ ref('dim_artist') }}
    where genre_raw_response_id is not null
),

artist_tags as (
    select
        versions.artist_key,
        tags.tag_type,
        tags.tag_name,
        tags.vote_count
    from versions
    inner join {{ ref('int_musicbrainz__artist_tags') }} as tags
        on tags.raw_response_id = versions.genre_raw_response_id
)

select
    artist_tags.artist_key,
    genres.genre_key,
    artist_tags.tag_type,
    artist_tags.vote_count,
    1.0 / count(*) over (partition by artist_tags.artist_key, artist_tags.tag_type) as weight_factor
from artist_tags
inner join {{ ref('dim_genre') }} as genres
    on genres.genre_name = artist_tags.tag_name
    and genres.tag_type = artist_tags.tag_type
