{{ config(enabled=false) }}
-- ⏸️ DISABLED until spot-main-R-015 (the bucket taxonomy).
--
-- data-contracts §3: "a dbt test fails the build when `other` exceeds 15% of allocated listening time."
-- R-024 built the genre source (dim_genre, br_artist_genre) but deliberately no buckets, so there is still
-- no `other` to measure.
--
-- When R-015 lands the bucket map, enable this and replace the placeholder below with, in outline:
--   select 1
--   from <bucket allocation model>
--   having sum(case when bucket_name = 'other' then allocated_ms end)
--        > 0.15 * sum(allocated_ms)
-- R-004 report §5 S2 also leaves open whether an empty genre_bucket_map should skip or fail this test.
select 1 as placeholder
where false
