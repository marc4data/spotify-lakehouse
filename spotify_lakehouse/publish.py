"""Flat extracts for Tableau Public (spot-main-R-007, CLAUDE.md §4).

Tableau Public cannot source a database, so `published/` carries CSVs regenerated from the marts.
Every extract is a query against the marts — never hand-assembled — so `make publish`
reproduces them.

🚨 **This module writes files to a gitignored directory. It uploads nothing, anywhere.** The
contents of `published/` are designed to leave the machine, which is why the exclusions below
exist and why the report that accompanies a run ends with a field-level manifest for Marc to
approve.

**The four contested choices are parameters, not decisions.** `Options` carries Cowork's
defaults for R-007 — exclude incognito plays, no device string of any kind, keep
`conn_country`, one profile — and each is a flag so reversing one is a call-site change rather
than a rewrite. They are defaults awaiting Marc's decision, not settled policy.

Raw `platform` is the one field no flag can emit: R-052 measured that it names a device model, and a
public workbook is exactly where that must not appear. `include_platform_family` adds the *family*
(`Android`, `iOS`, …), never the string itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from psycopg import sql

from spotify_lakehouse.config import mart_schema, session, stg_schema
from spotify_lakehouse.listening import platform_family

PUBLISHED_DIR = Path("published")
REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_PROJECT = REPO_ROOT / "dbt" / "dbt_project.yml"
UNCLASSIFIED = "unclassified"
UNIDENTIFIED = "(unidentified)"


@dataclass(frozen=True)
class Options:
    """Cowork's R-007 defaults. Every one of these is Marc's call, not this module's."""

    profile: str = "marc"
    exclude_incognito: bool = True
    include_platform_family: bool = False
    include_conn_country: bool = True


@dataclass(frozen=True)
class ExtractResult:
    name: str
    path: Path
    rows: int
    columns: list[str]
    date_min: date | None
    date_max: date | None


def allocation_tag_type() -> str:
    """The vocabulary step 4 follows, read from dbt's own config rather than copied here.

    A second copy of this value would drift the moment anyone changed the dbt var, and the
    allocation would silently mean something else (R-052's one-definition rule).
    """
    import yaml

    config = yaml.safe_load(DBT_PROJECT.read_text())
    tag_type = (config.get("vars") or {}).get("allocation_tag_type")
    if not tag_type:
        raise ValueError(f"vars.allocation_tag_type is not set in {DBT_PROJECT}")
    return str(tag_type)


def _schemas() -> tuple[sql.Identifier, sql.Identifier]:
    name = session()
    return sql.Identifier(stg_schema(name)), sql.Identifier(mart_schema(name))


def _compose(statement: str) -> sql.Composed:
    stg, mart = _schemas()
    return sql.SQL(statement).format(stg=stg, mart=mart)


def _frame(conn: Any, statement: str, params: tuple[Any, ...]) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(_compose(statement), params)
        columns = [column.name for column in cur.description] if cur.description else []
        return pd.DataFrame(cur.fetchall(), columns=columns)


def _incognito_filter(opts: Options) -> str:
    """Excluded by joining back to export staging: `fct_play_event` carries no incognito flag.

    R-007 F1. The flag lives only on `stg_export__play_record`, so the fact table cannot express the
    exclusion on its own and the natural key (`content_uri`, `ended_at_utc`) does the work.
    """
    if not opts.exclude_incognito:
        return ""
    return (
        " and not exists (select 1 from {stg}.stg_export__play_record as incog "
        "where incog.profile_slug = %s and incog.was_incognito "
        "and incog.content_uri = content.content_uri "
        "and incog.ended_at_utc = fct.ended_at_utc)"
    )


def _incognito_params(opts: Options) -> tuple[Any, ...]:
    return (opts.profile,) if opts.exclude_incognito else ()


