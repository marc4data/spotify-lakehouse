-- Required test 3 (R-004): the reconciliation chain adds up. For every step n > 1, what step n-1 held
-- equals what step n kept plus the residual step n names — for play counts and for ms_played.
with steps as (
    select * from {{ ref('int_allocation_reconciliation') }}
)

select
    cur.profile_slug,
    cur.step_name,
    prev.play_count as previous_play_count,
    cur.play_count,
    cur.residual_play_count,
    prev.ms_played as previous_ms_played,
    cur.ms_played,
    cur.residual_ms_played
from steps as cur
inner join steps as prev
    on prev.profile_slug = cur.profile_slug
    and prev.step_number = cur.step_number - 1
where prev.play_count <> cur.play_count + cur.residual_play_count
    or coalesce(prev.ms_played, 0) <> coalesce(cur.ms_played, 0) + coalesce(cur.residual_ms_played, 0)
