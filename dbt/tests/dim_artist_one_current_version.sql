-- SCD2 integrity (R-024): every artist has exactly one current version, and versions never overlap.
with versions as (
    select * from {{ ref('dim_artist') }}
),

current_count as (
    select artist_id, 'current versions = ' || count(*) filter (where is_current) as problem
    from versions
    group by artist_id
    having count(*) filter (where is_current) <> 1
),

overlapping as (
    select a.artist_id, 'overlapping versions ' || a.artist_key || ' and ' || b.artist_key as problem
    from versions as a
    inner join versions as b
        on b.artist_id = a.artist_id
        and b.artist_key > a.artist_key
        and tstzrange(a.valid_from, a.valid_to) && tstzrange(b.valid_from, b.valid_to)
)

select * from current_count
union all
select * from overlapping