def plays_daily(conn: Any, opts: Options) -> pd.DataFrame:
    """profile × date × content_type × bucket, plus whichever optional dimensions are switched on.

    The bucket grain re-expresses the data-contracts §4 chain at day grain because
    `int_genre_bucket_allocation` is monthly. It reads the same bridges and the same map, and
    `tests/test_publish.py` asserts that summing this to month reproduces that model exactly.

    ⚠️ **`plays` and `ms_played` are per-bucket and do not add up across buckets.** One play whose
    artist carries genres in three buckets appears in three rows. The additive measures are
    `allocated_plays` and `allocated_ms`, whose weights sum to 1.0 per play — that is what the §4
    chain allocates. The query collapses to one row per (play, bucket) first, so neither measure is
    multiplied by the genre join (R-007 F3).

    `conn_country` is on by default and `platform_family` is off, per Cowork's R-007 defaults. Each
    adds a dimension to the grain when on, so the row count changes with it (R-007 F4).
    """
    optional = []
    if opts.include_conn_country:
        optional.append("conn_country")
    if opts.include_platform_family:
        # Selected raw so the family can be derived by `platform_family`; dropped below, and never
        # written. Mapping it in SQL would be a second copy of that function (R-052's rule).
        optional.append("platform")
    per_play_cols = "".join(f", plays.{column}" for column in optional)
    names = "".join(f", {column}" for column in optional)
    per_play_group = ", ".join(str(i) for i in range(1, 5 + len(optional)))
    final_group = ", ".join(str(i) for i in range(1, 4 + len(optional)))
    frame = _frame(
        conn,
        "with plays as (select fct.play_event_key, dates.full_date, content.content_type, "
        "fct.ms_played, fct.is_qualified_play, fct.conn_country, fct.platform, bridge.artist_key "
        "from {mart}.fct_play_event as fct "
        "join {mart}.dim_profile as profiles on profiles.profile_key = fct.profile_key "
        "join {mart}.dim_date as dates on dates.date_key = fct.date_key "
        "join {mart}.dim_content as content on content.content_key = fct.content_key "
        "left join {mart}.br_content_artist as bridge "
        "on bridge.content_key = fct.content_key and bridge.is_primary "
        "where profiles.profile_slug = %s" + _incognito_filter(opts) + "), "
        "genres as (select bridge.artist_key, bridge.weight_factor, map.bucket_name "
        "from {mart}.br_artist_genre as bridge "
        "join {stg}.int_genre_bucket_map as map on map.genre_key = bridge.genre_key "
        "where bridge.tag_type = %s), "
        "per_play as (select plays.play_event_key, plays.full_date, plays.content_type, "
        "coalesce(genres.bucket_name, %s) as bucket_name" + per_play_cols + ", "
        "max(plays.ms_played) as ms_played, "
        "bool_or(plays.is_qualified_play) as is_qualified_play, "
        "sum(coalesce(genres.weight_factor, 1)) as weight "
        "from plays left join genres on genres.artist_key = plays.artist_key "
        "group by " + per_play_group + ") "
        "select full_date as play_date, content_type, bucket_name" + names + ", "
        "count(*) as plays, "
        "count(*) filter (where is_qualified_play) as qualified_plays, "
        "sum(ms_played) as ms_played, "
        "count(ms_played) as rows_with_duration, "
        "sum(weight) as allocated_plays, "
        "sum(ms_played * weight) as allocated_ms "
        "from per_play group by " + final_group + " order by " + final_group,
        (opts.profile, *_incognito_params(opts), allocation_tag_type(), UNCLASSIFIED),
    )
    if opts.include_platform_family:
        frame = _to_platform_family(frame)
    frame.insert(0, "profile_slug", opts.profile)
    return frame


