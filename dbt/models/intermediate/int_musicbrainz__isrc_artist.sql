-- One row per ISRC looked up: its latest MusicBrainz answer reduced to the primary artist (spot-main-R-024).
-- The primary artist is the first artist credit of each recording. An ISRC can map to several recordings;
-- it resolves only when every recording names the same first-credited artist. Disagreement is a coverage
-- gap, never a guess.
with latest as (
    select distinct on (isrc)
        *
    from {{ ref('stg_musicbrainz__isrc_lookup') }}
    order by isrc, ingested_at desc, raw_response_id desc
),

recordings as (
    select
        latest.isrc,
        jsonb_array_length(coalesce(recording.value -> 'artist-credit', '[]'::jsonb)) as credit_count,
        recording.value -> 'artist-credit' -> 0 -> 'artist' ->> 'id' as first_credit_mbid
    from latest
    cross join lateral jsonb_array_elements(latest.recordings) as recording (value)
),

per_isrc as (
    select
        isrc,
        count(*) as recording_count,
        count(*) filter (where credit_count > 0) as recordings_with_credit,
        count(distinct first_credit_mbid) filter (
            where first_credit_mbid ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        ) as distinct_primary_mbids,
        min(first_credit_mbid) as any_primary_mbid
    from recordings
    group by isrc
)

select
    latest.isrc,
    latest.is_found,
    coalesce(per_isrc.recording_count, 0) as recording_count,
    coalesce(per_isrc.recordings_with_credit, 0) as recordings_with_credit,
    coalesce(per_isrc.distinct_primary_mbids, 0) as distinct_primary_mbids,
    case when per_isrc.distinct_primary_mbids = 1 then per_isrc.any_primary_mbid end as primary_artist_mbid
from latest
left join per_isrc
    on per_isrc.isrc = latest.isrc
