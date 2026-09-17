{#
    The tier-3 loose match key (docs/data-contracts.md §5: "normalized name + primary creator").

    spot-main-R-060. §5 has always said *normalized*; until this round the normalization was only
    `lower(trim(...))`, which folds case and edge whitespace and nothing else. Measured against the
    warehouse as it stood:

      * 42,240 distinct keys; `lower+trim` changed none of them, because the old expression already
        did exactly that much.
      * Folding diacritics, punctuation and interior whitespace collapses 32 sibling pairs — Spanish
        accents (`perdóname`), curly apostrophes (`can’t`), `JAŸ-Z`, `Givēon`, `prélude`.
      * 92 keys stay non-ASCII afterwards and that is correct: ø æ ð þ ł have no canonical
        decomposition, so NFKD leaves them alone and they remain stable keys.

    Built from `normalize(..., NFKD)`, which Postgres 13+ supplies natively — deliberately NOT
    `unaccent`, which is available on this server but *not installed*, and a dbt model should not
    depend on an extension someone has to remember to create.

    Three passes, in order:
      1. NFKD decomposes a glyph into base + combining mark; the regexp strips U+0300–U+036F, so
         ÿ → y, ē → e, ř → r, Å → a.
      2. `translate` folds punctuation NFKD cannot touch, because these are distinct characters
         rather than decomposable ones: the curly quotes and dashes Spotify's catalogue mixes with
         their ASCII forms. Dollar-quoted so the five apostrophe variants need no doubling — the
         map is 9 characters to 9.
      3. Interior whitespace collapses to single spaces.

    ⚠️ This widens what tier 3 matches, which §5 already warns "will over-match on covers and live
    versions". Every one of the 32 merges it creates was inspected by hand in R-060's report.
#}
{% macro spot_fold(expr) -%}
    regexp_replace(
        translate(
            regexp_replace(normalize(lower(trim({{ expr }})), NFKD), $$[̀-ͯ]$$, '', 'g'),
            $$’‘ʼ`´“”–—$$, $$'''''""--$$
        ),
        $$\s+$$, ' ', 'g'
    )
{%- endmacro %}


{#
    The key itself: folded name, a pipe, folded primary creator. NULL when either side is NULL,
    which is the behaviour dim_content has always had — 922 of 49,136 rows carry no key because
    nothing has ever supplied a creator for that URI.
#}
{% macro content_match_key(name_expr, creator_expr) -%}
    {{ spot_fold(name_expr) }} || '|' || {{ spot_fold(creator_expr) }}
{%- endmacro %}
