-- dim_genre (data-contracts §3, spot-main-R-024). One row per distinct observed string per tag_type, ever.
-- A string MusicBrainz lists both as a genre and as a tag gets two rows: they are different vocabularies,
-- allocated separately in br_artist_genre. No bucket mapping here: that is R-015's, once Marc has seen this list.
with observed as (
    select distinct
        tag_type,
        tag_name
    from {{ ref('int_musicbrainz__artist_tags') }}
),

resolved as (
    select
        (row_number() over (order by tag_type, tag_name))::int as genre_key,
        tag_name as genre_name,
        tag_type,
        'musicbrainz'::text as genre_source
    from observed
),

unknown_member as (
    select
        -1 as genre_key,
        '(unknown)'::text as genre_name,
        'unknown'::text as tag_type,
        null::text as genre_source
)

select * from resolved
union all
select * from unknown_member
