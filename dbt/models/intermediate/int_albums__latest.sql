-- One row per album id: attributes from its most recent observation (SCD Type 1 source for dim_album).
with observations as (
    select
        album_id,
        album_uri,
        album_name,
        album_type,
        album_release_date,
        album_release_date_precision,
        album_total_tracks,
        row_number() over (
            partition by album_id
            order by ingested_at desc, played_at_utc desc, raw_response_id desc, item_ordinal
        ) as recency_rank
    from {{ ref('stg_spotify__recently_played') }}
    where album_id is not null
)

select
    album_id,
    album_uri,
    album_name,
    album_type,
    album_release_date,
    album_release_date_precision,
    album_total_tracks
from observations
where recency_rank = 1
