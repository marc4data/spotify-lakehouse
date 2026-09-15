-- raw.external_response: one row per response from a non-Spotify source (spot-main-R-024).
-- Shape fixed by docs/data-contracts.md §1 as amended by R-024. Never modified after insert.
--
-- There is deliberately NO profile_slug. A MusicBrainz genre list is the same whoever played the
-- artist; raw.api_response's `profile_slug not null` would force a fake value, and a fake value in a
-- not-null column is how a dimension starts lying. Its absence is the reason this table exists.
--
-- request_key is the identifier the request was made for (an ISRC, an artist MBID). A 404 body
-- does not echo it, so without this column an "unknown to MusicBrainz" answer could not be tied to
-- the ISRC it answers, could not be reported as a coverage gap, and would be re-fetched forever.
create table raw.external_response (
    id bigserial primary key,
    source text not null,
    feed text not null,
    request_key text not null,
    payload jsonb not null,
    source_file text not null,
    ingested_at timestamptz not null default now(),
    constraint external_response_source_allowed check (source in ('musicbrainz')),
    constraint external_response_feed_format check (feed ~ '^[a-z][a-z0-9_]*$')
);

create index external_response_source_feed_key_idx
    on raw.external_response (source, feed, request_key, ingested_at);

create trigger external_response_no_update_delete
    before update or delete on raw.external_response
    for each row execute function raw.reject_mutation();

create trigger external_response_no_truncate
    before truncate on raw.external_response
    for each statement execute function raw.reject_mutation();
