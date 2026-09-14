{{ config(enabled=false) }}
-- ⏸️ DISABLED until spot-main-R-024 (genre source).
--
-- data-contracts §3: "a dbt test fails the build when `other` exceeds 15% of allocated listening time."
-- It is moot while no genre source exists — nothing is allocated, so there is no `other` to measure —
-- and the models it needs (genre allocation by bucket) are deferred with dim_genre and br_artist_genre.
--
-- When R-024 lands, enable this and replace the placeholder below with, in outline:
--   select 1
--   from <bucket allocation model>
--   having sum(case when bucket_name = 'other' then allocated_ms end)
--        > 0.15 * sum(allocated_ms)
-- R-004 report §5 S2 also leaves open whether an empty genre_bucket_map should skip or fail this test.
select 1 as placeholder
where false
