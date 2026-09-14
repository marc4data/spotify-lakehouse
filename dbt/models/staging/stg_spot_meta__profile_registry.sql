-- Household role and home timezone per profile, loaded from ~/.config/spot/profiles.csv.
with source as (
    select * from {{ source('spot_meta', 'profile_registry') }}
),

renamed as (
    select
        profile_slug,
        household_role,
        home_timezone,
        loaded_at
    from source
)

select * from renamed
