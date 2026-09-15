"""Sources beside the API in notebooks/01_data_inventory.ipynb (spot-main-R-042, notebook-specs §1).

§1–§7 (`inventory`) cover the Spotify Web API. This module covers what now sits beside it: the
Extended Streaming History export (§8), MusicBrainz (§9) and the warehouse as built (§10). Each
table gets the four H4s an endpoint gets. Every figure is queried when the notebook runs, never
typed into the prose, and every sample and schema example passes through `redact.redact_profile`
plus a mask on identifier-shaped columns (`*_id`, `*_uri`, `*_mbid`, `request_key`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from spotify_lakehouse.inventory import SAMPLE_ROWS, _callout, _md, _show, _summary, sample_frame
from spotify_lakehouse.redact import REDACTED, _is_sensitive, redact_profile

IDENTIFIER_SUFFIXES = ("_id", "_uri", "_mbid")
IDENTIFIER_COLUMNS = frozenset({"request_key"})
# A key present on every record but null on more than this share is listed as sparse.
SPARSE_NULL_RATE = 0.95
DISCARDED_EXPORT_KEYS = ["ip_addr", "ip_addr_decrypted", "user_agent", "user_agent_decrypted"]
SCHEMA_COLUMNS = ["field", "type", "non_null", "null_rate", "example"]


def is_identifier(column: Any) -> bool:
    name = str(column)
    return _is_sensitive(name) or name.endswith(IDENTIFIER_SUFFIXES) or name in IDENTIFIER_COLUMNS


def redact_sample(frame: pd.DataFrame) -> pd.DataFrame:
    """`redact_profile`, then mask every identifier-shaped column the profile rule does not name."""
    out = redact_profile(frame)
    for column in out.columns:
        if is_identifier(column):
            out[column] = out[column].where(out[column].isna(), REDACTED)
    return out


@dataclass
class TableSection:
    number: str
    title: str
    source: str
    tables: list[tuple[str, pd.DataFrame]] = field(default_factory=list)
    schemas: list[tuple[str, pd.DataFrame]] = field(default_factory=list)
    samples: list[tuple[str, pd.DataFrame]] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)  # (callout kind, or "" for prose)


def emit_table_section(section: TableSection) -> None:
    """A table section: H3 plus the same four H4s an endpoint section has."""
    _md(f"### {section.number} {section.title}")
    _md("#### Table & source")
    _md(section.source)
    for caption, frame in section.tables:
        _md(caption)
        _show(frame)

    _md("#### Schema")
    shown = False
    for label, frame in section.schemas:
        if not frame.empty:
            _md(label)
            _show(frame)
            shown = True
    if not shown:
        _md("No rows were found, so there is no schema to derive.")

    _md("#### Sample")
    shown = False
    for label, frame in section.samples:
        if not frame.empty:
            _md(f"**{label}** — up to {SAMPLE_ROWS} rows, redacted.")
            _show(redact_sample(frame.head(SAMPLE_ROWS)))
            shown = True
    if not shown:
        _md("Nothing to sample.")

    _md("#### Notes & limits")
    for kind, text in section.notes:
        if kind:
            _callout(kind, text)
        else:
            _md(text)
    if not section.notes:
        _md("Nothing surprising beyond the tables above.")


# --- Schema helpers ------------------------------------------------------------------------------


def _relation(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _schema_row(name: str, kind: str, non_null: int, total: int, example: Any) -> dict[str, Any]:
    return {
        "field": name,
        "type": kind,
        "non_null": f"{non_null}/{total}",
        "null_rate": round(1 - non_null / total, 4) if total else None,
        "example": REDACTED if example is not None and is_identifier(name) else _summary(example),
    }


def column_schema(
    ctx: Any, schema: str, table: str, where: str = "true", params: tuple[Any, ...] = ()
) -> pd.DataFrame:
    """Every column: type, non-null count and null rate over all matching rows, one example."""
    columns = ctx.rows(
        "select column_name, data_type from information_schema.columns "
        "where table_schema = %s and table_name = %s order by ordinal_position",
        (schema, table),
    )
    if not columns:
        return pd.DataFrame(columns=SCHEMA_COLUMNS)
    relation = _relation(schema, table)
    counted = ", ".join(f'count("{name}")' for name, _ in columns)
    counts = ctx.rows(f"select count(*), {counted} from {relation} where {where}", params)[0]
    total = counts[0]
    rows = []
    for (name, kind), non_null in zip(columns, counts[1:], strict=True):
        example = (
            ctx.scalar(
                f'select "{name}" from {relation} where {where} and "{name}" is not null limit 1',
                params,
            )
            if non_null
            else None
        )
        rows.append(_schema_row(name, kind, non_null, total, example))
    return pd.DataFrame(rows, columns=SCHEMA_COLUMNS)


def payload_key_schema(
    ctx: Any, schema: str, table: str, where: str = "true", params: tuple[Any, ...] = ()
) -> pd.DataFrame:
    """Every top-level key of `payload`: JSON type(s), non-null share over all rows, one example."""
    relation = _relation(schema, table)
    total = ctx.scalar(f"select count(*) from {relation} where {where}", params)
    keys = ctx.rows(
        "select k, count(*) filter (where jsonb_typeof(payload -> k) <> 'null'), "
        "string_agg(distinct jsonb_typeof(payload -> k), '|') "
        f"from {relation} cross join lateral jsonb_object_keys(payload) as k "
        f"where {where} group by k order by k",
        params,
    )
    rows = []
    for key, non_null, kinds in keys:
        example = ctx.scalar(
            f"select payload -> %s from {relation} where {where} "
            "and jsonb_typeof(payload -> %s) <> 'null' limit 1",
            (key, *params, key),
        )
        rows.append(_schema_row(key, kinds, non_null, total or 0, example))
    return pd.DataFrame(rows, columns=SCHEMA_COLUMNS)


def _payload_sample(ctx: Any, statement: str, params: tuple[Any, ...]) -> pd.DataFrame:
    return sample_frame([payload for (payload,) in ctx.rows(statement, params)])


def _hours(frame: pd.DataFrame, ms_column: str = "ms_played") -> pd.DataFrame:
    out = frame.copy()
    out["hours"] = (out[ms_column].astype("float") / 3_600_000).round(1)
    return out.drop(columns=[ms_column])


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"


def _sparse_keys(schema: pd.DataFrame) -> list[str]:
    return [row.field for row in schema.itertuples() if (row.null_rate or 0) > SPARSE_NULL_RATE]


# --- §8 The Extended Streaming History export ----------------------------------------------------


def export_sections(ctx: Any) -> list[TableSection]:
    by_profile = "profile_slug = %s"
    profile = (ctx.profile,)
    files = ctx.frame(
        "select source_file, count(*) as records, "
        "min((payload ->> 'ts')::timestamptz)::date as first_day, "
        "max((payload ->> 'ts')::timestamptz)::date as last_day "
        "from raw.export_record where profile_slug = %s group by source_file order by source_file",
        profile,
    )
    records, first_ts, last_ts = ctx.rows(
        "select count(*), min((payload ->> 'ts')::timestamptz), "
        "max((payload ->> 'ts')::timestamptz) from raw.export_record where profile_slug = %s",
        profile,
    )[0]
    types = _hours(
        ctx.frame(
            "select case when payload ->> 'spotify_track_uri' is not null then 'track' "
            "when payload ->> 'spotify_episode_uri' is not null then 'episode' "
            "when payload ->> 'audiobook_chapter_uri' is not null then 'audiobook_chapter' "
            "else 'no content uri' end as content_type, count(*) as records, "
            "sum((payload ->> 'ms_played')::bigint) as ms_played "
            "from raw.export_record where profile_slug = %s group by 1 order by 2 desc",
            profile,
        )
    )
    raw_schema = payload_key_schema(ctx, "raw", "export_record", by_profile, profile)
    discarded = ctx.scalar(
        "select count(*) from raw.export_record where payload ?| %s", (DISCARDED_EXPORT_KEYS,)
    )
    constraint = ctx.scalar(
        "select count(*) from pg_constraint where conname = 'export_record_no_ip_or_user_agent'"
    )
    raw_notes = [
        (
            "WARNING" if discarded or not constraint else "NOTE",
            "**Discarded before insert, and rejected by the database:** "
            f"`{'`, `'.join(DISCARDED_EXPORT_KEYS)}`. Rows carrying any of them, measured in this "
            f"run: **{discarded}**. Check constraint `export_record_no_ip_or_user_agent` present: "
            f"**{'yes' if constraint else 'no'}**. In the original files `ip_addr` was on every "
            "record and `user_agent` on none, under either name (R-003 F4, measured on the "
            "originals, which this notebook does not read).",
        ),
        (
            "NOTE",
            "`offline_timestamp` mixes units: R-003 measured 309 of 43,028 values in milliseconds "
            "and the rest in seconds, so staging keeps it raw as `offline_timestamp_raw`.",
        ),
        (
            "",
            "`raw` is append-only. Records are unique on `(profile_slug, source_file, "
            "record_index)`, so re-loading the same export inserts zero rows (R-003).",
        ),
    ]
    sparse = _sparse_keys(raw_schema)
    if sparse:
        raw_notes.insert(
            1,
            (
                "",
                "**Present on every record, null on most** (null rate above "
                f"{SPARSE_NULL_RATE:.0%}): `{'`, `'.join(sparse)}`. A record carries a track, an "
                "episode or an audiobook chapter, and the other types' keys are null.",
            ),
        )
    raw_section = TableSection(
        "8.1",
        "raw.export_record — one row per export record",
        "Spotify's **Extended Streaming History** (a GDPR export, requested per account), loaded "
        "by `spot load-export` with one row per record and the record as `payload`. It is the "
        f"only full history this project has: **{records} records** for `{ctx.profile}`, from "
        f"**{first_ts:%Y-%m-%d}** to **{last_ts:%Y-%m-%d}**, in {len(files)} files."
        if records
        else f"No export records are loaded for `{ctx.profile}`.",
        tables=[("**Per file.**", files), ("**Content type, from the record's URI.**", types)],
        schemas=[
            (f"**payload keys** — {DOCUMENTATION_FREE}", raw_schema),
        ],
        samples=[
            (
                "payload, one level flattened",
                _payload_sample(
                    ctx,
                    "select payload from raw.export_record where profile_slug = %s "
                    "order by id limit 5",
                    profile,
                ),
            )
        ],
        notes=raw_notes,
    )

    stg = ctx.stg_schema
    split = _hours(
        ctx.frame(
            "select content_type, count(*) as plays, count(ms_played) as with_ms_played, "
            "sum(ms_played) as ms_played, min(ended_at_utc) as first_play, "
            "max(ended_at_utc) as last_play from {stg}.stg_export__play_record "
            "where profile_slug = %s group by content_type order by plays desc",
            profile,
        )
    )
    stg_section = TableSection(
        "8.2",
        "stg_export__play_record — the export, typed",
        f"A view in `{stg}` over `raw.export_record`: one row per record, every column typed, "
        "`content_type` taken from whichever URI the record carries. It feeds `fct_play_event` "
        "with `source_system = 'export'`.",
        tables=[("**By content type.**", split)],
        schemas=[
            (
                "**columns**",
                column_schema(ctx, stg, "stg_export__play_record", by_profile, profile),
            )
        ],
        samples=[
            (
                "latest plays",
                ctx.frame(
                    "select * from {stg}.stg_export__play_record where profile_slug = %s "
                    "order by ended_at_utc desc limit 5",
                    profile,
                ),
            )
        ],
        notes=[
            (
                "NOTE",
                "Three content types, not two. `audiobook_chapter` was found in the export "
                "(R-003 F3) and has been a `dim_content` type since R-037.",
            ),
            ("", "`reason_start` / `reason_end` empty strings become NULL here (R-003 F6)."),
        ],
    )
    return [raw_section, stg_section]


DOCUMENTATION_FREE = "derived from the stored records, not from documentation"


# --- §9 MusicBrainz ------------------------------------------------------------------------------


def musicbrainz_sections(ctx: Any) -> list[TableSection]:
    isrc_where = "source = 'musicbrainz' and feed = 'isrc_lookup'"
    artist_where = "source = 'musicbrainz' and feed = 'artist'"
    total, found, unknown, newest = ctx.rows(
        "select count(*), count(*) filter (where payload ? 'recordings'), "
        "count(*) filter (where payload ? 'error'), max(ingested_at) "
        f"from raw.external_response where {isrc_where}"
    )[0]
    lookups = pd.DataFrame(
        [
            {"measure": "ISRCs looked up", "value": total},
            {"measure": "known to MusicBrainz (has `recordings`)", "value": found},
            {"measure": "unknown to MusicBrainz (404 body stored)", "value": unknown},
            {"measure": "hit rate", "value": _pct(found, total)},
            {
                "measure": "newest lookup (UTC)",
                "value": f"{newest:%Y-%m-%d %H:%M}" if newest else "none",
            },
        ]
    )
    isrc_section = TableSection(
        "9.1",
        "raw.external_response — MusicBrainz `isrc_lookup`",
        "`spot resolve-musicbrainz` looks up each ISRC found on a Spotify track, one request per "
        "ISRC, and stores MusicBrainz's response as `payload` keyed by `request_key` (the ISRC). "
        "This is how a Spotify track reaches a MusicBrainz artist (R-024).",
        tables=[("**Lookups.**", lookups)],
        schemas=[
            (
                f"**payload keys** — {DOCUMENTATION_FREE}",
                payload_key_schema(ctx, "raw", "external_response", isrc_where),
            )
        ],
        samples=[
            (
                "payload, one level flattened",
                _payload_sample(
                    ctx,
                    f"select payload from raw.external_response where {isrc_where} "
                    "order by id limit 5",
                    (),
                ),
            )
        ],
        notes=[
            (
                "NOTE",
                "An ISRC MusicBrainz does not know is stored too, as its 404 body (`error`, "
                "`help`), so it is never requested again. The join to Spotify is by ISRC, never by "
                "name.",
            ),
            (
                "",
                "MusicBrainz throttled about a third of first attempts at 1.1 s spacing (R-024: "
                "34.9%; R-040: 35.2%); every one recovered on retry.",
            ),
        ],
    )

    artists, with_genre, with_tag = ctx.rows(
        "select count(*), count(*) filter (where jsonb_array_length(payload -> 'genres') > 0), "
        "count(*) filter (where jsonb_array_length(payload -> 'tags') > 0) "
        f"from raw.external_response where {artist_where}"
    )[0]
    vocabulary = ctx.frame(
        "select tag_type, count(*) as distinct_strings, "
        "count(*) filter (where artists = 1) as used_by_one_artist, "
        "sum(artists) as artist_string_pairs "
        "from (select tag_type, tag_name, count(distinct artist_id) as artists "
        "from {stg}.int_artist_genres__current group by tag_type, tag_name) as strings "
        "group by tag_type order by tag_type"
    )
    artist_section = TableSection(
        "9.2",
        "raw.external_response — MusicBrainz `artist`, and the `genre` / `tag` split",
        f"One request per MusicBrainz artist credited on a found recording. **{artists} artists**: "
        f"{with_genre} carry at least one curated `genre`, {with_tag} at least one `tag`.",
        tables=[("**Vocabulary, as `int_artist_genres__current` reads it.**", vocabulary)],
        schemas=[
            (
                f"**payload keys** — {DOCUMENTATION_FREE}",
                payload_key_schema(ctx, "raw", "external_response", artist_where),
            )
        ],
        samples=[
            (
                "payload, one level flattened",
                _payload_sample(
                    ctx,
                    f"select payload from raw.external_response where {artist_where} "
                    "order by id limit 5",
                    (),
                ),
            )
        ],
        notes=[
            (
                "NOTE",
                "`genres` is MusicBrainz's curated vocabulary; `tags` is its open folksonomy. They "
                "are **parallel allocations, never one denominator**: `br_artist_genre` weights "
                "each 1/N within its own `tag_type` (data-contracts §4, R-024).",
            ),
            (
                "",
                "The Spotify artist response carries no `genres` key (§5.1, §6.2), so this is the "
                "project's only genre source.",
            ),
        ],
    )
    return [isrc_section, artist_section]


# --- §10 The warehouse ---------------------------------------------------------------------------


def warehouse_sections(ctx: Any) -> list[TableSection]:
    mart, stg = ctx.mart_schema, ctx.stg_schema
    profile = (ctx.profile,)
    return [
        _fact_section(ctx, mart, profile),
        _content_section(ctx, mart),
        _artist_section(ctx, mart),
        _genre_section(ctx, mart),
        _reconciliation_section(ctx, stg, profile),
    ]


def _fact_section(ctx: Any, mart: str, profile: tuple[str]) -> TableSection:
    split = ctx.frame(
        "select f.source_system, c.content_type, count(*) as plays, "
        "count(f.ms_played) as with_ms_played, sum(f.ms_played) as ms_played "
        "from {mart}.fct_play_event f join {mart}.dim_content c using (content_key) "
        "join {mart}.dim_profile p using (profile_key) where p.profile_slug = %s "
        "group by 1, 2 order by 1, 2",
        profile,
    )
    split["pct_with_ms_played"] = (100 * split["with_ms_played"] / split["plays"]).round(2)
    api = split[split["source_system"] == "api"]
    api_rows, api_with = int(api["plays"].sum()), int(api["with_ms_played"].sum())
    total, with_ms = int(split["plays"].sum()), int(split["with_ms_played"].sum())
    return TableSection(
        "10.1",
        "fct_play_event — one row per play",
        f"The spine (data-contracts §2): **{total} plays** for `{ctx.profile}`, `ms_played` on "
        f"{_pct(with_ms, total)} of them. Both loading paths land here, told apart by "
        "`source_system`.",
        tables=[("**By source and content type.**", _hours(split))],
        schemas=[("**columns**", column_schema(ctx, mart, "fct_play_event"))],
        samples=[
            (
                "latest plays",
                ctx.frame(
                    "select f.* from {mart}.fct_play_event f join {mart}.dim_profile p "
                    "using (profile_key) where p.profile_slug = %s "
                    "order by f.ended_at_utc desc limit 5",
                    profile,
                ),
            )
        ],
        notes=[
            (
                "NOTE",
                "A play captured by both paths is kept once, and the export wins: export against "
                "export matches to the second, export against API and API against API to the "
                "minute (R-037).",
            ),
            (
                "CAUTION" if api_rows > api_with else "NOTE",
                f"`ms_played` is NULL on {api_rows - api_with} of {api_rows} API plays, by "
                "contract: the API returns no duration. Every figure that sums listening time is "
                "export data.",
            ),
        ],
    )


def _content_section(ctx: Any, mart: str) -> TableSection:
    split = ctx.frame(
        "select content_type, is_resolved, count(*) as items "
        "from {mart}.dim_content where content_key > 0 group by 1, 2 order by 1, 2"
    )
    return TableSection(
        "10.2",
        "dim_content — track, episode, audiobook chapter",
        "One row per piece of content, a supertype over `track`, `episode` and "
        "`audiobook_chapter` (data-contracts §3). `is_resolved` splits it below.",
        tables=[("**Resolved vs unresolved, by type.**", split)],
        schemas=[("**columns**", column_schema(ctx, mart, "dim_content", "content_key > 0"))],
        samples=[
            (
                "resolved first, most recently seen",
                ctx.frame(
                    "select * from {mart}.dim_content where content_key > 0 "
                    "order by is_resolved desc, last_seen_at desc limit 5"
                ),
            )
        ],
        notes=[("NOTE", RESOLVED_NOTE)],
    )


RESOLVED_NOTE = (
    "`is_resolved` is true when a Spotify API object exists for the item, from recently-played or "
    "a `GET /tracks/{id}` lookup (R-037, R-039). Unresolved items carry only the names the export "
    "gave them, and no artist id, which is why most export time has no artist yet (§10.5)."
)


def _artist_section(ctx: Any, mart: str) -> TableSection:
    rows, artists, current, max_versions = ctx.rows(
        "select count(*), count(distinct artist_id), count(*) filter (where is_current), "
        "coalesce(max(versions), 0) from {mart}.dim_artist "
        "left join (select artist_id, count(*) as versions from {mart}.dim_artist "
        "where artist_key > 0 group by artist_id) as v using (artist_id) where artist_key > 0"
    )[0]
    sources = ctx.frame(
        "select coalesce(genre_source, 'none') as genre_source, count(*) as current_artists "
        "from {mart}.dim_artist where artist_key > 0 and is_current group by 1 order by 2 desc"
    )
    summary = pd.DataFrame(
        [
            {"measure": "rows (versions)", "value": rows},
            {"measure": "distinct artists", "value": artists},
            {"measure": "current rows", "value": current},
            {"measure": "most versions of one artist", "value": max_versions},
        ]
    )
    notes = [
        (
            "",
            "Artist names come from the track and album credits; the Spotify artist response adds "
            "only `images` (R-041 F2, F7).",
        )
    ]
    if max_versions == 1:
        notes.insert(
            0,
            (
                "NOTE",
                "**SCD Type 2 has not produced a second version yet:** every artist has exactly "
                "one row, because each artist's MusicBrainz genres have been observed once. The "
                "versioning is built and tested (`dim_artist_one_current_version`); it has had "
                "nothing to version.",
            ),
        )
    return TableSection(
        "10.3",
        "dim_artist — SCD Type 2 on sourced genres",
        "One row per artist version (data-contracts §3). A version closes when the artist's "
        "sourced genres change.",
        tables=[("**Versions.**", summary), ("**Genre source, current rows.**", sources)],
        schemas=[("**columns**", column_schema(ctx, mart, "dim_artist", "artist_key > 0"))],
        samples=[
            (
                "first artists by key",
                ctx.frame(
                    "select * from {mart}.dim_artist where artist_key > 0 "
                    "order by artist_key limit 5"
                ),
            )
        ],
        notes=notes,
    )


def _genre_section(ctx: Any, mart: str) -> TableSection:
    genres = ctx.frame(
        "select tag_type, count(*) as genres from {mart}.dim_genre where genre_key > 0 "
        "group by 1 order by 1"
    )
    bridge = ctx.frame(
        "select tag_type, count(*) as rows, count(distinct artist_key) as artists, "
        "count(*) filter (where abs(total - 1) > 0.000001) as artists_not_summing_to_one "
        "from (select artist_key, tag_type, sum(weight_factor) as total, count(*) as n "
        "from {mart}.br_artist_genre group by 1, 2) as w group by 1 order by 1"
    )
    return TableSection(
        "10.4",
        "dim_genre and br_artist_genre",
        "`dim_genre` holds each MusicBrainz string once per `tag_type`; `br_artist_genre` joins "
        "artists to them with `weight_factor = 1/N` within one `tag_type` (data-contracts §4).",
        tables=[("**dim_genre.**", genres), ("**br_artist_genre, weights checked.**", bridge)],
        schemas=[
            ("**dim_genre columns**", column_schema(ctx, mart, "dim_genre", "genre_key > 0")),
            ("**br_artist_genre columns**", column_schema(ctx, mart, "br_artist_genre")),
        ],
        samples=[
            (
                "dim_genre",
                ctx.frame(
                    "select * from {mart}.dim_genre where genre_key > 0 order by genre_key limit 5"
                ),
            ),
            (
                "br_artist_genre",
                ctx.frame(
                    "select * from {mart}.br_artist_genre order by artist_key, genre_key limit 5"
                ),
            ),
        ],
        notes=[
            (
                "NOTE",
                "A genre is not a radar bucket. The mapping from strings to buckets (R-015) is not "
                "built, so nothing here rolls up yet.",
            )
        ],
    )


def _reconciliation_section(ctx: Any, stg: str, profile: tuple[str]) -> TableSection:
    steps = ctx.frame(
        "select step_number, step_name, play_count, ms_played, residual_name, "
        "residual_play_count, residual_ms_played from {stg}.int_allocation_reconciliation "
        "where profile_slug = %s order by step_number",
        profile,
    )
    holds = ctx.scalar(
        "select coalesce(bool_and(prev.play_count = cur.play_count + cur.residual_play_count "
        "and coalesce(prev.ms_played, 0) = coalesce(cur.ms_played, 0) "
        "+ coalesce(cur.residual_ms_played, 0)), false) "
        "from {stg}.int_allocation_reconciliation cur "
        "join {stg}.int_allocation_reconciliation prev "
        "on prev.profile_slug = cur.profile_slug and prev.step_number = cur.step_number - 1 "
        "where cur.profile_slug = %s",
        profile,
    )
    coverage = ctx.frame(
        "select * from {stg}.int_artist_enrichment_coverage where profile_slug = %s", profile
    )
    shown = steps.copy()
    shown["hours"] = (shown["ms_played"].astype("float") / 3_600_000).round(1)
    shown["residual_hours"] = (shown["residual_ms_played"].astype("float") / 3_600_000).round(1)
    shown = shown.drop(columns=["ms_played", "residual_ms_played"])
    return TableSection(
        "10.5",
        "The allocation reconciliation — four steps and their residuals",
        "`int_allocation_reconciliation` (data-contracts §4): total listening → music → "
        "artist identified → genre allocated. Each step names what it lost from the step before.",
        tables=[
            ("**The four steps.**", shown),
            ("**Beside the chain, not in it: Spotify artist objects.**", coverage),
        ],
        schemas=[
            (
                "**columns**",
                column_schema(
                    ctx, stg, "int_allocation_reconciliation", "profile_slug = %s", profile
                ),
            )
        ],
        samples=[("every step", steps)],
        notes=[
            (
                "NOTE" if holds else "WARNING",
                "**The chain adds up at every step**, for plays and for `ms_played`, checked in "
                "this run."
                if holds
                else "**The chain does not add up** at one or more steps in this run.",
            ),
            (
                "NOTE",
                "Step 3 has been `artist_identified` (the primary artist id is known) since R-041. "
                "It used to require the Spotify artist response, which the genre join never reads. "
                "The change raised genre-allocated time from 1.32% to 19.07% of music time **with "
                "no new data**: a change of definition, not progress in resolution.",
            ),
        ],
    )


# --- Emitters ------------------------------------------------------------------------------------


def emit_export(ctx: Any) -> None:
    for section in export_sections(ctx):
        emit_table_section(section)


def emit_musicbrainz(ctx: Any) -> None:
    for section in musicbrainz_sections(ctx):
        emit_table_section(section)


def emit_warehouse(ctx: Any) -> None:
    for section in warehouse_sections(ctx):
        emit_table_section(section)
