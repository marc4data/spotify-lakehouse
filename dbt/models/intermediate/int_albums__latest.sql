-- One row per album id: attributes from its most recent observation (SCD Type 1 source for dim_album), from
-- recently-played items and, since R-037, GET /tracks/{id} lookups of export URIs.
with observations as (
    select
        album_id, album_uri, album_name, album_type, album_release_date, album_release_date_precision,
        album_total_tracks, ingested_at, played_at_utc, raw_response_id, item_ordinal
    from {{ ref('stg_spotify__recently_played') }}

    union all

    select
        album_id, album_uri, album_name, album_type, album_release_date, album_release_date_precision,
        album_total_tracks, ingested_at, played_at_utc, raw_response_id, item_ordinal
    from {{ ref('stg_spotify__track') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by album_id
            order by ingested_at desc, played_at_utc desc nulls last, raw_response_id desc, item_ordinal
        ) as recency_rank
    from observations
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
from ranked
where recency_rank = 1
