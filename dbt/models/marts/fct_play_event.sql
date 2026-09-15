-- fct_play_event (data-contracts §2). Grain: one row per completed play event, per profile.
-- date_key and time_of_day_key come from ended_at_utc converted to the profile's home timezone, never UTC.
-- Lookups that fail resolve to the -1 unknown members rather than dropping the play.
with plays as (
    select * from {{ ref('int_play_events__deduped') }}
),

profiles as (
    select profile_key, profile_slug, home_timezone
    from {{ ref('dim_profile') }}
    where profile_key > 0
),

contents as (
    select content_key, content_uri, duration_ms
    from {{ ref('dim_content') }}
    where content_key > 0
),

localized as (
    select
        plays.*,
        profiles.profile_key,
        contents.content_key,
        contents.duration_ms as content_duration_ms,
        plays.ended_at_utc at time zone profiles.home_timezone as ended_at_local
    from plays
    left join profiles
        on profiles.profile_slug = plays.profile_slug
    left join contents
        on contents.content_uri = plays.content_uri
)

select
    -- Stable 64-bit surrogate from the natural key, so a rebuild never renumbers events.
    ('x' || substr(md5(
        profile_slug || '|' || content_uri || '|'
        || to_char(date_trunc('minute', ended_at_utc) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI')
    ), 1, 16))::bit(64)::bigint as play_event_key,
    coalesce(profile_key, -1) as profile_key,
    coalesce(content_key, -1) as content_key,
    coalesce(to_char(ended_at_local, 'YYYYMMDD')::int, -1) as date_key,
    coalesce(
        (extract(hour from ended_at_local) * 100 + extract(minute from ended_at_local))::int,
        -1
    ) as time_of_day_key,
    ended_at_utc,
    ms_played,
    is_ms_played_imputed,
    -- §2 "what counts as a listen": qualified_play_count = count(*) filter (where is_qualified_play).
    -- ms_played >= 30 s, or >= half the content's duration. NULL ms_played (every API row) never qualifies;
    -- the half-duration clause needs a known duration, which export-only content does not have.
    coalesce(
        ms_played >= 30000 or ms_played >= 0.5 * content_duration_ms,
        false
    ) as is_qualified_play,
    reason_start,
    reason_end,
    was_skipped,
    was_shuffled,
    was_offline,
    platform,
    conn_country,
    source_system,
    source_file
from localized
