-- One export file is loaded once, in one batch (spot-main-R-044).
--
-- 🚨 Caught for real, 2026-09-18 05:52:36Z, while the round that wrote this test was running.
--
-- `raw.export_record` is unique on (profile_slug, source_file, record_index) and `export.load_file`
-- inserts with `on conflict ... do nothing`. That makes re-loading the SAME file a clean no-op, which
-- is the intended idempotence — `make load-export` advertises "re-run inserts 0".
--
-- It does something else entirely when the file is DIFFERENT. Loading the correct export over a wrong
-- one keeps every colliding row and accepts only the positions the wrong file never reached. Measured
-- here: `brody` held 145,056 records from the wrong export, a second load added **91**, and three
-- files ended up holding the first N records from one person and the rest from another under
-- contiguous record_index:
--
--     Streaming_History_Video_2022.json    1 from load 1, 24 from load 2
--     Streaming_History_Video_2024.json   30 from load 1,  5 from load 2
--     Streaming_History_Video_2026.json   38 from load 1, 62 from load 2
--
-- 🚨 Why this test exists SEPARATELY from export_profiles_are_not_duplicates: that test compares whole
-- profiles and passed the moment this happened, because the 91 extra rows made brody and emma differ.
-- The corruption defeated the guard written for it, in the same hour, and the build went green. An
-- exact-duplicate check cannot see a partial overwrite; this one can, because it asks a question about
-- provenance rather than about content.
--
-- The threshold is time, not row identity: one file is written by a single COPY, so its rows land
-- within seconds (a 145,056-row load spanned 00:09:17 to 00:09:21). Anything spread across more than a
-- minute is two loads wearing one filename.
with file_loads as (
    select
        profile_slug,
        source_file,
        count(*) as records,
        min(ingested_at) as first_ingested_at,
        max(ingested_at) as last_ingested_at,
        max(ingested_at) - min(ingested_at) as ingest_span
    from {{ source('raw', 'export_record') }}
    group by profile_slug, source_file
)

select
    profile_slug,
    source_file,
    records,
    first_ingested_at,
    last_ingested_at,
    ingest_span
from file_loads
where ingest_span > interval '1 minute'
