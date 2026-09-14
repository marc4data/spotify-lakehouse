-- Bring raw.api_response to the amended data-contracts §1 shape (spot-main-R-004):
--   * profile_key -> profile_slug  (F3; metadata only, no row data is written)
--   * new column feed text not null (F2)
--
-- The feed backfill for rows inserted before the column existed is the ONE write §1 permits:
-- "a schema migration that adds a column which did not exist at insert time may backfill that column,
-- inside the migration's own transaction, with the update trigger disabled for its duration, and must
-- checksum every pre-existing column before and after to prove nothing else was written."
-- The checksum is enforced below, in this transaction: if it differs, the whole migration rolls back.

alter table raw.api_response rename column profile_key to profile_slug;

create temporary table _raw_checksum_before on commit drop as
select encode(sha256(convert_to(coalesce(string_agg(
    id::text || '|' || payload::text || '|' || source_file || '|' || profile_slug || '|' || ingested_at::text,
    E'\n' order by id), ''), 'UTF8')), 'hex') as checksum
from raw.api_response;

alter table raw.api_response add column feed text;

alter table raw.api_response disable trigger api_response_no_update_delete;

-- Pre-contract rows are identified by the source_file layout the R-002 extractor wrote.
update raw.api_response
set feed = case
    when source_file like 'api/me/%' then 'me'
    when source_file like 'api/me\_player\_recently-played/%' then 'recently_played'
end
where feed is null;

alter table raw.api_response enable trigger api_response_no_update_delete;

do $$
declare
    before_checksum text := (select checksum from _raw_checksum_before);
    after_checksum text := (
        select encode(sha256(convert_to(coalesce(string_agg(
            id::text || '|' || payload::text || '|' || source_file || '|' || profile_slug || '|' || ingested_at::text,
            E'\n' order by id), ''), 'UTF8')), 'hex')
        from raw.api_response
    );
    unmapped bigint := (select count(*) from raw.api_response where feed is null);
begin
    if unmapped > 0 then
        raise exception '0002: % pre-existing row(s) have a source_file with no known feed; migration aborted', unmapped;
    end if;
    if before_checksum is distinct from after_checksum then
        raise exception '0002: pre-existing columns changed during the feed backfill (before %, after %); migration aborted',
            before_checksum, after_checksum;
    end if;
    raise notice '0002: feed backfilled; pre-existing column checksum unchanged (%)', after_checksum;
end
$$;

alter table raw.api_response alter column feed set not null;

alter table raw.api_response
    add constraint api_response_feed_format check (feed ~ '^[a-z][a-z0-9_]*$');

create index api_response_feed_ingested_idx on raw.api_response (feed, ingested_at);
