-- dim_content, supertype, SCD Type 1 (data-contracts §3). API content is resolved by construction;
-- unresolved export content (R-003) will add rows here with is_resolved = false, never unknown members.
with tracks as (
    select * from {{ ref('int_tracks__latest') }}
),

resolved as (
    select
        (row_number() over (order by first_seen_at, content_uri))::int as content_key,
        content_uri,
        content_type,
        content_name,
        album_name as parent_name,
        album_artists -> 0 ->> 'name' as primary_creator_name,
        duration_ms,
        true as is_resolved,
        first_seen_at,
        last_seen_at,
        -- §5 tier-3 loose match key
        lower(trim(content_name)) || '|' || lower(trim(album_artists -> 0 ->> 'name')) as content_match_key
    from tracks
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
