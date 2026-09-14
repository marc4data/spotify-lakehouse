{#
    Generic test: the combination of `columns` is unique. A dependency-free stand-in for
    dbt_utils.unique_combination_of_columns, so the project needs no package install.
#}
{% test dbt_utils_free_unique_combination(model, columns) %}
    select
        {{ columns | join(', ') }},
        count(*) as rows_at_key
    from {{ model }}
    group by {{ columns | join(', ') }}
    having count(*) > 1
{% endtest %}
