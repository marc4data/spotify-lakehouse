-- raw.export_record: one row per play record from a Spotify Extended Streaming History export
-- (spot-main-R-003). Shape fixed by docs/data-contracts.md §1 as amended by R-003. Never modified after insert.
--
-- One row per RECORD, not per file: a file holds ~15,000 records, and one multi-megabyte jsonb per file
-- is unqueryable and un-diffable. (profile_slug, source_file, record_index) addresses a record, so a
-- re-run of the loader over the same file inserts nothing.
--
-- ip_addr and user_agent are discarded at INGEST (§1 wins over §2's former "dropped at staging"): once in
-- an append-only table they could never be removed. The loader scrubs them; this check constraint is the
-- backstop, so a scrub bug fails the load instead of writing a family member's IP address forever.
create table raw.export_record (
    id bigserial primary key,
    profile_slug text not null,
    source_file text not null,
    record_index int not null,
    payload jsonb not null,
    ingested_at timestamptz not null default now(),
    constraint export_record_position unique (profile_slug, source_file, record_index),
    constraint export_record_index_non_negative check (record_index >= 0),
    constraint export_record_no_ip_or_user_agent check (
        not (payload ?| array['ip_addr', 'ip_addr_decrypted', 'user_agent', 'user_agent_decrypted'])
    )
);

create trigger export_record_no_update_delete
    before update or delete on raw.export_record
    for each row execute function raw.reject_mutation();

create trigger export_record_no_truncate
    before truncate on raw.export_record
    for each statement execute function raw.reject_mutation();
