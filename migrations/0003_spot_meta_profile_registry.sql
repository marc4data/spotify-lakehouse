-- spot_meta.profile_registry: household role and home timezone per profile (data-contracts §3, dim_profile).
-- These have no API source. They are personal data about family members, so they are NOT in the public
-- repo: `spot sync-profiles` loads them from ~/.config/spot/profiles.csv (R-004, report §5 S1).
-- Not part of `raw` — raw holds only extractor output.
create table spot_meta.profile_registry (
    profile_slug text primary key check (profile_slug ~ '^[a-z][a-z0-9_]{0,31}$'),
    household_role text not null check (household_role in ('self', 'spouse', 'child', 'friend')),
    home_timezone text not null,
    loaded_at timestamptz not null default now()
);
