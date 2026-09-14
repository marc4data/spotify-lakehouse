-- dim_date (data-contracts §3): 2008-10-07 (Spotify launch) through two years past the build date.
with days as (
    select day::date as full_date
    from generate_series(date '2008-10-07', (current_date + interval '2 years')::date, interval '1 day') as day
),

calendar as (
    select
        to_char(full_date, 'YYYYMMDD')::int as date_key,
        full_date,
        extract(year from full_date)::int as year,
        extract(quarter from full_date)::int as quarter,
        extract(month from full_date)::int as month,
        to_char(full_date, 'FMMonth') as month_name,
        date_trunc('month', full_date)::date as month_start,
        extract(day from full_date)::int as day_of_month,
        extract(isodow from full_date)::int as day_of_week_iso,
        to_char(full_date, 'FMDay') as day_of_week_name,
        extract(week from full_date)::int as iso_week,
        extract(isoyear from full_date)::int as iso_year,
        extract(isodow from full_date) in (6, 7) as is_weekend
    from days
),

unknown_member as (
    select
        -1 as date_key,
        null::date as full_date,
        null::int as year,
        null::int as quarter,
        null::int as month,
        null::text as month_name,
        null::date as month_start,
        null::int as day_of_month,
        null::int as day_of_week_iso,
        null::text as day_of_week_name,
        null::int as iso_week,
        null::int as iso_year,
        null::boolean as is_weekend
)

select * from calendar
union all
select * from unknown_member
