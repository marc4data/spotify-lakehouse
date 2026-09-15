-- Required test 1 (R-004), re-keyed by R-037: fct_play_event is unique on
-- (profile_key, content_key, date_trunc('second', ended_at_utc)). Since the dedupe key splits by source pair
-- (data-contracts §2), two export plays of one track in one minute are two events; the minute rule for
-- export-vs-api and api-vs-api is checked by tests/play_events_dedupe_by_source_pair.sql.
-- Returns one row per violating key; any row fails the test.
select
    profile_key,
    content_key,
    date_trunc('second', ended_at_utc) as ended_second_utc,
    count(*) as rows_at_key
from {{ ref('fct_play_event') }}
group by profile_key, content_key, date_trunc('second', ended_at_utc)
having count(*) > 1
