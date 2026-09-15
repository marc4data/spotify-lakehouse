-- br_artist_genre weight normalization (data-contracts §4, R-024): for every artist version and every
-- tag_type, the weights sum to 1.0. Returns each (artist_key, tag_type) that does not, with its sum.
select
    artist_key,
    tag_type,
    count(*) as genre_count,
    sum(weight_factor) as weight_sum
from {{ ref('br_artist_genre') }}
group by artist_key, tag_type
having abs(sum(weight_factor) - 1.0) > 0.000001
