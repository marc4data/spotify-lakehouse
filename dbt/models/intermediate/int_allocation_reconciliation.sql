-- Allocation reconciliation, data-contracts §4. One row per (profile, step).
-- Each step names the residual it lost from the step before, so for every step n > 1:
--   step[n-1].play_count = step[n].play_count + step[n].residual_play_count   (and the same for ms)
-- Step 4 counts a play as genre-allocated when its primary artist currently has at least one MusicBrainz
-- string of the tag_type in var('allocation_tag_type') (default `genre`, the curated list; `tag` is the
-- folksonomy). The two vocabularies are parallel allocations (br_artist_genre), so the chain follows one.
-- ms_played is NULL for every API row; play counts carry the chain meanwhile, and pct_rows_with_duration
-- shows the coverage (§2).
with artists_with_genre as (
    select distinct artist_id
    from {{ ref('int_artist_genres__current') }}
    where tag_type = '{{ var("allocation_tag_type") }}'
),

plays as (
    select
        plays.profile_slug,
        plays.ms_played,
        coalesce(plays.content_type, 'unknown') as content_type,
        coalesce(artists.is_fetched, false) as is_primary_artist_resolved,
        artists_with_genre.artist_id is not null as has_genre
    from {{ ref('int_play_events__deduped') }} as plays
    left join {{ ref('int_content_artists') }} as primary_artist
        on primary_artist.content_uri = plays.content_uri
        and primary_artist.is_primary
    left join {{ ref('int_artists__latest') }} as artists
        on artists.artist_id = primary_artist.artist_id
    left join artists_with_genre
        on artists_with_genre.artist_id = primary_artist.artist_id
),

flagged as (
    select
        profile_slug,
        ms_played,
        true as in_total,
        content_type = 'track' as in_music,
        content_type = 'track' and is_primary_artist_resolved as in_artist_resolved,
        content_type = 'track' and is_primary_artist_resolved and has_genre as in_genre_allocated
    from plays
),

steps as (
    select profile_slug, 1 as step_number, 'total_listening' as step_name,
        in_total as in_step, false as in_residual, null::text as residual_name, ms_played
    from flagged
    union all
    select profile_slug, 2, 'music',
        in_music, in_total and not in_music, 'not_music (episodes and unknown content)', ms_played
    from flagged
    union all
    select profile_slug, 3, 'artist_resolved',
        in_artist_resolved, in_music and not in_artist_resolved, 'primary_artist_unresolved', ms_played
    from flagged
    union all
    select profile_slug, 4, 'genre_allocated',
        in_genre_allocated, in_artist_resolved and not in_genre_allocated,
        'unclassified (primary artist has no MusicBrainz {{ var("allocation_tag_type") }})', ms_played
    from flagged
)

select
    profile_slug,
    step_number,
    step_name,
    count(*) filter (where in_step) as play_count,
    sum(ms_played) filter (where in_step) as ms_played,
    count(ms_played) filter (where in_step) as rows_with_duration,
    round(
        100.0 * count(ms_played) filter (where in_step)
        / nullif(count(*) filter (where in_step), 0),
        2
    ) as pct_rows_with_duration,
    residual_name,
    count(*) filter (where in_residual) as residual_play_count,
    sum(ms_played) filter (where in_residual) as residual_ms_played
from steps
group by profile_slug, step_number, step_name, residual_name
