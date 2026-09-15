-- dim_genre_bucket (data-contracts §3, spot-main-R-015): the analysis taxonomy, one row per bucket.
-- Marc owns the list (seeds/genre_bucket.csv) and the mapping (seeds/genre_bucket_map.csv). Both are seeds so
-- he can change them in a spreadsheet without a round. bucket_order fixes spoke order and colour in every radar.
select
    bucket_order::int as genre_bucket_key,
    bucket_order::int as bucket_order,
    bucket_name::text as bucket_name
from {{ ref('genre_bucket') }}
