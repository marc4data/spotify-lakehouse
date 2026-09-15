-- One row per track, episode or audiobook chapter URI seen in an export, described by the export's own metadata from its most
-- recent play (spot-main-R-003). dim_content uses it for content the API has never resolved: data-contracts §3
-- requires such content to get a real row with is_resolved = false, never an unknown member.
with plays as (
    select
        *,
        row_number() over (
            partition by content_uri
            order by ended_at_utc desc, raw_record_id desc
        ) as recency_rank
    from {{ ref('stg_export__play_record') }}
    where content_type in ('track', 'episode', 'audiobook_chapter')
)

select
    content_uri,
    content_type,
    coalesce(track_name, episode_name, audiobook_chapter_title) as content_name,
    coalesce(album_name, episode_show_name, audiobook_title) as parent_name,
    album_artist_name as primary_creator_name  -- episodes: the export carries no show publisher
from plays
where recency_rank = 1
