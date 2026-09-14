-- dim_time_of_day (data-contracts §3): 1440 rows, one per minute, key = hour * 100 + minute.
with minutes as (
    select minute_of_day
    from generate_series(0, 1439) as minute_of_day
),

times as (
    select
        (minute_of_day / 60) * 100 + minute_of_day % 60 as time_of_day_key,
        minute_of_day / 60 as hour,
        minute_of_day % 60 as minute,
        case
            when minute_of_day / 60 < 5 then 'overnight'
            when minute_of_day / 60 < 9 then 'morning'
            when minute_of_day / 60 < 12 then 'midday'
            when minute_of_day / 60 < 17 then 'afternoon'
            when minute_of_day / 60 < 22 then 'evening'
            else 'night'
        end as daypart
    from minutes
),

unknown_member as (
    select
        -1 as time_of_day_key,
        null::int as hour,
        null::int as minute,
        'unknown'::text as daypart
)

select * from times
union all
select * from unknown_member
