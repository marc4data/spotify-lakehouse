-- raw.api_response: one row per Spotify Web API response, as received (minus discarded fields).
-- Shape fixed by docs/data-contracts.md §1. Never modified after insert — enforced below.
create schema if not exists raw;

create table raw.api_response (
    id bigserial primary key,
    payload jsonb not null,
    source_file text not null,
    profile_key text not null,
    ingested_at timestamptz not null default now()
);

create index api_response_profile_ingested_idx
    on raw.api_response (profile_key, ingested_at);

create function raw.reject_mutation() returns trigger
language plpgsql
as $$
begin
    raise exception 'raw.% is append-only: % rejected (docs/data-contracts.md §1)',
        tg_table_name, tg_op;
end;
$$;

create trigger api_response_no_update_delete
    before update or delete on raw.api_response
    for each row execute function raw.reject_mutation();

create trigger api_response_no_truncate
    before truncate on raw.api_response
    for each statement execute function raw.reject_mutation();
