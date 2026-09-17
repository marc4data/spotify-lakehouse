-- dim_content, supertype, SCD Type 1 (data-contracts §3). API-resolved tracks carry the API's attributes and
-- is_resolved = true. Content known only from an export (R-003) gets a real row from the export's own metadata
-- with is_resolved = false: never dropped, never routed to an unknown member. first/last seen span every source.
with api_tracks as (
    select * from {{ ref('int_tracks__latest') }}
),

seen as (
    select
        content_uri,
        min(ended_at_utc) as first_seen_at,
        max(ended_at_utc) as last_seen_at
    from {{ ref('int_play_events__unioned') }}
    group by content_uri
),

combined as (
    select
        content_uri,
        content_type,
        content_name,
        album_name as parent_name,
        album_artists -> 0 ->> 'name' as primary_creator_name,
        duration_ms,
        true as is_resolved
    from api_tracks

    union all

    select
        export_content.content_uri,
        export_content.content_type,
        export_content.content_name,
        export_content.parent_name,
        export_content.primary_creator_name,
        null::int as duration_ms,
        false as is_resolved
    from {{ ref('int_export_content') }} as export_content
    where not exists (
        select 1 from api_tracks where api_tracks.content_uri = export_content.content_uri
    )

    union all

    -- Third source (spot-main-R-054): a playlist can contain tracks nobody has played. Measured
    -- before deciding — 638 distinct playlist tracks had no row here, 14.22% of membership rows —
    -- and measured again afterwards: adding them moves **0** existing content_key values, because
    -- they carry no first_seen_at and sort to the end of the row_number() ordering below.
    select
        playlist_content.content_uri,
        playlist_content.content_type,
        playlist_content.content_name,
        playlist_content.parent_name,
        playlist_content.primary_creator_name,
        playlist_content.duration_ms,
        false as is_resolved
    from {{ ref('int_playlist_content') }} as playlist_content
    where not exists (
        select 1 from api_tracks where api_tracks.content_uri = playlist_content.content_uri
    )
    and not exists (
        select 1 from {{ ref('int_export_content') }} as export_content
        where export_content.content_uri = playlist_content.content_uri
    )
),

match_keys as (
    -- The tier-3 key, taken across EVERY branch that has seen this URI rather than from the one the
    -- union order happens to favour (spot-main-R-060).
    --
    -- 🚨 Taking the winning branch's key would have swapped one order-dependence for another. Measured
    -- 2026-09-16, the branches disagree on the folded key for **17** URIs (api vs export), **19**
    -- (api vs playlist) and **32** (export vs playlist) — so a track would change key the day the API
    -- first resolved it, which is the same defect wearing a different hat. Each branch already exposes
    -- an order-invariant `min()` over its own observations, so the min of those mins is the min over
    -- all of them: a pure function of the set of observations, independent of arrival order, of
    -- recency, and of which branch got there first.
    --
    -- No `not exists` filters here, unlike `combined` above: every branch contributes, including ones
    -- whose row loses the union.
    select
        content_uri,
        min(content_match_key) as content_match_key
    from (
        select content_uri, content_match_key from api_tracks
        union all
        select content_uri, content_match_key from {{ ref('int_export_content') }}
        union all
        select content_uri, content_match_key from {{ ref('int_playlist_content') }}
    ) as every_branch
    group by content_uri
),

resolved as (
    select
        (row_number() over (order by seen.first_seen_at, combined.content_uri))::int as content_key,
        combined.content_uri,
        combined.content_type,
        combined.content_name,
        combined.parent_name,
        combined.primary_creator_name,
        combined.duration_ms,
        combined.is_resolved,
        seen.first_seen_at,
        seen.last_seen_at,
        -- §5 tier-3 loose match key: see match_keys above for why it is not computed from the
        -- winning row's name.
        match_keys.content_match_key
    from combined
    left join seen
        on seen.content_uri = combined.content_uri
    left join match_keys
        on match_keys.content_uri = combined.content_uri
),

unknown_members as (
    select
        -1 as content_key,
        '(absent)'::text as content_uri,
        'unknown'::text as content_type,
        'Unknown content'::text as content_name,
        null::text as parent_name,
        null::text as primary_creator_name,
        null::int as duration_ms,
        false as is_resolved,
        null::timestamptz as first_seen_at,
        null::timestamptz as last_seen_at,
        null::text as content_match_key
    union all
    select -2, '(pending)', 'unknown', 'Pending resolution', null, null, null, false, null, null, null
)

select * from resolved
union all
select * from unknown_members
