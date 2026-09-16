"""Helpers for notebooks/03_history_profile.ipynb (spot-main-R-050, docs/notebook-specs.md §3).

Notebook 01 answers *what tables exist*; 02 answers *questions about listening*. This one
profiles the export itself — distributions, cardinalities, gaps and oddities — so it surfaces
what nobody thought to ask. Everything is measured when the notebook runs; no count is written
into the prose.

Two rules run through it:

- **A play with no duration is not a play of zero seconds.** `ms_played = 0` is a real
  observation the export made; NULL is the API telling us nothing. `duration_profile` keeps
  them apart, and §5.1 is about the zeros (`tests/test_history.py` watches that distinction).
- Every sample passes through `redact_sample`, and no `platform` string is printed raw: they
  can name a device model, so they are grouped into families (R-009).
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from spotify_lakehouse.data_inventory import REDACTED, column_schema, redact_sample
from spotify_lakehouse.inventory import _callout, _md, _show
from spotify_lakehouse.listening import MS_PER_HOUR, period_start, platform_family

NO_DATA = "No data for this panel"
TOP_ROWS = 10
LONG_TAIL_BUCKETS = [1, 2, 3, 5, 10, 25, 50, 100]
# Above this an offline_timestamp is milliseconds, not seconds: 1e11 seconds is the year 5138.
MILLISECOND_THRESHOLD = 100_000_000_000
LONG_PLAY_HOURS = 2
# `platform` can name a device model (R-009), so no raw value is displayed anywhere — not as an
# example, not as a range, not as a modal value. §2 profiles it by family; §6.1 charts families.
SENSITIVE_COLUMNS = {"platform"}

EXPORT_COLUMNS = [
    "ended_at_utc",
    "ms_played",
    "content_type",
    "content_uri",
    "track_name",
    "album_name",
    "album_artist_name",
    "episode_name",
    "episode_show_name",
    "audiobook_title",
    "reason_start",
    "reason_end",
    "was_skipped",
    "was_shuffled",
    "was_offline",
    "was_incognito",
    "offline_timestamp_raw",
    "platform",
    "conn_country",
    "source_file",
]


def load_export(ctx: Any) -> pd.DataFrame:
    """Every export record for the profile, one row each, typed."""
    frame = ctx.frame(
        "select ended_at_utc, ms_played, content_type, content_uri, track_name, album_name, "
        "album_artist_name, episode_name, episode_show_name, audiobook_title, reason_start, "
        "reason_end, was_skipped, was_shuffled, was_offline, was_incognito, offline_timestamp_raw, "
        "platform, conn_country, source_file "
        "from {stg}.stg_export__play_record where profile_slug = %s order by ended_at_utc",
        (ctx.profile,),
    )
    if frame.empty:
        frame = pd.DataFrame(columns=EXPORT_COLUMNS)
    frame["ended_at_utc"] = pd.to_datetime(frame["ended_at_utc"], utc=True)
    frame["ms_played"] = pd.to_numeric(frame["ms_played"], errors="coerce")
    frame["day"] = frame["ended_at_utc"].dt.tz_convert("UTC").dt.date
    frame["month"] = period_start(frame["ended_at_utc"].dt.tz_localize(None), "month")
    frame["year"] = frame["ended_at_utc"].dt.year
    return frame


def duration_profile(frame: pd.DataFrame, column: str = "ms_played") -> dict[str, int]:
    """Zero, NULL and positive durations counted apart. Conflating the first two is the bug."""
    values = frame[column]
    return {
        "rows": int(len(values)),
        "zero": int((values == 0).sum()),
        "null": int(values.isna().sum()),
        "positive": int((values > 0).sum()),
    }


def _require(ctx: Any, export: pd.DataFrame, what: str) -> bool:
    if export.empty:
        _callout(
            "CAUTION",
            f"**{NO_DATA}.** {what} needs the Extended Streaming History export, and none is "
            f"loaded for `{ctx.profile}`. Fix: request it at spotify.com/account/privacy, then "
            f"`make load-export PROFILE={ctx.profile}` and `make dbt-build`.",
        )
        return False
    return True


def _plt() -> Any:
    import matplotlib.pyplot as plt

    return plt


# --- §1 Coverage & provenance --------------------------------------------------------------------


def emit_coverage(ctx: Any, export: pd.DataFrame) -> None:
    raw_records, raw_files = ctx.rows(
        "select count(*), count(distinct source_file) from raw.export_record "
        "where profile_slug = %s",
        (ctx.profile,),
    )[0]
    if not _require(ctx, export, "This profile"):
        _show(pd.DataFrame([{"raw.export_record rows": raw_records, "files": raw_files}]))
        return
    duration = duration_profile(export)
    span = export["ended_at_utc"].max() - export["ended_at_utc"].min()
    facts = [
        ("Records in raw.export_record", f"{raw_records:,} across {raw_files} files"),
        ("Rows in stg_export__play_record", f"{len(export):,}"),
        (
            "Window (UTC)",
            f"{export['ended_at_utc'].min():%Y-%m-%d} → "
            f"{export['ended_at_utc'].max():%Y-%m-%d} ({span.days / 365.25:.1f} years)",
        ),
        ("Listening time", f"{export['ms_played'].sum() / MS_PER_HOUR:,.0f} hours"),
        (
            "Durations",
            f"{duration['positive']:,} positive · {duration['zero']:,} exactly zero · "
            f"{duration['null']:,} NULL",
        ),
    ]
    _show(pd.DataFrame(facts, columns=["measure", "value"]))
    _callout(
        "NOTE",
        "**This notebook profiles the export only.** The API's rows carry no duration and are a "
        "few hundred plays against these; they belong to notebook 02's coverage section, not to a "
        "profile of the history file. Every figure below is the export's.",
    )


# --- §2 Field profile ----------------------------------------------------------------------------


def _top_platform_family(ctx: Any, table: str) -> str:
    """The most common device family, so §2 can profile `platform` without printing one."""
    counts: dict[str, int] = {}
    for value, number in ctx.rows(
        f"select platform, count(*) from {{stg}}.{table} where profile_slug = %s group by 1",
        (ctx.profile,),
    ):
        family = platform_family(value)
        counts[family] = counts.get(family, 0) + int(number)
    if not counts:
        return "—"
    family, number = max(counts.items(), key=lambda item: item[1])
    return f"{family} ({number:,})"


def field_profile(ctx: Any) -> pd.DataFrame:
    """Every column of the table, profiled.

    Cardinality and range are measured in SQL rather than from the loaded frame, so the columns the
    notebook does not select — the bookkeeping keys, and `audiobook_chapter_title` — are profiled
    too. Reading them from the frame left those rows blank, which is not the same statement as
    "this column has no values".
    """
    table = "stg_export__play_record"
    schema = column_schema(ctx, ctx.stg_schema, table, "profile_slug = %s", (ctx.profile,))
    rows = []
    for name in schema["field"]:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", str(name)):
            continue
        distinct, low, high = ctx.rows(
            f"select count(distinct {name}), min({name}::text), max({name}::text) "
            f"from {{stg}}.{table} where profile_slug = %s",
            (ctx.profile,),
        )[0]
        if name in SENSITIVE_COLUMNS:
            rows.append(
                {
                    "field": name,
                    "distinct": int(distinct),
                    "min": REDACTED,
                    "max": REDACTED,
                    "top_value": _top_platform_family(ctx, table),
                }
            )
            continue
        top = ctx.rows(
            f"select {name}::text, count(*) from {{stg}}.{table} "
            f"where profile_slug = %s and {name} is not null "
            "group by 1 order by 2 desc limit 1",
            (ctx.profile,),
        )
        rows.append(
            {
                "field": name,
                "distinct": int(distinct),
                "min": str(low)[:24] if low is not None else None,
                "max": str(high)[:24] if high is not None else None,
                "top_value": f"{top[0][0]} ({top[0][1]:,})" if top else None,
            }
        )
    merged = schema.merge(pd.DataFrame(rows), on="field", how="left")
    # column_schema samples a real value for `example`; redact it for the same reason.
    merged.loc[merged["field"].isin(SENSITIVE_COLUMNS), "example"] = REDACTED
    return merged


def emit_field_profile(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "The field profile"):
        return
    profile = field_profile(ctx)
    _md(
        f"Every column of `stg_export__play_record`, measured in this run over all "
        f"{len(export):,} rows: type and null rate from the database, and cardinality, range "
        "and most common value computed in SQL so that columns this notebook does not load "
        "are profiled too."
    )
    _show(redact_sample(profile).fillna("—"))
    sparse = profile[profile["null_rate"].astype(float) > 0.9]["field"].tolist()
    if sparse:
        _md(
            "**Columns null on more than 90% of rows:** `"
            + "`, `".join(sparse)
            + "`. Each belongs "
            "to one content type — a track row has no episode name — so a high null rate here is "
            "the shape of the file, not missing data."
        )


# --- §3 Temporal shape ---------------------------------------------------------------------------


def emit_records_per_month(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "The monthly shape"):
        return
    monthly = export.groupby("month").agg(
        records=("ms_played", "size"), hours=("ms_played", lambda s: s.sum() / MS_PER_HOUR)
    )
    plt = _plt()
    fig, (top, bottom) = plt.subplots(2, 1, sharex=True, figsize=(9, 5))
    top.bar(monthly.index, monthly["records"], width=25, color="#4C72B0")
    top.set_ylabel("records")
    bottom.bar(monthly.index, monthly["hours"], width=25, color="#55A868")
    bottom.set_ylabel("hours")
    plt.show()
    busiest = monthly["records"].idxmax()
    thinnest = monthly["records"].idxmin()
    _md(
        f"**{len(monthly)} months** carry data. Densest: **{busiest:%B %Y}** "
        f"({monthly.loc[busiest, 'records']:,} records). Thinnest: **{thinnest:%B %Y}** "
        f"({monthly.loc[thinnest, 'records']:,}). Months with no row at all: "
        f"**{_missing_months(monthly.index)}**."
    )
    _show(
        monthly.reset_index()
        .assign(
            month=lambda f: f["month"].dt.strftime("%Y-%m"), hours=lambda f: f["hours"].round(1)
        )
        .tail(TOP_ROWS)
    )


def _missing_months(index: pd.Index) -> int:
    full = pd.date_range(index.min(), index.max(), freq="MS")
    return int(len(full) - len(index))


def silent_stretches(export: pd.DataFrame, top: int = TOP_ROWS) -> pd.DataFrame:
    """The longest gaps between consecutive plays: where the history simply stops for a while."""
    times = export["ended_at_utc"].sort_values()
    gaps = times.diff()
    frame = pd.DataFrame(
        {
            "gap_days": (gaps.dt.total_seconds() / 86400).round(1),
            "from": times.shift(1),
            "to": times,
        }
    ).dropna()
    return frame.nlargest(top, "gap_days").reset_index(drop=True)


def emit_silent_stretches(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Silent stretches"):
        return
    gaps = silent_stretches(export)
    shown = gaps.assign(
        **{
            "from": gaps["from"].dt.strftime("%Y-%m-%d %H:%M"),
            "to": gaps["to"].dt.strftime("%Y-%m-%d %H:%M"),
        }
    )
    _show(shown)
    longest = gaps.iloc[0]
    _callout(
        "WARNING",
        f"**The longest silence is {longest['gap_days']:.0f} days**, "
        f"{longest['from']:%Y-%m-%d} → {longest['to']:%Y-%m-%d}. A gap in an export is not "
        "proof of silence: it is proof that Spotify recorded nothing. A phone playing local "
        "files, another "
        "service, or an account not yet in use all look identical here.",
    )


def emit_bursts(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Bursts"):
        return
    daily = export.groupby("day").agg(
        records=("ms_played", "size"), hours=("ms_played", lambda s: s.sum() / MS_PER_HOUR)
    )
    heaviest = daily.nlargest(TOP_ROWS, "hours").reset_index()
    heaviest["hours"] = heaviest["hours"].round(1)
    _show(heaviest)
    day = heaviest.iloc[0]["day"]
    that_day = export[export["day"] == day]
    top_names = (
        that_day.groupby(["album_artist_name", "track_name"])
        .agg(
            plays=("ms_played", "size"),
            hours=("ms_played", lambda s: round(s.sum() / MS_PER_HOUR, 2)),
        )
        .nlargest(5, "plays")
        .reset_index()
    )
    _md(f"**The heaviest day was {day}** — {len(that_day):,} records. What was on:")
    _show(redact_sample(top_names))


def emit_file_coverage(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Per-file coverage"):
        return
    files = (
        export.groupby("source_file")
        .agg(
            records=("ms_played", "size"),
            first_play=("ended_at_utc", "min"),
            last_play=("ended_at_utc", "max"),
        )
        .sort_values("first_play")
        .reset_index()
    )
    files["first_play"] = files["first_play"].dt.date
    files["last_play"] = files["last_play"].dt.date
    _show(files)
    overlaps = 0
    for i in range(1, len(files)):
        if files.loc[i, "first_play"] <= files.loc[i - 1, "last_play"]:
            overlaps += 1
    _md(
        f"**{len(files)} files.** {overlaps} of them start before the previous file ends — the "
        "export splits a year into two files when it is large, and the split is not always "
        "clean at a date boundary. The loader's unique key is `(profile, source_file, "
        "record_index)`, so an overlap in dates is not a duplicate row."
    )


# --- §4 Content shape ----------------------------------------------------------------------------


def emit_uri_types(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Content types"):
        return
    types = export.groupby("content_type").agg(
        records=("ms_played", "size"), hours=("ms_played", lambda s: s.sum() / MS_PER_HOUR)
    )
    types["pct_records"] = (100 * types["records"] / types["records"].sum()).round(2)
    types["pct_hours"] = (100 * types["hours"] / types["hours"].sum()).round(2)
    types["hours"] = types["hours"].round(1)
    _show(types.reset_index())
    _md(
        "A record carries exactly one of three URIs — track, episode or audiobook chapter — and "
        "`content_type` is read from whichever one is set. The share of records and the share of "
        "hours differ because an episode runs far longer than a song."
    )


def long_tail(export: pd.DataFrame, buckets: list[int] | None = None) -> pd.DataFrame:
    """How many distinct items were played once, twice, … — the shape of the tail."""
    counts = export.groupby("content_uri").size()
    rows = []
    for edge in buckets or LONG_TAIL_BUCKETS:
        rows.append(
            {
                "played_at_least": edge,
                "distinct_items": int((counts >= edge).sum()),
                "share_of_items": round(100 * (counts >= edge).sum() / len(counts), 2),
                "share_of_plays": round(100 * counts[counts >= edge].sum() / counts.sum(), 2),
            }
        )
    return pd.DataFrame(rows)


def emit_long_tail(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "The long tail"):
        return
    counts = export.groupby("content_uri").size()
    once = int((counts == 1).sum())
    share_items = 100 * once / len(counts)
    share_plays = 100 * once / len(export)
    _show(long_tail(export))
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.hist(counts.clip(upper=50), bins=50, color="#4C72B0")
    ax.set_xlabel("plays per item (clipped at 50)")
    ax.set_ylabel("distinct items")
    plt.show()
    _md(
        f"**{len(counts):,} distinct items**, of which **{once:,} ({share_items:.1f}%) were "
        f"played exactly once** and account for {share_plays:.1f}% of all records. "
        "The median item was played "
        f"{int(counts.median())} time(s); the most-played was heard {counts.max():,} times."
    )


def emit_resolution(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Resolution"):
        return
    resolved = ctx.frame(
        "select c.is_resolved, count(*) as plays, count(distinct c.content_uri) as items "
        "from {mart}.fct_play_event f join {mart}.dim_content c using (content_key) "
        "join {mart}.dim_profile p using (profile_key) where p.profile_slug = %s "
        "group by 1 order by 1",
        (ctx.profile,),
    )
    _show(resolved)
    unresolved = ctx.frame(
        "select c.content_type, count(*) as plays, count(distinct c.content_uri) as items "
        "from {mart}.fct_play_event f join {mart}.dim_content c using (content_key) "
        "join {mart}.dim_profile p using (profile_key) "
        "where p.profile_slug = %s and not c.is_resolved group by 1 order by 2 desc",
        (ctx.profile,),
    )
    _md("**What the unresolved look like**, by content type:")
    _show(unresolved)
    _callout(
        "NOTE",
        "Unresolved means no Spotify API object has been fetched for that URI, so it carries the "
        "export's own names and no artist id. Resolution is one API call per id (R-037), and only "
        "the most-listened 500 tracks have been resolved so far, which is why the resolved items "
        "are few but their play count is not.",
    )


def emit_repeats(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Repeat behaviour"):
        return
    tracks = export[export["content_type"] == "track"]
    per_track = tracks.groupby(["album_artist_name", "track_name"]).agg(
        plays=("ms_played", "size"),
        hours=("ms_played", lambda s: round(s.sum() / MS_PER_HOUR, 1)),
        first_play=("ended_at_utc", "min"),
        last_play=("ended_at_utc", "max"),
    )
    per_track["span_years"] = (
        (per_track["last_play"] - per_track["first_play"]).dt.days / 365.25
    ).round(1)
    top = per_track.nlargest(TOP_ROWS, "plays").reset_index()
    top["first_play"] = top["first_play"].dt.date
    top["last_play"] = top["last_play"].dt.date
    _md("**The most-replayed tracks**, with how long they have been in rotation:")
    _show(redact_sample(top))
    _md(
        f"Median span between a track's first and last play: "
        f"**{per_track['span_years'].median():.1f} years** across {len(per_track):,} tracks."
    )


# --- §5 Quality & oddities -----------------------------------------------------------------------


def emit_oddities(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "The quality checks"):
        return
    duration = duration_profile(export)
    _md("### 5.1 Zero-duration plays, and why they are not NULLs")
    _show(
        pd.DataFrame(
            [
                {"durations": "positive", "records": duration["positive"]},
                {"durations": "exactly zero", "records": duration["zero"]},
                {"durations": "NULL", "records": duration["null"]},
            ]
        )
    )
    zero = export[export["ms_played"] == 0]
    by_reason = (
        zero.groupby("reason_end").size().nlargest(5).rename("records").reset_index()
        if not zero.empty
        else pd.DataFrame(columns=["reason_end", "records"])
    )
    _show(by_reason)
    _callout(
        "WARNING",
        f"**{duration['zero']:,} records have `ms_played = 0` and "
        f"{duration['null']:,} have NULL.** "
        "They are different facts: zero is the export saying a play started and produced no "
        "listening time; NULL is the API saying nothing at all, and no export row carries one. "
        "Anything that averages duration must exclude the NULLs and decide deliberately about the "
        "zeros — they drag a mean down by a real amount.",
    )

    _md("### 5.2 Empty reason_start and reason_end")
    empty_start = int(export["reason_start"].isna().sum())
    empty_end = int(export["reason_end"].isna().sum())
    reason_rows = []
    for name in ("reason_start", "reason_end"):
        values = export[name]
        top = values.value_counts().head(1)
        reason_rows.append(
            {
                "column": name,
                "null_records": int(values.isna().sum()),
                "share_of_records": round(100 * values.isna().mean(), 4),
                "distinct_values": int(values.nunique()),
                "most_common": f"{top.index[0]} ({top.iloc[0]:,})" if len(top) else None,
            }
        )
    _show(pd.DataFrame(reason_rows))
    _md(
        f"`reason_start` is NULL on **{empty_start:,}** records and `reason_end` on "
        f"**{empty_end:,}**. Staging turns the export's empty string into NULL (R-003 F6), "
        "so these are rows the file left blank, not rows staging lost."
    )

    _md("### 5.3 offline_timestamp's mixed units")
    offline = export["offline_timestamp_raw"].dropna()
    looks_ms = int((offline > MILLISECOND_THRESHOLD).sum())
    _show(
        pd.DataFrame(
            [
                {"measure": "non-null values", "value": f"{len(offline):,}"},
                {"measure": "look like milliseconds", "value": f"{looks_ms:,}"},
                {"measure": "smallest value", "value": f"{int(offline.min()):,}"},
                {"measure": "largest value", "value": f"{int(offline.max()):,}"},
            ]
        )
    )
    _callout(
        "WARNING",
        f"**{looks_ms:,} of {len(offline):,} values are milliseconds and the rest are seconds.** "
        "The column is kept raw (`offline_timestamp_raw`) for exactly this reason: converting it "
        "without checking the magnitude would date those plays to the year 54000-odd. Nothing in "
        "the warehouse reads it yet.",
    )

    _md("### 5.4 Duplicate natural keys")
    keys = export.groupby(["content_uri", "ended_at_utc"]).size()
    colliding = keys[keys > 1]
    _show(
        pd.DataFrame(
            [
                {"measure": "keys with more than one row", "value": f"{len(colliding):,}"},
                {"measure": "rows involved", "value": f"{int(colliding.sum()):,}"},
                {
                    "measure": "rows that collapse",
                    "value": f"{int(colliding.sum() - len(colliding)):,}",
                },
            ]
        )
    )
    _callout(
        "WARNING",
        "**Two export rows with the same content and the same second are indistinguishable**, so "
        "the dedupe keeps one (data-contracts §2, amended R-037). That is correct at second "
        "precision and it does lose rows: the count above is how many.",
    )

    _md("### 5.5 reason_end = trackerror")
    errors = export[export["reason_end"] == "trackerror"]
    error_zero = int((errors["ms_played"] == 0).sum())
    _md(
        f"**{len(errors):,} records** ended with `trackerror`, of which **{error_zero:,} "
        "played for zero milliseconds**. They are real rows in the file and they stay in the "
        "warehouse; a "
        "count of plays includes them, and a sum of hours barely notices them."
    )
    _show(errors.groupby("year").size().rename("trackerror_records").reset_index().tail(TOP_ROWS))

    _md("### 5.6 What else the profiling found")
    findings = _other_oddities(export)
    if findings.empty:
        _md(
            "Nothing beyond §5.1–§5.5. The profiling ran over every column and found no further "
            "anomaly worth a section."
        )
    else:
        _show(findings)


def _other_oddities(export: pd.DataFrame) -> pd.DataFrame:
    """Anything the profiling turns up that §5.1–§5.5 do not name."""
    rows = []
    long_plays = export[export["ms_played"] > LONG_PLAY_HOURS * MS_PER_HOUR]
    if not long_plays.empty:
        longest = long_plays["ms_played"].max() / MS_PER_HOUR
        rows.append(
            {
                "what": f"plays longer than {LONG_PLAY_HOURS} hours",
                "records": len(long_plays),
                "detail": f"longest {longest:.1f} h; audiobooks and podcasts, not songs",
            }
        )
    blank_country = int(export["conn_country"].isna().sum() + (export["conn_country"] == "").sum())
    if blank_country:
        rows.append(
            {
                "what": "records with no conn_country",
                "records": blank_country,
                "detail": "no country recorded",
            }
        )
    incognito = int(export["was_incognito"].fillna(False).sum())
    if incognito:
        rows.append(
            {
                "what": "played in incognito mode",
                "records": incognito,
                "detail": "Spotify still exports them; they are in every figure here",
            }
        )
    skipped_no_reason = int(
        (export["was_skipped"].fillna(False) & export["reason_end"].isna()).sum()
    )
    if skipped_no_reason:
        rows.append(
            {
                "what": "flagged skipped with no reason_end",
                "records": skipped_no_reason,
                "detail": "the flag and the reason disagree",
            }
        )
    unknown_reason = int((export["reason_end"] == "unknown").sum())
    if unknown_reason:
        rows.append(
            {
                "what": "reason_end = 'unknown'",
                "records": unknown_reason,
                "detail": "the export's own placeholder, not a NULL",
            }
        )
    return pd.DataFrame(rows, columns=["what", "records", "detail"])


# --- §6 Platform & context -----------------------------------------------------------------------


def emit_platforms(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Platform strings"):
        return
    families = export.assign(family=export["platform"].map(platform_family))
    counts = families.groupby(["year", "family"]).size().unstack(fill_value=0)
    shares = counts.div(counts.sum(axis=1), axis=0) * 100
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 4))
    bottom = np.zeros(len(shares))
    for column in shares.columns:
        ax.bar(shares.index, shares[column], bottom=bottom, label=str(column))
        bottom += shares[column].to_numpy()
    ax.set_ylabel("% of records")
    ax.legend(loc="center left", bbox_to_anchor=(1, 0.5), fontsize=8)
    plt.show()
    _show(counts.reset_index())
    _md(
        f"**{export['platform'].nunique():,} distinct platform strings** are grouped into "
        f"{shares.shape[1]} families. The raw strings are not shown: they can name a specific "
        "device model (R-009)."
    )


def emit_countries(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "conn_country"):
        return
    countries = export.groupby(export["conn_country"].fillna("(none)")).agg(
        records=("ms_played", "size"),
        first_play=("ended_at_utc", "min"),
        last_play=("ended_at_utc", "max"),
    )
    countries["first_play"] = countries["first_play"].dt.date
    countries["last_play"] = countries["last_play"].dt.date
    _show(countries.sort_values("records", ascending=False).reset_index())
    _md(
        "`ZZ` is not a country: ISO 3166 reserves it for user-assigned use and it is commonly used "
        "for an unknown location. Treat those records as location not recorded (R-009 §6.5 reads "
        "the same column for relocation signals)."
    )


def emit_behaviour(ctx: Any, export: pd.DataFrame) -> None:
    if not _require(ctx, export, "Behaviour rates"):
        return
    flags = export.assign(
        skipped=export["was_skipped"].fillna(False),
        shuffled=export["was_shuffled"].fillna(False),
        offline=export["was_offline"].fillna(False),
        incognito=export["was_incognito"].fillna(False),
    )
    yearly = flags.groupby("year")[["skipped", "shuffled", "offline", "incognito"]].mean() * 100
    yearly["records"] = flags.groupby("year").size()
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 4))
    for column in ("skipped", "shuffled", "offline", "incognito"):
        ax.plot(yearly.index, yearly[column], marker="o", linewidth=1.2, label=column)
    ax.set_ylabel("% of records")
    ax.legend(fontsize=8)
    plt.show()
    _show(yearly.round(2).reset_index())


PANELS = (
    emit_coverage,
    emit_field_profile,
    emit_records_per_month,
    emit_silent_stretches,
    emit_bursts,
    emit_file_coverage,
    emit_uri_types,
    emit_long_tail,
    emit_resolution,
    emit_repeats,
    emit_oddities,
    emit_platforms,
    emit_countries,
    emit_behaviour,
)
