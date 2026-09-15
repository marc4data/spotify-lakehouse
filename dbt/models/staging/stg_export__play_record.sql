-- One row per record in a Spotify Extended Streaming History export (spot-main-R-003, data-contracts §2).
-- ip_addr and user_agent never reach this view: they are discarded at ingest and never written to raw.
-- content_type: a record carries exactly one of spotify_track_uri, spotify_episode_uri, audiobook_chapter_uri.
-- Audiobook chapters are typed here but have no home in dim_content's contract (track | episode); see
-- int_play_events__unioned. offline_timestamp is kept as received: its unit varies (seconds and ms both seen).
with source as (
    select *
    from {{ source('raw', 'export_record') }}
),

renamed as (
    select
        id as raw_record_id,
        profile_slug,
        source_file,
        record_index,
        ingested_at,
        (payload ->> 'ts')::timestamptz as ended_at_utc,
        (payload ->> 'ms_played')::int as ms_played,
        case
            when payload ->> 'spotify_track_uri' is not null then 'track'
            when payload ->> 'spotify_episode_uri' is not null then 'episode'
            when payload ->> 'audiobook_chapter_uri' is not null then 'audiobook_chapter'
        end as content_type,
        coalesce(
            payload ->> 'spotify_track_uri',
            payload ->> 'spotify_episode_uri',
            payload ->> 'audiobook_chapter_uri'
        ) as content_uri,
        payload ->> 'master_metadata_track_name' as track_name,
        payload ->> 'master_metadata_album_album_name' as album_name,
        payload ->> 'master_metadata_album_artist_name' as album_artist_name,
        payload ->> 'episode_name' as episode_name,
        payload ->> 'episode_show_name' as episode_show_name,
        payload ->> 'audiobook_title' as audiobook_title,
        payload ->> 'audiobook_chapter_title' as audiobook_chapter_title,
        nullif(payload ->> 'reason_start', '') as reason_start,
        nullif(payload ->> 'reason_end', '') as reason_end,
        (payload ->> 'skipped')::boolean as was_skipped,
        (payload ->> 'shuffle')::boolean as was_shuffled,
        (payload ->> 'offline')::boolean as was_offline,
        (payload ->> 'incognito_mode')::boolean as was_incognito,
        (payload ->> 'offline_timestamp')::bigint as offline_timestamp_raw,
        payload ->> 'platform' as platform,
        payload ->> 'conn_country' as conn_country
    from source
)

select * from renamed
-- Structurally invalid rows only: a record with no timestamp or no content uri cannot be a play.
where ended_at_utc is not null
    and content_uri is not null
