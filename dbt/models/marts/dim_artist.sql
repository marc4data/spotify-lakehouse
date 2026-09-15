-- dim_artist, SCD Type 2 on the sourced genres plus genre_source (data-contracts §3, spot-main-R-024).
--
-- History is rebuilt from raw.external_response, not kept in a dbt snapshot. raw is append-only, so every
-- MusicBrainz observation of an artist is already there with its ingested_at; a snapshot would hold a second
-- copy of that history in one session's schema, and a fresh `make worktree` would start with none of it.
-- Built this way, every session derives the same versions from the shared raw schema.
--
-- A version changes when the SET of genre/tag strings (or genre_source) changes. Vote counts are not part of
-- it: they move on every vote and would mint a version per vote. Name stays Type 1 (current value on every
-- version). The first version is valid from Spotify's launch (the start of dim_date); genre_observed_at is
-- when the observation that established the version was taken.
with artists as (
    select * from {{ ref('int_artists__latest') }}
),

mapping as (
    select * from {{ ref('int_artist_musicbrainz') }}
    where artist_mbid is not null
),

signatures as (
    select
        raw_response_id,
        string_agg(tag_type || ':' || tag_name, '|' order by tag_type, tag_name) as genre_signature
    from {{ ref('int_musicbrainz__artist_tags') }}
    group by raw_response_id
),

observations as (
    select
        mapping.artist_id,
        mapping.artist_mbid,
        responses.raw_response_id,
        responses.ingested_at,
        coalesce(signatures.genre_signature, '') as genre_signature
    from mapping
    inner join {{ ref('stg_musicbrainz__artist') }} as responses
        on responses.artist_mbid = mapping.artist_mbid
    left join signatures
        on signatures.raw_response_id = responses.raw_response_id
),

changes as (
    select
        *,
        case
            when lag(genre_signature) over w is null or lag(genre_signature) over w <> genre_signature
                then 1
            else 0
        end as is_change
    from observations
    window w as (partition by artist_id order by ingested_at, raw_response_id)
),

islands as (
    select
        *,
        sum(is_change) over (partition by artist_id order by ingested_at, raw_response_id) as version_number
    from changes
),

versions as (
    select
        artist_id,
        min(artist_mbid) as musicbrainz_artist_id,
        version_number,
        min(ingested_at) as genre_observed_at,
        (array_agg(raw_response_id order by ingested_at, raw_response_id))[1] as genre_raw_response_id,
        min(genre_signature) as genre_signature
    from islands
    group by artist_id, version_number
),

bounded as (
    select
        *,
        case
            when version_number = 1 then '2008-10-07 00:00:00+00'::timestamptz
            else genre_observed_at
        end as valid_from,
        lead(genre_observed_at) over (partition by artist_id order by version_number) as valid_to
    from versions
),

all_versions as (
    select
        artists.artist_id,
        artists.artist_uri,
        artists.artist_name,
        bounded.musicbrainz_artist_id,
        case when bounded.genre_signature <> '' then 'musicbrainz' end as genre_source,
        bounded.genre_raw_response_id,
        bounded.genre_observed_at,
        coalesce(bounded.valid_from, '2008-10-07 00:00:00+00'::timestamptz) as valid_from,
        bounded.valid_to
    from artists
    left join bounded
        on bounded.artist_id = artists.artist_id
),

resolved as (
    select
        (row_number() over (order by artist_id, valid_from))::int as artist_key,
        artist_id,
        artist_uri,
        artist_name,
        genre_source,
        musicbrainz_artist_id,
        genre_raw_response_id,
        genre_observed_at,
        valid_from,
        valid_to,
        valid_to is null as is_current
    from all_versions
),

unknown_member as (
    select
        -1 as artist_key,
        '(unknown)'::text as artist_id,
        null::text as artist_uri,
        'Unknown artist'::text as artist_name,
        null::text as genre_source,
        null::text as musicbrainz_artist_id,
        null::bigint as genre_raw_response_id,
        null::timestamptz as genre_observed_at,
        '2008-10-07 00:00:00+00'::timestamptz as valid_from,
        null::timestamptz as valid_to,
        true as is_current
)

select * from resolved
union all
select * from unknown_member
