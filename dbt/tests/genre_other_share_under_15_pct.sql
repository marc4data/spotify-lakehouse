-- data-contracts §3: "a dbt test fails the build when `other` exceeds 15% of allocated listening time" (R-015).
-- Silent drift into `other` is how a radar chart becomes a lie. Per profile, over all allocated time. An empty or
-- missing mapping sends every genre to `other`, which is 100%: this test fails rather than skipping (R-004 S2).
with shares as (
    select
        profile_slug,
        sum(allocated_ms) filter (where bucket_name = 'other') as other_ms,
        sum(allocated_ms) as allocated_ms
    from {{ ref('int_genre_bucket_allocation') }}
    group by profile_slug
)

select
    profile_slug,
    other_ms,
    allocated_ms,
    round(100.0 * other_ms / nullif(allocated_ms, 0), 2) as other_pct
from shares
where coalesce(other_ms, 0) > 0.15 * allocated_ms
