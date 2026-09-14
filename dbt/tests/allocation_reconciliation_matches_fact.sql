-- The reconciliation's first step must account for every row in fct_play_event, per profile, so the
-- chain R-009 prints starts from the same total the fact table holds.
with reconciliation_total as (
    select profile_slug, play_count
    from {{ ref('int_allocation_reconciliation') }}
    where step_number = 1
),

fact_total as (
    select profiles.profile_slug, count(*) as play_count
    from {{ ref('fct_play_event') }} as fct
    inner join {{ ref('dim_profile') }} as profiles
        on profiles.profile_key = fct.profile_key
    group by profiles.profile_slug
)

select
    coalesce(r.profile_slug, f.profile_slug) as profile_slug,
    r.play_count as reconciliation_play_count,
    f.play_count as fact_play_count
from reconciliation_total as r
full outer join fact_total as f
    on f.profile_slug = r.profile_slug
where r.play_count is distinct from f.play_count
