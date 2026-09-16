-- One row per playlist per stored /me/playlists page (spot-main-R-054). The page is exploded, so a
-- playlist appears once per fetch and `snapshot_date` carries the day it was observed.
--
-- 🚨 No owner column, by design. `raw_store` discards `items[].owner.*` before anything is written
-- (R-054): a playlist names a person who is not Marc, and discarding beats redacting because a field
-- never written cannot leak from a table nobody thought to check. data-contracts §3 contracts
-- `dim_playlist` as Type 2 *on owner*, which cannot be built from this — reported as a finding.
with source as (
    select
        id as raw_response_id,
        profile_slug,
        ingested_at,
        source_file,
        payload
    from {{ source('raw', 'api_response') }}
    where feed = 'playlist'
),

exploded as (
    select
        source.raw_response_id,
        source.profile_slug,
        source.ingested_at,
        source.source_file,
        item.ordinality::int as item_ordinal,
        item.value as playlist
    from source
    cross join lateral jsonb_array_elements(
        coalesce(source.payload -> 'items', '[]'::jsonb)
    ) with ordinality as item (value, ordinality)
)

select
    raw_response_id,
    profile_slug,
    ingested_at,
    ingested_at::date as snapshot_date,
    source_file,
    item_ordinal,
    playlist ->> 'id' as playlist_id,
    playlist ->> 'uri' as playlist_uri,
    playlist ->> 'name' as playlist_name,
    nullif(playlist ->> 'description', '') as description,
    (playlist ->> 'collaborative')::boolean as is_collaborative,
    (playlist ->> 'public')::boolean as is_public,
    playlist ->> 'snapshot_id' as snapshot_id,
    -- R-008 F3, re-measured R-054 on stored payloads: the playlist object carries `items`, not
    -- `tracks`. Both are read so either shape lands, and neither is assumed from documentation.
    coalesce(
        (playlist -> 'items' ->> 'total')::int,
        (playlist -> 'tracks' ->> 'total')::int
    ) as track_total
from exploded
-- structurally invalid rows only
where playlist ->> 'id' is not null
