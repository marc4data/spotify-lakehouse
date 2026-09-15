-- The radar and the reconciliation it prints must describe the same time (R-015). Per profile, the bucket
-- allocation's ms_played must equal int_allocation_reconciliation step 4 (genre_allocated): weights sum to 1 per
-- artist within the tag_type, so allocating across genres and buckets neither creates nor loses time.
with allocation as (
    select
        profile_slug,
        sum(allocated_ms) as allocated_ms
    from {{ ref('int_genre_bucket_allocation') }}
    group by profile_slug
),

reconciliation as (
    select
        profile_slug,
        ms_played
    from {{ ref('int_allocation_reconciliation') }}
    where step_number = 4
)

select
    coalesce(allocation.profile_slug, reconciliation.profile_slug) as profile_slug,
    allocation.allocated_ms,
    reconciliation.ms_played as step4_ms
from allocation
full outer join reconciliation
    on reconciliation.profile_slug = allocation.profile_slug
where abs(coalesce(allocation.allocated_ms, 0) - coalesce(reconciliation.ms_played, 0)) > 1