def _to_platform_family(frame: pd.DataFrame) -> pd.DataFrame:
    """Replace the raw device string with its family, then re-aggregate onto the new grain.

    The raw value exists only inside this function: it is never written, and several raw strings
    collapse into one family, so the measures have to be summed again afterwards.
    """
    frame = frame.copy()
    frame["platform_family"] = frame["platform"].map(platform_family)
    frame = frame.drop(columns=["platform"])
    keys = [
        column
        for column in (
            "play_date",
            "content_type",
            "bucket_name",
            "conn_country",
            "platform_family",
        )
        if column in frame.columns
    ]
    for column in ("ms_played", "allocated_ms"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.groupby(keys, as_index=False, dropna=False).agg(
        plays=("plays", "sum"),
        qualified_plays=("qualified_plays", "sum"),
        ms_played=("ms_played", lambda values: values.sum(min_count=1)),
        rows_with_duration=("rows_with_duration", "sum"),
        allocated_plays=("allocated_plays", "sum"),
        allocated_ms=("allocated_ms", lambda values: values.sum(min_count=1)),
    )


def artists(conn: Any, opts: Options) -> pd.DataFrame:
    """profile × artist × year. Unidentified plays are a named row, not a silent omission."""
    frame = _frame(
        conn,
        "select dates.year, coalesce(artist.artist_name, %s) as artist_name, "
        "(artist.artist_key is not null) as is_identified, "
        "count(*) as plays, "
        "count(*) filter (where fct.is_qualified_play) as qualified_plays, "
        "round(sum(fct.ms_played) / 3600000.0, 3) as hours, "
        "count(fct.ms_played) as rows_with_duration "
        "from {mart}.fct_play_event as fct "
        "join {mart}.dim_profile as profiles on profiles.profile_key = fct.profile_key "
        "join {mart}.dim_date as dates on dates.date_key = fct.date_key "
        "join {mart}.dim_content as content on content.content_key = fct.content_key "
        "left join {mart}.br_content_artist as bridge "
        "on bridge.content_key = fct.content_key and bridge.is_primary "
        "left join {mart}.dim_artist as artist on artist.artist_key = bridge.artist_key "
        "where profiles.profile_slug = %s and content.content_type = 'track'"
        + _incognito_filter(opts)
        + " group by 1, 2, 3 order by 1, 4 desc",
        (UNIDENTIFIED, opts.profile, *_incognito_params(opts)),
    )
    frame.insert(0, "profile_slug", opts.profile)
    return frame


def genres(conn: Any, opts: Options) -> pd.DataFrame:
    """profile × bucket × month, with the unclassified residual as its own row.

    A bucket share that quietly renormalises over allocated time only would read as full coverage.
    The residual is a row so a workbook cannot avoid it.
    """
    daily = plays_daily(conn, opts)
    if daily.empty:
        return pd.DataFrame(
            columns=[
                "profile_slug",
                "month_start",
                "bucket_name",
                "allocated_plays",
                "allocated_ms",
                "share_of_music_ms",
            ]
        )
    music = daily[daily["content_type"] == "track"].copy()
    music["month_start"] = pd.to_datetime(music["play_date"]).dt.to_period("M").dt.to_timestamp()
    grouped = (
        music.groupby(["month_start", "bucket_name"], as_index=False)
        # `min_count=1` keeps an all-NULL group NULL. pandas sums NaN to 0.0, which would publish
        # "0 ms allocated" for a month whose every play is an API row with no duration — a play
        # with no duration is not a play of zero seconds (R-050, data-contracts §2).
        .agg(
            allocated_plays=("allocated_plays", "sum"),
            allocated_ms=("allocated_ms", lambda values: values.sum(min_count=1)),
        )
        .sort_values(["month_start", "bucket_name"])
    )
    totals = grouped.groupby("month_start")["allocated_ms"].transform("sum")
    grouped["share_of_music_ms"] = (grouped["allocated_ms"] / totals.replace(0, pd.NA)).astype(
        float
    )
    grouped["share_of_music_ms"] = grouped["share_of_music_ms"].round(6)
    grouped["month_start"] = grouped["month_start"].dt.date
    grouped.insert(0, "profile_slug", opts.profile)
    return grouped.reset_index(drop=True)


def coverage(conn: Any, opts: Options) -> pd.DataFrame:
    """The four reconciliation steps and their residuals, verbatim from the model.

    ⚠️ This describes the **warehouse**, not the published extract: the reconciliation counts every
    play, including the incognito ones the extracts exclude. R-007 F2 — a workbook quoting these
    numbers beside filtered extracts would overstate its own coverage by the excluded rows.
    """
    return _frame(
        conn,
        "select profile_slug, step_number, step_name, play_count, ms_played, rows_with_duration, "
        "pct_rows_with_duration, residual_name, residual_play_count, residual_ms_played "
        "from {stg}.int_allocation_reconciliation where profile_slug = %s order by step_number",
        (opts.profile,),
    )


EXTRACTS = {
    "plays_daily": plays_daily,
    "artists": artists,
    "genres": genres,
    "coverage": coverage,
}

# Fields deliberately absent, and why. The manifest renders this; it is not prose in a report.
EXCLUDED_FIELDS = [
    (
        "platform (raw)",
        "fct_play_event.platform",
        "Names a device model (R-052 F1, R-009). No flag emits it; "
        "`include_platform_family` adds the family only.",
    ),
    (
        "incognito plays",
        "stg_export__play_record.was_incognito",
        "1,558 plays Marc marked private. Excluded by default; "
        "`exclude_incognito=False` keeps them.",
    ),
    (
        "artist_id / content_uri",
        "dim_artist.artist_id, dim_content.content_uri",
        "Identifier-shaped (R-052 `must_redact`). A workbook needs names, not catalogue ids.",
    ),
    (
        "profile_slug (other people)",
        "dim_profile.profile_slug",
        "One profile per run. Marie, Brody and Emma's listening is theirs to consent to, "
        "not Marc's.",
    ),
    (
        "display_name / spotify_user_id / email",
        "dim_profile, /me",
        "Never leaves the warehouse; `redact.SENSITIVE_FIELDS` covers them.",
    ),
]


def write_extracts(
    conn: Any, opts: Options | None = None, out_dir: Path | None = None
) -> list[ExtractResult]:
    """Regenerate every extract. Writes CSV only — nothing is uploaded."""
    opts = opts or Options()
    out_dir = out_dir or PUBLISHED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, build in EXTRACTS.items():
        frame = build(conn, opts)
        path = out_dir / f"{name}.csv"
        frame.to_csv(path, index=False, encoding="utf-8", date_format="%Y-%m-%d")
        dates = _date_span(frame)
        results.append(
            ExtractResult(name, path, len(frame), list(frame.columns), dates[0], dates[1])
        )
    return results


def _date_span(frame: pd.DataFrame) -> tuple[date | None, date | None]:
    for column in ("play_date", "month_start"):
        if column in frame.columns and not frame.empty:
            values = pd.to_datetime(frame[column])
            return values.min().date(), values.max().date()
    if "year" in frame.columns and not frame.empty:
        return date(int(frame["year"].min()), 1, 1), date(int(frame["year"].max()), 12, 31)
    return None, None


def manifest(results: list[ExtractResult]) -> pd.DataFrame:
    """Every column of every extract, from the files themselves plus the exclusion list."""
    rows: list[dict[str, Any]] = []
    for result in results:
        for column in result.columns:
            rows.append(
                {
                    "file": result.path.name,
                    "column": column,
                    "included": "yes",
                    "source": _SOURCES.get(column, "derived in publish.py"),
                    "note": _NOTES.get(column, ""),
                }
            )
    for name, source, reason in EXCLUDED_FIELDS:
        rows.append(
            {"file": "(all)", "column": name, "included": "NO", "source": source, "note": reason}
        )
    return pd.DataFrame(rows, columns=["file", "column", "included", "source", "note"])


_SOURCES = {
    "profile_slug": "dim_profile.profile_slug",
    "play_date": "dim_date.full_date",
    "month_start": "dim_date, truncated to month",
    "year": "dim_date.year",
    "content_type": "dim_content.content_type",
    "bucket_name": "int_genre_bucket_map.bucket_name",
    "artist_name": "dim_artist.artist_name",
    "is_identified": "br_content_artist.is_primary → dim_artist",
    "plays": "count of fct_play_event rows (per bucket; not additive across buckets)",
    "qualified_plays": "fct_play_event.is_qualified_play",
    "ms_played": "fct_play_event.ms_played (per bucket; not additive across buckets)",
    "hours": "fct_play_event.ms_played / 3,600,000",
    "rows_with_duration": "count of non-null fct_play_event.ms_played",
    "allocated_plays": "weight_factor (1/N per tag_type); sums to 1.0 per play — additive",
    "allocated_ms": "ms_played × weight_factor",
    "share_of_music_ms": "allocated_ms / month total",
    "conn_country": "fct_play_event.conn_country (US / ZZ / MX; ZZ is not a country)",
    "platform_family": "listening.platform_family(fct_play_event.platform) — never the string",
    "step_number": "int_allocation_reconciliation",
    "step_name": "int_allocation_reconciliation",
    "play_count": "int_allocation_reconciliation",
    "pct_rows_with_duration": "int_allocation_reconciliation",
    "residual_name": "int_allocation_reconciliation",
    "residual_play_count": "int_allocation_reconciliation",
    "residual_ms_played": "int_allocation_reconciliation",
}

_NOTES = {
    "bucket_name": f"`{UNCLASSIFIED}` where the primary artist has no genre — a row, not a gap.",
    "artist_name": f"`{UNIDENTIFIED}` where no artist object has been resolved.",
    "ms_played": "NULL on API plays; never defaulted to zero (data-contracts §2).",
    "play_count": "Counts every play in the warehouse, incognito included — see the coverage note.",
}


def platform_family_of(value: Any) -> str:
    """Exposed for `include_platform_family`: the family, never the string behind it."""
    return platform_family(value)
