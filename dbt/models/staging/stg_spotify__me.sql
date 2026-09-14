-- One row per GET /me response. `account_id` (F8) and `email` (F7) are never selected.
with source as (
    select *
    from {{ source('raw', 'api_response') }}
    where feed = 'me'
),

renamed as (
    select
        id as raw_response_id,
        profile_slug,
        payload ->> 'id' as spotify_user_id,
        payload ->> 'display_name' as display_name,
        payload ->> 'country' as country_code,
        payload ->> 'product' as product,
        ingested_at,
        source_file
    from source
)

select * from renamed
