-- dim_playlist, SCD Type 2 (data-contracts §3): a new version whenever a tracked attribute changes.
--
-- 🚨 No owner column, and none is coming: owner identity is discarded at ingest because a playlist's
-- owner is a person who is not Marc and the repo is public. What this model carries instead is
-- `is_owned_by_profile`, a boolean `scrub` computes from `owner.id` before discarding it (R-058).
-- §3's "Type 2 on owner" is amended to name the boolean — this round's one sanctioned amendment.
-- It is a tracked attribute: a playlist changing hands starts a new version.
with observations as (
    select distinct on (playlist_id, ingested_at)
        playlist_id,
        ingested_at,
        snapshot_date,
        playlist_uri,
        playlist_name,
        description,
        is_owned_by_profile,
        is_collaborative,
        is_public,
        snapshot_id,
        track_total
    from {{ ref('stg_spotify__playlist') }}
    order by playlist_id, ingested_at, item_ordinal
),

hashed as (
    select
        *,
        md5(
            coalesce(playlist_name, '') || '|' || coalesce(description, '') || '|'
            || coalesce(is_owned_by_profile::text, '') || '|'
            || coalesce(is_collaborative::text, '') || '|' || coalesce(is_public::text, '') || '|'
            || coalesce(track_total::text, '')
        ) as attribute_hash
    from observations
),

marked as (
    select
        *,
        lag(attribute_hash) over (partition by playlist_id order by ingested_at) as previous_hash
    from hashed
),

version_starts as (
    select *
    from marked
    where previous_hash is null or previous_hash <> attribute_hash
),

spans as (
    select
        *,
        lead(ingested_at) over (partition by playlist_id order by ingested_at) as valid_to
    from version_starts
),

resolved as (
    select
        (row_number() over (order by playlist_id, ingested_at))::int as playlist_key,
        playlist_id,
        playlist_uri,
        playlist_name,
        description,
        is_owned_by_profile,
        is_collaborative,
        is_public,
        snapshot_id,
        track_total,
        ingested_at as valid_from,
        valid_to,
        (valid_to is null) as is_current
    from spans
),

unknown_member as (
    select
        -1 as playlist_key,
        '(unknown)'::text as playlist_id,
        null::text as playlist_uri,
        'Unknown playlist'::text as playlist_name,
        null::text as description,
        null::boolean as is_owned_by_profile,
        null::boolean as is_collaborative,
        null::boolean as is_public,
        null::text as snapshot_id,
        null::int as track_total,
        null::timestamptz as valid_from,
        null::timestamptz as valid_to,
        false as is_current
)

select * from resolved
union all
select * from unknown_member
