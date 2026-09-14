{#
    Session-scoped schemas (docs/worktree-protocol.md §2, docs/data-contracts.md §1).
      staging, intermediate  -> target.schema, i.e. stg_<session> from profiles.yml
      marts (+schema: mart)  -> mart_<session>
      prod target            -> analytics for any model that declares a custom schema
    SPOT_SESSION has no default on purpose: an unset session must fail, never become `main`.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif target.name == 'prod' -%}
        analytics
    {%- else -%}
        {{ custom_schema_name | trim }}_{{ env_var('SPOT_SESSION') }}
    {%- endif -%}
{%- endmacro %}
