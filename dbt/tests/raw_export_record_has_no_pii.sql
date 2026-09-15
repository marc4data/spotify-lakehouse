-- data-contracts §1/§2 (R-003): ip_addr and user_agent are discarded at ingest and never written to raw.
-- Standing guard over the whole table. Returns the id of every record that carries one; any row fails.
-- (The check constraint export_record_no_ip_or_user_agent should make this impossible; this test would
-- still see it if the constraint were ever dropped.)
select
    id,
    profile_slug,
    source_file,
    record_index
from {{ source('raw', 'export_record') }}
where payload ?| array['ip_addr', 'ip_addr_decrypted', 'user_agent', 'user_agent_decrypted']
