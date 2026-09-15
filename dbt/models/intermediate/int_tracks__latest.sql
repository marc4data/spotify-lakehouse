-- One row per track URI: attributes from its most recent observation (SCD Type 1 source for dim_content,
-- dim_track_detail). An observation is a recently-played item or, since R-037, a GET /tracks/{id} lookup of an
-- export URI. first/last seen come from recently-played plays only (NULL for a lookup-only track); dim_content
-- computes seen across every source itself.
with observations as (
    select
        raw_response_id, item_ordinal, ingested_at, played_at_utc, content_uri, content_id, content_type,
        content_name, duration_ms, is_explicit, disc_number, track_number, is_local, isrc, album_id,
        album_name, track_artists, album_artists
    from {{ ref('stg_spotify__recently_played') }}

    union all

    select
        raw_response_id, item_ordinal, ingested_at, played_at_utc, content_uri, content_id, content_type,
        content_name, duration_ms, is_explicit, disc_number, track_number, is_local, isrc, album_id,
        album_name, track_artists, album_artists
    from {{ ref('stg_spotify__track') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by content_uri
            order by ingested_at desc, played_at_utc desc nulls last, raw_response_id desc, item_ordinal
        ) as recency_rank
    from observations
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
    ranked.content_uri,
    ranked.content_id,
    ranked.content_type,
    ranked.content_name,
    ranked.duration_ms,
    ranked.is_explicit,
    ranked.disc_number,
    ranked.track_number,
    ranked.is_local,
    ranked.isrc,
    ranked.album_id,
    ranked.album_name,
    ranked.track_artists,
    ranked.album_artists,
    seen.first_seen_at,
    seen.last_seen_at
from ranked
left join seen
    on seen.content_uri = ranked.content_uri
where ranked.recency_rank = 1
