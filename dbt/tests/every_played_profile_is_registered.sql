-- A profile with plays but no registry row would land on the -1 unknown member with no home timezone,
-- and every one of its dates would be -1. Fail loudly instead: add the row to ~/.config/spot/profiles.csv.
select distinct plays.profile_slug
from {{ ref('stg_spotify__recently_played') }} as plays
left join {{ ref('stg_spot_meta__profile_registry') }} as registry
    on registry.profile_slug = plays.profile_slug
where registry.profile_slug is null
