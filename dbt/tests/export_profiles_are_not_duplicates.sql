-- Two people cannot have byte-identical listening histories (spot-main-R-044).
--
-- 🚨 Caught for real, 2026-09-17. `brody` and `emma` were both loaded from the SAME export file —
-- MD5 `821d0e1e…` on both zips, 145,056 records each, identical per-file row counts across all 20
-- source files, identical first and last play. One download had been re-served under the other
-- child's name, and nothing in the warehouse noticed.
--
-- Why this is a build-breaking test rather than a notebook caveat: R-044 is a HOUSEHOLD COMPARISON.
-- A duplicated profile does not degrade it, it inverts it — two children read as perfectly identical
-- listeners (Jaccard 1.0), every first-play precedence is a tie, and household hours double-count a
-- person who may not be in the data at all. The failure is invisible in every downstream chart
-- precisely because it is perfectly self-consistent. It has to be caught at the source.
--
-- The Extended Streaming History export carries NO account-identifying file — only the ReadMe PDF and
-- the history JSONs — so nothing inside the payload can say whose it is. The warehouse cannot
-- attribute a duplicate to the right person; it can only refuse to guess. That is why this test
-- names the pair and stops, rather than picking one.
--
-- The signature is per-record and order-independent: aggregated over a sorted list, so two profiles
-- whose records merely arrive in a different order still compare equal, and a genuine difference of a
-- single play breaks the match.
with signatures as (
    select
        profile_slug,
        md5(
            string_agg(
                coalesce(ended_at_utc::text, '')
                || '~' || coalesce(content_uri, '')
                || '~' || coalesce(ms_played::text, ''),
                '|' order by
                    coalesce(ended_at_utc::text, ''),
                    coalesce(content_uri, ''),
                    coalesce(ms_played::text, '')
            )
        ) as export_signature,
        count(*) as record_count,
        min(ended_at_utc) as coverage_start,
        max(ended_at_utc) as coverage_end
    from {{ ref('stg_export__play_record') }}
    group by profile_slug
)

select
    lower_slug.profile_slug,
    higher_slug.profile_slug as identical_to,
    lower_slug.export_signature,
    lower_slug.record_count,
    lower_slug.coverage_start,
    lower_slug.coverage_end
from signatures as lower_slug
inner join signatures as higher_slug
    on higher_slug.export_signature = lower_slug.export_signature
    -- one row per pair, never both directions
    and higher_slug.profile_slug > lower_slug.profile_slug
