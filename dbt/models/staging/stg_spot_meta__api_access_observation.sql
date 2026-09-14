-- Whether each registry profile had a stored refresh token, per `spot refresh` run (R-033). A boolean only;
-- the token itself never reaches the database.
with source as (
    select * from {{ source('spot_meta', 'api_access_observation') }}
),

renamed as (
    select
        id as api_access_observation_id,
        run_id,
        profile_slug,
        has_stored_token,
        observed_at
    from source
)

select * from renamed
