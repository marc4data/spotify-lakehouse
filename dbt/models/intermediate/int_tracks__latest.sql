-- One row per track URI: attributes from its most recent observation (SCD Type 1 source for dim_content,
-- dim_track_detail), plus first/last seen from every observation.
with observations as (
    select
        *,
        row_number() over (
            partition by content_uri
            order by ingested_at desc, played_at_utc desc, raw_response_id desc, item_ordinal
        ) as recency_rank
    from {{ ref('stg_spotify__recently_played') }}
),

seen as (
    select
        content_uri,
        min(played_at_utc) as first_seen_at,
        max(played_at_utc) as last_seen_at
    from {{ ref('stg_spotify__recently_played') }}
    group by content_uri
)

select
    observations.content_uri,
    observations.content_id,
    observations.content_type,
    observations.content_name,
    observations.duration_ms,
    observations.is_explicit,
    observations.disc_number,
    observations.track_number,
    observations.is_local,
    observations.isrc,
    observations.album_id,
    observations.album_name,
    observations.track_artists,
    observations.album_artists,
    seen.first_seen_at,
    seen.last_seen_at
from observations
inner join seen
    on seen.content_uri = observations.content_uri
where observations.recency_rank = 1
