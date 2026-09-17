-- int_playlist_content: one row per distinct track ever seen in a playlist (spot-main-R-054).
--
-- The mirror of `int_export_content`. A track is content whether or not anyone played it: R-054
-- measured **638 distinct playlist tracks with no `dim_content` row** (14.22% of 4,697 membership
-- rows), 625 of them carrying an ISRC, only 13 local files. Dropping them would have answered Marc's
-- question wrong — a track in one playlist and not the other is exactly the row he is looking for.
--
-- Attributes come from the most recent observation, like every other __latest model.
--
-- `content_match_key` is order-invariant (spot-main-R-060), built the same way as in
-- `int_export_content` even though this source needs no rescuing: measured 2026-09-16, **0** playlist
-- URIs disagree on (content_name, primary_creator_name) and **0** can tie on
-- (content_uri, ingested_at, item_ordinal). It is written this way so the property holds by
-- construction rather than by the payload happening to be consistent today.
select distinct on (content_uri)
    content_uri,
    content_type,
    content_name,
    parent_name,
    primary_creator_name,
    duration_ms,
    isrc,
    is_local,
    ingested_at as observed_at,
    min({{ content_match_key('content_name', 'primary_creator_name') }})
        over (partition by content_uri) as content_match_key
from {{ ref('stg_spotify__playlist_item') }}
where content_uri is not null
order by content_uri, ingested_at desc, item_ordinal
