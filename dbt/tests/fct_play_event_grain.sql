-- Required test 1 (R-004): fct_play_event is unique on
-- (profile_key, content_key, date_trunc('minute', ended_at_utc)) — data-contracts §2 natural key.
-- Returns one row per violating key; any row fails the test.
select
    profile_key,
    content_key,
    date_trunc('minute', ended_at_utc) as ended_minute_utc,
    count(*) as rows_at_key
from {{ ref('fct_play_event') }}
group by profile_key, content_key, date_trunc('minute', ended_at_utc)
having count(*) > 1
