-- One row per (MusicBrainz artist response, tag_type, tag string) for every observation ever stored
-- (spot-main-R-024). tag_type 'genre' is MusicBrainz's curated genre list; 'tag' is the open folksonomy.
-- The two are kept apart: a genre usually also appears as a tag, and mixing them double-counts it.
-- Only strings with a positive net vote count: MusicBrainz tags can be voted below zero, which means the
-- community rejected them (none observed at R-024, 0 of 0 non-positive; the filter is for the future).
-- Strings are kept exactly as MusicBrainz spells them: R-015 re-bases the taxonomy on real strings.
with responses as (
    select *
    from {{ ref('stg_musicbrainz__artist') }}
    where is_found
),

genres as (
    select
        responses.raw_response_id,
        responses.artist_mbid,
        responses.ingested_at,
        'genre'::text as tag_type,
        entry.value ->> 'name' as tag_name,
        (entry.value ->> 'count')::int as vote_count
    from responses
    cross join lateral jsonb_array_elements(responses.genres) as entry (value)
),

tags as (
    select
        responses.raw_response_id,
        responses.artist_mbid,
        responses.ingested_at,
        'tag'::text as tag_type,
        entry.value ->> 'name' as tag_name,
        (entry.value ->> 'count')::int as vote_count
    from responses
    cross join lateral jsonb_array_elements(responses.tags) as entry (value)
)

select * from genres
where tag_name is not null and vote_count > 0
union all
select * from tags
where tag_name is not null and vote_count > 0
