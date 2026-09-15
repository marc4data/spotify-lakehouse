-- br_content_artist (data-contracts §4). Phase-1 allocation: weight 1.0 to the primary artist, 0.0 to
-- features. Splitting credit is a phase-2 change to weight_factor, not to this model.
select
    content.content_key,
    artist.artist_key,
    credits.is_primary,
    case when credits.is_primary then 1.0 else 0.0 end::numeric(3, 2) as weight_factor,
    credits.artist_ordinal
from {{ ref('int_content_artists') }} as credits
inner join {{ ref('dim_content') }} as content
    on content.content_uri = credits.content_uri
inner join {{ ref('dim_artist') }} as artist
    on artist.artist_id = credits.artist_id
    and artist.is_current  -- dim_artist is SCD Type 2 since R-024
