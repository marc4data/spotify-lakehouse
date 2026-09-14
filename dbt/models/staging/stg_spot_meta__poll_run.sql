-- One row per profile per `spot refresh` poll (spot_meta.poll_run, R-017).
with source as (
    select * from {{ source('spot_meta', 'poll_run') }}
),

renamed as (
    select
        id as poll_run_id,
        run_id,
        profile_slug,
        started_at,
        finished_at,
        status,
        raw_response_id,
        items_returned
    from source
)

select * from renamed
