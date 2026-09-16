-- One row per track per stored /playlists/{id}/items page (spot-main-R-054).
--
-- The playlist is read from the page's own `href`, which is a link to the playlist and carries no
-- identity. `added_by` is absent: `raw_store` discards `items[].added_by` at ingest, because on a
-- collaborative playlist it names a different person on every row (R-054). data-contracts §4
-- contracts `added_by_profile_key` on the fact; that row is amended to state what was built.
--
-- Both routes land here: R-008 F3 observed `/playlists/{id}/items` and the older
-- `/playlists/{id}/tracks` serving the same shape, and `feed_for_endpoint` maps both to
-- `playlist_item`.
with source as (
    select
        id as raw_response_id,
        profile_slug,
        ingested_at,
        source_file,
        payload,
        coalesce((payload ->> 'offset')::int, 0) as page_offset,
        substring(payload ->> 'href' from 'playlists/([A-Za-z0-9]+)/') as playlist_id
    from {{ source('raw', 'api_response') }}
    where feed = 'playlist_item'
),

exploded as (
    select
        source.raw_response_id,
        source.profile_slug,
        source.ingested_at,
        source.source_file,
        source.playlist_id,
        source.page_offset,
        item.ordinality::int as item_ordinal,
        item.value as entry
    from source
    cross join lateral jsonb_array_elements(
        coalesce(source.payload -> 'items', '[]'::jsonb)
    ) with ordinality as item (value, ordinality)
),

typed as (
    select
        raw_response_id,
        profile_slug,
        ingested_at,
        ingested_at::date as snapshot_date,
        source_file,
        playlist_id,
        item_ordinal,
        -- position within the playlist, not within the page
        page_offset + item_ordinal as position,
        nullif(entry ->> 'added_at', '')::timestamptz as added_at,
        -- R-008 F3, re-measured R-054 on stored payloads: each entry carries `item`, not `track`.
        -- Reading only `track` produced zero rows; both keys are read so either shape lands.
        coalesce(entry -> 'item', entry -> 'track') as track,
        coalesce(entry -> 'item', entry -> 'track') ->> 'uri' as content_uri,
        coalesce(entry -> 'item', entry -> 'track') ->> 'type' as content_type,
        coalesce(entry -> 'item', entry -> 'track') ->> 'name' as content_name,
        coalesce(entry -> 'item', entry -> 'track') -> 'album' ->> 'name' as parent_name,
        coalesce(entry -> 'item', entry -> 'track') -> 'external_ids' ->> 'isrc' as isrc,
        (coalesce(entry -> 'item', entry -> 'track') ->> 'duration_ms')::int as duration_ms,
        coalesce(entry -> 'item', entry -> 'track') -> 'artists' -> 0 ->> 'name'
            as primary_creator_name,
        (coalesce(entry -> 'item', entry -> 'track') ->> 'is_local')::boolean as is_local
    from exploded
)

select
    raw_response_id,
    profile_slug,
    ingested_at,
    snapshot_date,
    source_file,
    playlist_id,
    item_ordinal,
    position,
    added_at,
    content_uri,
    content_type,
    content_name,
    parent_name,
    primary_creator_name,
    isrc,
    duration_ms,
    is_local
from typed
-- a removed or unavailable track comes back as a null `track` object; it is not a row
where content_uri is not null
