-- One row per track, episode or audiobook chapter URI seen in an export, described by the export's own metadata from its most
-- recent play (spot-main-R-003). dim_content uses it for content the API has never resolved: data-contracts §3
-- requires such content to get a real row with is_resolved = false, never an unknown member.
--
-- ⚠️ `content_match_key` is deliberately NOT read off the winning row (spot-main-R-060). **This model is the one
-- place in the warehouse where the tier-3 key was genuinely order-dependent.** The export spells the same URI more
-- than one way, so the key moved with whichever play happened to be most recent. Measured before the fix:
--
--   * 71 URIs produce a DIFFERENT key if the recency ordering is reversed — a deterministic reproduction, not an
--     inference from the one case R-059 hit.
--   * 69 still carry more than one distinct key after folding, so the fold alone fixes only 8 of the 71. The rest
--     is Spotify relabelling the same URI ("- Remastered 2010" vs "- 2010 Remastered", classical movements losing
--     their work title, `Alan Menken` vs `Samuel E. Wright`), which no normalization can or should reconcile.
--   * The tie-break cannot tie: 0 rows share (content_uri, ended_at_utc, raw_record_id).
--
-- So the key is the MINIMUM folded key over every observation of the URI. `min()` is order-invariant by
-- construction, which is why the fix is selection rather than normalization. The display attributes below still
-- come from the most recent play — only the key is a pure function of the content.
with plays as (
    select
        *,
        row_number() over (
            partition by content_uri
            order by ended_at_utc desc, raw_record_id desc
        ) as recency_rank,
        min(
            {{ content_match_key(
                'coalesce(track_name, episode_name, audiobook_chapter_title)',
                'album_artist_name'
            ) }}
        ) over (partition by content_uri) as content_match_key
    from {{ ref('stg_export__play_record') }}
    where content_type in ('track', 'episode', 'audiobook_chapter')
)

select
    content_uri,
    content_type,
    coalesce(track_name, episode_name, audiobook_chapter_title) as content_name,
    coalesce(album_name, episode_show_name, audiobook_title) as parent_name,
    album_artist_name as primary_creator_name,  -- episodes: the export carries no show publisher
    content_match_key
from plays
where recency_rank = 1
