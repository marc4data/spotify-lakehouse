-- fct_playlist_membership, periodic snapshot (data-contracts §4).
-- Grain: one row per (snapshot_date, playlist_key, content_key).
--
-- 🚨 No `added_by_profile_key`. §4 contracts it for collaborative playlists; R-054 discards
-- `items[].added_by` at ingest because it names a different person on every row of a collaborative
-- playlist, and Marc has not asked who added what (R-044 already rules that "who introduced whom" is
-- an inference, not a measurement). §4's row is amended to state what was built — one of the two
-- amendments this round is sanctioned to make.
--
-- 🚨 `content_key` is NOT coalesced to the -2 pending member. A playlist can contain tracks Marc has
-- never played, and collapsing every one of them onto a single key would make them indistinguishable
-- — which is precisely the row Marc's question is hunting ("in Smith, not in Connor"). The orphan
-- count is measured and reported, and `dim_content` is the place that changes, not this join.
with items as (
    select distinct on (snapshot_date, playlist_id, content_uri)
        snapshot_date,
        playlist_id,
        content_uri,
        position,
        added_at,
        content_name,
        parent_name,
        primary_creator_name,
        isrc,
        is_local
    from {{ ref('stg_spotify__playlist_item') }}
    order by snapshot_date, playlist_id, content_uri, position
)

select
    items.snapshot_date,
    coalesce(playlist.playlist_key, -1) as playlist_key,
    content.content_key,
    items.content_uri,
    items.position,
    items.added_at,
    items.content_name,
    items.primary_creator_name,
    items.isrc,
    items.is_local
from items
left join {{ ref('dim_playlist') }} as playlist
    on playlist.playlist_id = items.playlist_id
    and playlist.is_current
left join {{ ref('dim_content') }} as content
    on content.content_uri = items.content_uri
