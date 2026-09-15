"""Helpers for notebooks/02_listening_patterns.ipynb (spot-main-R-009, docs/notebook-specs.md §2).

The notebook reads the marts and never the API: nothing here imports the Spotify client. One
profile's plays are loaded once (`load_plays`) and every panel is computed from that frame. Two
contract rules shape every panel:

- every time series is clipped to the export's coverage window, with partial periods at its edges
  shaded (spec §2, chart contracts), so an export that ends mid-month does not read as a collapse;
- every panel that sums `ms_played` also shows `play_count` and `pct_rows_with_duration` for the
  same grouping (data-contracts §2), and a group with no duration stays NULL, never zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from spotify_lakehouse.inventory import _callout, _md, _show

MS_PER_HOUR = 3_600_000
# spec §2: weekly grain, not daily, for anything spanning over two years
WEEKLY_AFTER_DAYS = 2 * 365
CONTENT_CATEGORIES = {"track": "music", "episode": "podcast", "audiobook_chapter": "audiobook"}
CATEGORY_ORDER = ["music", "podcast", "audiobook"]
CATEGORY_COLORS = {"music": "#4C72B0", "podcast": "#DD8452", "audiobook": "#55A868"}
DAYPART_ORDER = ["overnight", "morning", "midday", "afternoon", "evening", "night"]
DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NO_DATA = "No data for this panel"
PARTIAL_LABEL = "partial period (window edge)"
TOP_ARTISTS_PER_YEAR = 10
ACTIVE_WITHIN_DAYS = 90
MIN_TRACK_PLAYS = 10
ONE_HIT_PEAK_SHARE = 0.8
ONE_HIT_QUIET_MONTHS = 6
# R-038's hour-of-day phase signal
PHASE_THRESHOLD_HOURS = 1.0
PHASE_MIN_MONTH_PLAYS = 100
PHASE_BASELINE_MONTHS = 6  # months either side of a month; the month itself is left out
PLATFORM_FAMILIES = (
    ("windows", "Windows"),
    ("osx", "macOS"),
    ("ios", "iOS"),
    ("android", "Android"),
    ("web_player", "web player"),
    ("webplayer", "web player"),
    ("partner", "connected device"),
    ("sonos", "connected device"),
    ("comcast", "connected device"),
    ("tizen", "connected device"),
    ("cast", "connected device"),
    ("not_applicable", "not recorded"),
    ("unknown", "not recorded"),
)


# --- Loading ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """dim_profile's coverage columns (data-contracts §3), with the window in the home timezone."""

    profile: str
    home_timezone: str | None
    export_start: datetime | None
    export_end: datetime | None
    api_start: datetime | None

    def _local(self, value: datetime) -> date:
        return value.astimezone(ZoneInfo(self.home_timezone or "UTC")).date()

    @property
    def window(self) -> tuple[date, date] | None:
        """The export window every time series is clipped to; None when no export is loaded."""
        if self.export_start is None or self.export_end is None:
            return None
        return self._local(self.export_start), self._local(self.export_end)


def load_coverage(ctx: Any) -> Coverage:
    rows = ctx.rows(
        "select home_timezone, export_coverage_start, export_coverage_end, api_coverage_start "
        "from {mart}.dim_profile where profile_slug = %s",
        (ctx.profile,),
    )
    if not rows:
        return Coverage(ctx.profile, None, None, None, None)
    timezone, start, end, api_start = rows[0]
    return Coverage(ctx.profile, timezone, start, end, api_start)


PLAY_COLUMNS = [
    "ended_at_utc",
    "source_system",
    "ms_played",
    "is_qualified_play",
    "was_skipped",
    "was_shuffled",
    "was_offline",
    "reason_start",
    "reason_end",
    "platform",
    "conn_country",
    "full_date",
    "year",
    "hour",
    "minute",
    "daypart",
    "day_of_week_name",
    "content_key",
    "content_type",
    "content_name",
    "parent_name",
    "primary_creator_name",
    "is_resolved",
    "duration_ms",
    "has_artist_id",
]


def load_plays(ctx: Any) -> pd.DataFrame:
    """Every play of the profile, one row each, with the dimension attributes the panels use."""
    frame = ctx.frame(
        "select f.ended_at_utc, f.source_system, f.ms_played, f.is_qualified_play, f.was_skipped, "
        "f.was_shuffled, f.was_offline, f.reason_start, f.reason_end, f.platform, f.conn_country, "
        "d.full_date, d.year, t.hour, t.minute, t.daypart, d.day_of_week_name, "
        "c.content_key, c.content_type, c.content_name, c.parent_name, c.primary_creator_name, "
        "c.is_resolved, c.duration_ms, "
        "exists (select 1 from {mart}.br_content_artist ca where ca.content_key = f.content_key "
        "and ca.is_primary and ca.artist_key is not null) as has_artist_id "
        "from {mart}.fct_play_event f "
        "join {mart}.dim_profile p using (profile_key) "
        "left join {mart}.dim_date d using (date_key) "
        "left join {mart}.dim_time_of_day t using (time_of_day_key) "
        "left join {mart}.dim_content c using (content_key) "
        "where p.profile_slug = %s",
        (ctx.profile,),
    )
    if frame.empty:
        frame = pd.DataFrame(columns=PLAY_COLUMNS)
    frame["full_date"] = pd.to_datetime(frame["full_date"])
    frame["ms_played"] = pd.to_numeric(frame["ms_played"], errors="coerce").astype(float)
    frame["duration_ms"] = pd.to_numeric(frame["duration_ms"], errors="coerce").astype(float)
    frame["category"] = frame["content_type"].map(CONTENT_CATEGORIES).fillna("unknown")
    return frame


# --- Pure helpers -------------------------------------------------------------------------------


def grain_for(window: tuple[date, date]) -> str:
    return "week" if (window[1] - window[0]).days > WEEKLY_AFTER_DAYS else "day"


def period_start(dates: pd.Series, grain: str) -> pd.Series:
    values = pd.to_datetime(dates)
    if grain == "day":
        return values.dt.normalize()
    if grain == "week":
        return (values - pd.to_timedelta(values.dt.dayofweek, unit="D")).dt.normalize()
    if grain == "month":
        return values.dt.to_period("M").dt.start_time
    if grain == "year":
        return values.dt.to_period("Y").dt.start_time
    raise ValueError(f"unknown grain {grain!r}")


def period_end(starts: pd.Series, grain: str) -> pd.Series:
    values = pd.to_datetime(starts)
    if grain == "day":
        return values
    if grain == "week":
        return values + pd.Timedelta(days=6)
    if grain == "month":
        return values + pd.offsets.MonthEnd(0)
    if grain == "year":
        return values + pd.offsets.YearEnd(0)
    raise ValueError(f"unknown grain {grain!r}")


def clip_to_window(
    frame: pd.DataFrame, grain: str, window: tuple[date, date], period_column: str = "period"
) -> pd.DataFrame:
    """Keep the periods that overlap the window; flag those that run past either edge as partial.

    The spec's chart contract: an export that ends mid-month must not render as a collapse in
    listening, so the edge periods are kept, flagged and shaded, and nothing outside is drawn.
    """
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    starts = pd.to_datetime(frame[period_column])
    ends = period_end(starts, grain)
    keep = (ends >= start) & (starts <= end)
    out = frame.loc[keep].copy()
    out["is_partial"] = ((starts < start) | (ends > end))[keep].to_numpy()
    return out.reset_index(drop=True)


def with_coverage(plays: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Time and counts per group, with `play_count` and `pct_rows_with_duration` beside the time."""
    frame = plays.assign(
        _qualified=plays["is_qualified_play"].fillna(False).astype(bool),
        _skipped=plays["was_skipped"].fillna(False).astype(bool),
        _shuffled=plays["was_shuffled"].fillna(False).astype(bool),
        _offline=plays["was_offline"].fillna(False).astype(bool),
    )
    out = (
        frame.groupby(by, dropna=False, observed=True)
        .agg(
            play_count=("ended_at_utc", "size"),
            rows_with_duration=("ms_played", "count"),
            # min_count=1: a group where no row carries a duration is NULL, not zero (contracts §2)
            ms_played=("ms_played", lambda values: values.sum(min_count=1)),
            qualified_play_count=("_qualified", "sum"),
            skipped_count=("_skipped", "sum"),
            shuffled_count=("_shuffled", "sum"),
            offline_count=("_offline", "sum"),
        )
        .reset_index()
    )
    out["hours"] = out["ms_played"] / MS_PER_HOUR
    out["pct_rows_with_duration"] = 100 * out["rows_with_duration"] / out["play_count"]
    return out


def _period_range(start: pd.Timestamp, end: pd.Timestamp, grain: str) -> pd.DatetimeIndex:
    frequency = {"day": "D", "week": "7D", "month": "MS", "year": "YS"}[grain]
    return pd.date_range(start, end, freq=frequency)


def time_series(
    plays: pd.DataFrame, grain: str, window: tuple[date, date], by: str | None = None
) -> pd.DataFrame:
    """Per-period coverage aggregates, every period in the window present, clipped and flagged."""
    frame = plays.assign(period=period_start(plays["full_date"], grain))
    aggregated = with_coverage(frame, ["period", *([by] if by else [])])
    if by is None:
        first = period_start(pd.Series([pd.Timestamp(window[0])]), grain).iloc[0]
        periods = pd.DataFrame({"period": _period_range(first, pd.Timestamp(window[1]), grain)})
        aggregated = periods.merge(aggregated, on="period", how="left")
        counts = [
            "play_count",
            "rows_with_duration",
            "qualified_play_count",
            "skipped_count",
            "shuffled_count",
            "offline_count",
        ]
        aggregated[counts] = aggregated[counts].fillna(0).astype(int)
        # a period with no plays was zero listening; one with plays but no duration stays NULL
        silent = aggregated["play_count"] == 0
        aggregated.loc[silent, ["ms_played", "hours"]] = 0.0
        aggregated["pct_rows_with_duration"] = (
            100 * aggregated["rows_with_duration"] / aggregated["play_count"].where(~silent)
        )
    return clip_to_window(aggregated, grain, window)


def days_in_window(series: pd.DataFrame, grain: str, window: tuple[date, date]) -> pd.Series:
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    starts = pd.to_datetime(series["period"])
    ends = period_end(starts, grain)
    low = starts.where(starts > start, start)
    high = ends.where(ends < end, end)
    return (high - low).dt.days + 1


def _angle_to_hour(angle: float) -> float:
    return (angle * 24 / (2 * math.pi)) % 24


def circular_mean_hour(hours: Any, weights: Any = None) -> float:
    """The mean of clock hours on a circle: 23:00 and 01:00 average to midnight, not noon."""
    values = np.asarray(hours, dtype=float)
    weight = np.ones_like(values) if weights is None else np.asarray(weights, dtype=float)
    angles = 2 * np.pi * values / 24
    sine, cosine = float((weight * np.sin(angles)).sum()), float((weight * np.cos(angles)).sum())
    if sine == 0 and cosine == 0:
        return math.nan
    return _angle_to_hour(math.atan2(sine, cosine))


def hour_difference(a: Any, b: Any) -> Any:
    """Signed shortest distance from b to a on a 24-hour clock, in (-12, 12]."""
    return ((np.asarray(a, dtype=float) - np.asarray(b, dtype=float) + 12) % 24) - 12


PHASE_COLUMNS = ["month", "plays", "mean_hour", "baseline_hour", "shift_hours"]


def monthly_phase(
    plays: pd.DataFrame, baseline_months: int = PHASE_BASELINE_MONTHS
) -> pd.DataFrame:
    """Each month's circular mean listening hour against the months around it (the month left out).

    A rolling baseline, not the whole-window profile, so a slow drift in habits over twelve years
    does not flag every month; a stint of a month or two stands out against its neighbours.
    """
    timed = plays.dropna(subset=["hour", "full_date"])
    if timed.empty:
        return pd.DataFrame(columns=PHASE_COLUMNS)
    hours = timed["hour"].astype(float) + timed["minute"].fillna(0).astype(float) / 60
    angles = 2 * np.pi * hours / 24
    grouped = (
        pd.DataFrame(
            {
                "month": period_start(timed["full_date"], "month"),
                "sin": np.sin(angles),
                "cos": np.cos(angles),
            }
        )
        .groupby("month")
        .agg(plays=("sin", "size"), sin=("sin", "sum"), cos=("cos", "sum"))
        .reset_index()
    )
    number = (grouped["month"].dt.year * 12 + grouped["month"].dt.month).to_numpy()
    sines, cosines = grouped["sin"].to_numpy(), grouped["cos"].to_numpy()
    grouped["mean_hour"] = [
        _angle_to_hour(math.atan2(s, c)) for s, c in zip(sines, cosines, strict=True)
    ]
    baselines = []
    for n in number:
        near = (np.abs(number - n) <= baseline_months) & (number != n)
        baselines.append(
            _angle_to_hour(math.atan2(sines[near].sum(), cosines[near].sum()))
            if near.any()
            else math.nan
        )
    grouped["baseline_hour"] = baselines
    grouped["shift_hours"] = hour_difference(grouped["mean_hour"], grouped["baseline_hour"])
    return grouped[PHASE_COLUMNS]


RUN_COLUMNS = ["first_month", "last_month", "months", "mean_shift_hours", "plays"]


def shift_runs(
    phase: pd.DataFrame,
    threshold: float = PHASE_THRESHOLD_HOURS,
    min_plays: int = PHASE_MIN_MONTH_PLAYS,
) -> pd.DataFrame:
    """Consecutive months shifted the same way by at least `threshold` hours: dates, not places."""
    flagged = phase[
        (phase["plays"] >= min_plays) & (phase["shift_hours"].abs() >= threshold)
    ].sort_values("month")
    runs: list[dict[str, Any]] = []
    for row in flagged.itertuples():
        number = row.month.year * 12 + row.month.month
        sign = row.shift_hours > 0
        if runs and number == runs[-1]["number"] + 1 and sign == runs[-1]["sign"]:
            runs[-1].update(number=number, last=row.month)
            runs[-1]["rows"].append(row)
        else:
            runs.append(
                {
                    "number": number,
                    "sign": sign,
                    "first": row.month,
                    "last": row.month,
                    "rows": [row],
                }
            )
    records = []
    for run in runs:
        plays = sum(r.plays for r in run["rows"])
        shift = sum(r.shift_hours * r.plays for r in run["rows"]) / plays
        records.append(
            {
                "first_month": run["first"],
                "last_month": run["last"],
                "months": len(run["rows"]),
                "mean_shift_hours": shift,
                "plays": plays,
            }
        )
    return pd.DataFrame(records, columns=RUN_COLUMNS)


def platform_family(value: Any) -> str:
    """A device family from Spotify's platform string; the raw string is never displayed."""
    if not isinstance(value, str) or not value.strip():
        return "not recorded"
    lowered = value.strip().lower()
    for prefix, family in PLATFORM_FAMILIES:
        if lowered.startswith(prefix):
            return family
    return "other"


# --- Presentation helpers -----------------------------------------------------------------------


def _plt() -> Any:
    import matplotlib.pyplot as plt

    return plt


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.2f}%" if whole else "n/a"


def _tidy(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in out.columns:
        if column in ("hours", "residual_hours") or column.endswith("_hours"):
            out[column] = out[column].astype(float).round(1)
        elif column.startswith("pct") or column.endswith("_share") or column == "share":
            out[column] = out[column].astype(float).round(2)
    return out.drop(
        columns=[c for c in ("ms_played", "shuffled_count", "offline_count") if c in out.columns]
    )


def _export_plays(ctx: Any, plays: pd.DataFrame, cov: Coverage, what: str) -> pd.DataFrame | None:
    export = plays[plays["source_system"] == "export"]
    if cov.window is None or export.empty:
        _callout(
            "CAUTION",
            f"**{NO_DATA}.** {what} comes only from the Extended Streaming History export, and "
            f"none is loaded for `{ctx.profile}` (§1).",
        )
        return None
    return export


def _shade_partial(ax: Any, series: pd.DataFrame, grain: str) -> None:
    partial = series.loc[series["is_partial"], "period"]
    for index, (start, end) in enumerate(zip(partial, period_end(partial, grain), strict=True)):
        ax.axvspan(
            start,
            end + pd.Timedelta(days=1),
            color="0.85",
            zorder=0,
            label=PARTIAL_LABEL if index == 0 else None,
        )


def _two_panels(plt: Any) -> tuple[Any, Any, Any]:
    fig, (top, bottom) = plt.subplots(
        2, 1, sharex=True, figsize=(9, 5.5), gridspec_kw={"height_ratios": [3, 1.3]}
    )
    return fig, top, bottom


def _coverage_panel(ax: Any, series: pd.DataFrame, grain: str) -> None:
    """The contract's companion panel: plays and the share of them carrying a duration."""
    ax.plot(series["period"], series["play_count"], color="0.3", linewidth=0.8, label="plays")
    ax.set_ylabel("plays")
    twin = ax.twinx()
    twin.plot(
        series["period"],
        series["pct_rows_with_duration"],
        color="#C44E52",
        linewidth=0.8,
        label="% with duration",
    )
    twin.set_ylim(0, 105)
    twin.set_ylabel("% with duration")
    _shade_partial(ax, series, grain)
    lines = ax.get_legend_handles_labels()
    twin_lines = twin.get_legend_handles_labels()
    ax.legend(lines[0] + twin_lines[0], lines[1] + twin_lines[1], loc="lower left", fontsize=8)


def _yearly_table(plays: pd.DataFrame, window: tuple[date, date]) -> pd.DataFrame:
    frame = plays.assign(period=period_start(plays["full_date"], "year"))
    table = clip_to_window(with_coverage(frame, ["period"]), "year", window)
    table.insert(0, "year", table.pop("period").dt.year)
    return _tidy(table)[
        [
            "year",
            "hours",
            "play_count",
            "rows_with_duration",
            "pct_rows_with_duration",
            "is_partial",
        ]
    ]


# --- Title and §1 -------------------------------------------------------------------------------


def emit_intro(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = int((plays["source_system"] == "export").sum())
    api = int((plays["source_system"] == "api").sum())
    span = f"{cov.window[0]:%Y-%m-%d} → {cov.window[1]:%Y-%m-%d}" if cov.window else "none loaded"
    _md(
        f"**Profile:** `{ctx.profile}` · **Run:** {ctx.started_at:%Y-%m-%d %H:%M} UTC · "
        f"**Session:** `{ctx.session}` (schemas `{ctx.stg_schema}`, `{ctx.mart_schema}`)\n\n"
        f"**Plays in the warehouse:** {len(plays):,} — {export:,} from the export, {api:,} "
        f"from the API. **Export window:** {span}."
    )


def emit_coverage(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    window = cov.window
    export = plays[plays["source_system"] == "export"]
    api = plays[plays["source_system"] == "api"]
    with_ms = int(plays["ms_played"].notna().sum())
    resolved = int(plays["is_resolved"].fillna(False).astype(bool).sum())
    distinct = plays.drop_duplicates("content_key")
    distinct_resolved = int(distinct["is_resolved"].fillna(False).astype(bool).sum())
    api_last = api["ended_at_utc"].max() if not api.empty else None
    years = (window[1] - window[0]).days / 365.25 if window else 0
    facts = [
        (
            "Export window (home timezone)",
            f"{window[0]:%Y-%m-%d} → {window[1]:%Y-%m-%d} ({years:.1f} years)"
            if window
            else "no export loaded",
        ),
        (
            "API window",
            f"{cov.api_start:%Y-%m-%d %H:%M} UTC (first ok poll) → "
            + (f"{api_last:%Y-%m-%d %H:%M} UTC (latest API play)" if api_last else "no API plays")
            if cov.api_start
            else "no API access recorded",
        ),
        ("Plays", f"{len(plays):,}: export {len(export):,}, API {len(api):,}"),
        (
            "pct_rows_with_duration",
            f"{_pct(with_ms, len(plays))}: {with_ms:,} of {len(plays):,} plays carry ms_played",
        ),
        (
            "Content resolution rate, plays",
            f"{_pct(resolved, len(plays))} of plays are of content resolved to a Spotify object",
        ),
        (
            "Content resolution rate, distinct items",
            f"{_pct(distinct_resolved, len(distinct))} of {len(distinct):,} distinct items",
        ),
    ]
    _show(pd.DataFrame(facts, columns=["measure", "value"]))

    if plays.empty:
        _callout("CAUTION", f"**{NO_DATA}.** The warehouse holds no plays for `{ctx.profile}`.")
        return
    _md("**By source.**")
    _show(_tidy(with_coverage(plays, ["source_system"])))
    _md("**By content category.**")
    _show(_tidy(with_coverage(plays, ["category"])))

    if window is None:
        _callout(
            "CAUTION",
            f"**No Extended Streaming History export is loaded for `{ctx.profile}`.** Listening "
            "time, skips, shuffle, offline play, devices and country exist only in the export, so "
            "every panel in §2, §3, §5 and §6 says what is missing instead of drawing. Fix: "
            "request the export at spotify.com/account/privacy, then run "
            f"`make load-export PROFILE={ctx.profile}` and `make dbt-build`.",
        )
    else:
        after = int((api["ended_at_utc"] > cov.export_end).sum()) if not api.empty else 0
        _callout(
            "NOTE",
            f"**{len(api):,} API plays carry no `ms_played`**: the API returns no duration. "
            f"Against {len(export):,} export plays, every panel that measures time is export "
            "data, and every time series below is clipped to the export window, "
            f"{window[0]:%Y-%m-%d} → {window[1]:%Y-%m-%d}. The {after:,} API plays after the "
            "export ends appear in this section only.",
        )
        edges = []
        if window[0].day != 1:
            edges.append(f"{window[0]:%B %Y} starts on day {window[0].day}")
        month_end = (pd.Timestamp(window[1]) + pd.offsets.MonthEnd(0)).date()
        if window[1] != month_end:
            edges.append(f"{window[1]:%B %Y} ends on day {window[1].day}")
        if edges:
            _md(
                f"**Partial periods at the edges:** {'; '.join(edges)}. They are kept, shaded and "
                "labelled in every chart, never drawn as a drop in listening."
            )
    _callout(
        "NOTE",
        "**Three content categories, not two.** Besides music (`track`) and podcasts (`episode`), "
        "the export carries **audiobook chapters** (`audiobook_chapter`), which are neither; §3 "
        "shows them as their own category.",
    )
    _callout(
        "WARNING",
        f"**Only {_pct(resolved, len(plays))} of plays are of resolved content.** Names come from "
        "the export for everything, but an artist *id*, and so a genre, exists only for resolved "
        "tracks: that is what limits §4.1.",
    )


# --- §2 Volume over time ------------------------------------------------------------------------


def emit_daily_minutes(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Listening time")
    if export is None:
        return
    window = cov.window
    grain = grain_for(window)
    series = time_series(export, grain, window)
    series["minutes_per_day"] = series["hours"] * 60 / days_in_window(series, grain, window)
    span = 7 if grain == "day" else 13
    series["rolling"] = series["minutes_per_day"].rolling(span, min_periods=span // 2).mean()
    plt = _plt()
    _, top, bottom = _two_panels(plt)
    top.plot(
        series["period"],
        series["minutes_per_day"],
        linewidth=0.6,
        alpha=0.55,
        color="#4C72B0",
        label=f"minutes per day, averaged by {grain}",
    )
    top.plot(
        series["period"],
        series["rolling"],
        linewidth=1.8,
        color="#1F3B73",
        label=f"{span}-{grain} rolling mean",
    )
    top.set_ylabel("minutes per day")
    _shade_partial(top, series, grain)
    top.legend(loc="upper left", fontsize=8)
    _coverage_panel(bottom, series, grain)
    plt.show()
    years = (window[1] - window[0]).days / 365.25
    _md(
        f"**Grain: {grain}.** The window spans {years:.1f} years, and the spec draws anything over "
        "two years weekly, not daily: 4,000 daily points would be noise. Each point is that "
        f"{grain}'s average minutes per day, and the overlay is a {span}-{grain} rolling mean. "
        "Edge periods are averaged over only the days inside the window."
    )
    _md("**By year**, with the plays and duration coverage behind each total.")
    _show(_yearly_table(export, window))


def emit_monthly_totals(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Listening time")
    if export is None:
        return
    series = time_series(export, "month", cov.window)
    plt = _plt()
    _, top, bottom = _two_panels(plt)
    full = series[~series["is_partial"]]
    partial = series[series["is_partial"]]
    top.bar(full["period"], full["hours"], width=25, color="#4C72B0", label="hours")
    top.bar(
        partial["period"],
        partial["hours"],
        width=25,
        color="white",
        edgecolor="#4C72B0",
        hatch="///",
        label=PARTIAL_LABEL,
    )
    top.set_ylabel("hours per month")
    top.legend(loc="upper left", fontsize=8)
    _coverage_panel(bottom, series, "month")
    plt.show()
    if not full.empty:
        busiest = full.loc[full["hours"].idxmax()]
        quietest = full.loc[full["hours"].idxmin()]
        _md(
            f"Busiest full month: **{busiest['period']:%B %Y}**, {busiest['hours']:.1f} h over "
            f"{busiest['play_count']:,} plays. Quietest: **{quietest['period']:%B %Y}**, "
            f"{quietest['hours']:.1f} h over {quietest['play_count']:,} plays."
        )


def emit_dow_daypart(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Listening time")
    if export is None:
        return
    grouped = with_coverage(export, ["day_of_week_name", "daypart"])

    def pivot(values: str) -> pd.DataFrame:
        return grouped.pivot(index="day_of_week_name", columns="daypart", values=values).reindex(
            index=DOW_ORDER, columns=DAYPART_ORDER
        )

    hours = pivot("hours")
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 4))
    image = ax.imshow(hours.fillna(0).to_numpy(), aspect="auto", cmap="Blues")
    ax.set_xticks(range(len(DAYPART_ORDER)), DAYPART_ORDER)
    ax.set_yticks(range(len(DOW_ORDER)), DOW_ORDER)
    ax.grid(False)
    ceiling = np.nanmax(hours.to_numpy()) if hours.notna().any().any() else 0
    for row, day in enumerate(DOW_ORDER):
        for column, part in enumerate(DAYPART_ORDER):
            value = hours.loc[day, part]
            if pd.notna(value):
                ax.text(
                    column,
                    row,
                    f"{value:,.0f}",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white" if value > ceiling / 2 else "black",
                )
    fig.colorbar(image, ax=ax, label="hours")
    plt.show()
    _md(
        f"Local time in `{cov.home_timezone}` for every play (data-contracts §2). Dayparts: "
        "overnight 0–4, morning 5–8, midday 9–11, afternoon 12–16, evening 17–21, night 22–23."
    )
    _md("**Plays in each cell.**")
    _show(pivot("play_count").fillna(0).astype(int))
    _md("**% of those plays carrying a duration.**")
    _show(pivot("pct_rows_with_duration").round(2))


def emit_qualified(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Qualified plays (they need `ms_played`)")
    if export is None:
        return
    grain = grain_for(cov.window)
    series = time_series(export, grain, cov.window)
    series["gap_pct"] = 100 * (1 - series["qualified_play_count"] / series["play_count"])
    plt = _plt()
    _, top, bottom = _two_panels(plt)
    top.plot(series["period"], series["play_count"], linewidth=0.8, color="0.3", label="plays")
    top.plot(
        series["period"],
        series["qualified_play_count"],
        linewidth=0.8,
        color="#4C72B0",
        label="qualified plays",
    )
    top.set_ylabel(f"plays per {grain}")
    _shade_partial(top, series, grain)
    top.legend(loc="upper left", fontsize=8)
    bottom.plot(series["period"], series["gap_pct"], linewidth=0.8, color="#C44E52", label="gap %")
    bottom.set_ylabel("not qualified, %")
    twin = bottom.twinx()
    twin.plot(
        series["period"],
        series["pct_rows_with_duration"],
        linewidth=0.8,
        color="0.5",
        linestyle="--",
    )
    twin.set_ylim(0, 105)
    twin.set_ylabel("% with duration")
    _shade_partial(bottom, series, grain)
    plt.show()
    total, qualified = int(export.shape[0]), int(export["is_qualified_play"].fillna(False).sum())
    _md(
        f"Over the window, **{qualified:,} of {total:,} plays qualified "
        f"({_pct(qualified, total)})**. A qualified play lasted at least 30 seconds or half the "
        "item's length; a play with no `ms_played` never qualifies, which is why API plays are "
        "left out here (data-contracts §2)."
    )


# --- §3 Music vs podcasts -----------------------------------------------------------------------


def _share_frame(export: pd.DataFrame, window: tuple[date, date], values: str) -> pd.DataFrame:
    series = time_series(export, "month", window, by="category")
    shares = series.pivot_table(
        index="period", columns="category", values=values, aggfunc="sum", fill_value=0
    ).reindex(columns=CATEGORY_ORDER, fill_value=0)
    totals = shares.sum(axis=1)
    return shares.div(totals.where(totals > 0), axis=0) * 100


def _emit_share(ctx: Any, plays: pd.DataFrame, cov: Coverage, values: str, label: str) -> None:
    export = _export_plays(ctx, plays, cov, f"Share of {label}")
    if export is None:
        return
    shares = _share_frame(export, cov.window, values)
    totals = time_series(export, "month", cov.window)
    plt = _plt()
    _, top, bottom = _two_panels(plt)
    top.stackplot(
        shares.index,
        [shares[c].fillna(0) for c in CATEGORY_ORDER],
        labels=CATEGORY_ORDER,
        colors=[CATEGORY_COLORS[c] for c in CATEGORY_ORDER],
    )
    top.set_ylim(0, 100)
    top.set_ylabel(f"% of {label}")
    _shade_partial(top, totals, "month")
    top.legend(loc="lower left", fontsize=8)
    _coverage_panel(bottom, totals, "month")
    plt.show()


def _category_totals(export: pd.DataFrame) -> pd.DataFrame:
    table = with_coverage(export, ["category"]).set_index("category").reindex(CATEGORY_ORDER)
    table["time_share"] = 100 * table["ms_played"] / table["ms_played"].sum()
    table["count_share"] = 100 * table["play_count"] / table["play_count"].sum()
    return table


def emit_time_share(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    _emit_share(ctx, plays, cov, "ms_played", "listening time")
    export = plays[plays["source_system"] == "export"]
    if cov.window is None or export.empty:
        return
    yearly = with_coverage(export, ["year", "category"])
    yearly["share"] = (
        100 * yearly["ms_played"] / yearly.groupby("year")["ms_played"].transform("sum")
    )
    _md("**By year and category**, with plays and duration coverage beside each share.")
    _show(
        _tidy(yearly)[
            ["year", "category", "hours", "share", "play_count", "pct_rows_with_duration"]
        ]
    )


def emit_count_share(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    _emit_share(ctx, plays, cov, "play_count", "plays")
    export = plays[plays["source_system"] == "export"]
    if cov.window is None or export.empty:
        return
    table = _category_totals(export)
    lines = [
        f"**{category}**: {row.time_share:.1f}% of listening time, {row.count_share:.1f}% of plays"
        for category, row in table.dropna(subset=["play_count"]).iterrows()
    ]
    _md(
        "**Time share and count share diverge, and that is the point:** an episode or a chapter "
        "is long, so it takes far more time per play than a song. Over the whole window: "
        + "; ".join(lines)
        + "."
    )


def emit_podcast_leaderboard(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Podcast listening")
    if export is None:
        return
    episodes = export[export["category"] == "podcast"]
    if episodes.empty:
        _callout("NOTE", f"**{NO_DATA}.** No podcast plays for `{ctx.profile}`.")
        return
    board = with_coverage(episodes, ["parent_name"])
    board["episodes"] = board["parent_name"].map(
        episodes.groupby("parent_name")["content_key"].nunique()
    )
    timed = episodes.dropna(subset=["duration_ms", "ms_played"])
    completion = (
        (timed["ms_played"] / timed["duration_ms"])
        .clip(upper=1)
        .groupby(timed["parent_name"])
        .mean()
    )
    board["mean_completion_pct"] = 100 * board["parent_name"].map(completion)
    board = (
        board.sort_values("hours", ascending=False).head(15).rename(columns={"parent_name": "show"})
    )
    _show(
        _tidy(board)[
            [
                "show",
                "hours",
                "episodes",
                "play_count",
                "pct_rows_with_duration",
                "mean_completion_pct",
            ]
        ]
    )
    with_duration = int(episodes.drop_duplicates("content_key")["duration_ms"].notna().sum())
    distinct = episodes["content_key"].nunique()
    _callout(
        "NOTE",
        f"**Mean completion needs each episode's length**, and {with_duration} of {distinct:,} "
        "episodes have one: episodes are known only from the export, which records how long a "
        "play lasted but not how long the episode is. The column is left blank where it cannot be "
        "computed, never estimated.",
    )


def emit_podcast_daypart(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Podcast listening")
    if export is None:
        return
    episodes = export[export["category"] == "podcast"]
    if episodes.empty:
        _callout("NOTE", f"**{NO_DATA}.** No podcast plays for `{ctx.profile}`.")
        return
    table = with_coverage(episodes, ["daypart"]).set_index("daypart").reindex(DAYPART_ORDER)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.bar(table.index, table["hours"].fillna(0), color=CATEGORY_COLORS["podcast"])
    ax.set_ylabel("podcast hours")
    plt.show()
    _show(_tidy(table.reset_index())[["daypart", "hours", "play_count", "pct_rows_with_duration"]])


# --- §4.1 Allocation reconciliation -------------------------------------------------------------


def _blank(frame: pd.DataFrame) -> pd.DataFrame:
    """Show an empty cell where a value does not apply, rather than NaN (R-009 F12)."""
    return frame.astype(object).where(frame.notna(), "")


def reconciliation_steps(ctx: Any) -> pd.DataFrame:
    """The profile's four allocation steps (data-contracts §4), with hours beside the ms."""
    steps = ctx.frame(
        "select step_number, step_name, play_count, ms_played, pct_rows_with_duration, "
        "residual_name, residual_play_count, residual_ms_played "
        "from {stg}.int_allocation_reconciliation where profile_slug = %s order by step_number",
        (ctx.profile,),
    )
    if not steps.empty:
        steps["hours"] = steps["ms_played"].astype(float) / MS_PER_HOUR
        steps["residual_hours"] = steps["residual_ms_played"].astype(float) / MS_PER_HOUR
    return steps


def emit_reconciliation(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    steps = reconciliation_steps(ctx)
    if steps.empty:
        _callout("CAUTION", f"**{NO_DATA}.** The reconciliation has no rows for `{ctx.profile}`.")
        return
    total = steps.loc[steps["step_number"] == 1, "hours"].iloc[0]
    music = steps.loc[steps["step_number"] == 2, "hours"].iloc[0]
    steps["pct_of_total_time"] = 100 * steps["hours"] / total if total else np.nan
    steps["pct_of_music_time"] = np.where(
        steps["step_number"] >= 2, 100 * steps["hours"] / music if music else np.nan, np.nan
    )
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ordered = steps.sort_values("step_number", ascending=False)
    ax.barh(ordered["step_name"], ordered["hours"].fillna(0), color="#4C72B0")
    for position, row in enumerate(ordered.itertuples()):
        if pd.notna(row.residual_hours) and row.residual_hours:
            ax.text(
                (row.hours or 0) + total * 0.01,
                position,
                f"lost {row.residual_hours:,.0f} h: {str(row.residual_name).split(' (')[0]}",
                va="center",
                fontsize=8,
            )
    ax.set_xlabel("hours")
    ax.set_xlim(0, total * 1.45 if total else 1)
    plt.show()
    _show(
        _blank(_tidy(steps))[
            [
                "step_number",
                "step_name",
                "hours",
                "pct_of_total_time",
                "pct_of_music_time",
                "play_count",
                "pct_rows_with_duration",
                "residual_name",
                "residual_play_count",
                "residual_hours",
            ]
        ]
    )
    allocated = steps.loc[steps["step_number"] == 4]
    unclassified = allocated["residual_hours"].iloc[0] if not allocated.empty else np.nan
    _md(
        f"**Unclassified:** {unclassified:,.1f} h of artist-identified music time belongs to a "
        "primary artist with no MusicBrainz genre. It is shown as its own residual, never folded "
        "into a bucket (data-contracts §4)."
    )
    _callout(
        "WARNING",
        "**Step 3's size is a definition, not progress.** Step 3 is `artist_identified`: the "
        "play's primary artist id is known. Until R-041 it also required Spotify's own artist "
        "response, which the genre join never reads; removing that gate moved genre-allocated time "
        "from 1.32% to 19.07% of music time **with no new data**. That time was always "
        "classifiable. What would genuinely raise step 3 is resolving more of the export's tracks.",
    )


# --- §4.2–§4.4 Genre buckets (R-015) -------------------------------------------------------------

# Colour by position in dim_genre_bucket's fixed order, so a renamed bucket keeps its colour.
# `other` is always grey.
BUCKET_PALETTE = (
    "#1f77b4",
    "#e377c2",
    "#d62728",
    "#ff7f0e",
    "#8c564b",
    "#bcbd22",
    "#9467bd",
    "#17becf",
    "#2ca02c",
    "#1b9e77",
    "#7570b3",
    "#e7298a",
)
OTHER_COLOR = "#b0b0b0"
RADAR_COLOR = "#1f3b73"
SUNBURST_TOP_ARTISTS = 5
ALLOCATION_COLUMNS = [
    "month_start",
    "year",
    "bucket_name",
    "bucket_order",
    "genre_name",
    "artist_name",
    "allocated_ms",
    "allocated_plays",
    "rows_with_duration",
]


def bucket_colors(buckets: list[str]) -> dict[str, str]:
    others = [b for b in buckets if b != "other"]
    colors = {b: BUCKET_PALETTE[i % len(BUCKET_PALETTE)] for i, b in enumerate(others)}
    colors["other"] = OTHER_COLOR
    return colors


def load_buckets(ctx: Any) -> list[str]:
    rows = ctx.rows("select bucket_name from {mart}.dim_genre_bucket order by bucket_order")
    return [row[0] for row in rows]


def load_bucket_allocation(ctx: Any) -> pd.DataFrame:
    """The profile's rows of int_genre_bucket_allocation, loaded once per context."""
    cached = getattr(ctx, "_bucket_allocation", None)
    if cached is not None:
        return cached
    frame = ctx.frame(
        "select month_start, year, bucket_name, bucket_order, genre_name, "
        "coalesce(artist_name, '(unnamed artist)') as artist_name, allocated_ms, allocated_plays, "
        "rows_with_duration from {stg}.int_genre_bucket_allocation where profile_slug = %s",
        (ctx.profile,),
    )
    if frame.empty:
        frame = pd.DataFrame(columns=ALLOCATION_COLUMNS)
    frame["month_start"] = pd.to_datetime(frame["month_start"])
    for column in ("allocated_ms", "allocated_plays"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    ctx._bucket_allocation = frame
    return frame


def bucket_shares(allocation: pd.DataFrame, buckets: list[str]) -> pd.DataFrame:
    """Each bucket's share of allocated music time, in the fixed order (spec §2: never raw ms)."""
    grouped = allocation.groupby("bucket_name")
    ms = grouped["allocated_ms"].sum(min_count=1).reindex(buckets)
    plays = grouped["allocated_plays"].sum().reindex(buckets).fillna(0.0)
    total = float(ms.sum())
    shares = 100 * ms.fillna(0) / total if total else pd.Series(np.nan, index=ms.index)
    return pd.DataFrame(
        {
            "bucket": buckets,
            "allocated_ms": ms.to_numpy(),
            "hours": (ms / MS_PER_HOUR).to_numpy(),
            "share_pct": shares.to_numpy(),
            "allocated_plays": plays.to_numpy(),
        }
    )


SUNBURST_COLUMNS = ["id", "label", "parent", "value", "bucket"]


def sunburst_nodes(
    allocation: pd.DataFrame, period: str, top_artists: int = SUNBURST_TOP_ARTISTS
) -> pd.DataFrame:
    """bucket → genre → top artists, in hours; every parent is exactly the sum of its children.

    plotly's branchvalues="total" needs that, and it is what makes a click on a slice show the
    whole of that slice. Artists past `top_artists` in a genre are grouped into one leaf.
    """
    frame = allocation.assign(hours=allocation["allocated_ms"].fillna(0) / MS_PER_HOUR)
    artists = frame.groupby(["bucket_name", "genre_name", "artist_name"], as_index=False)[
        "hours"
    ].sum()
    artists = artists[artists["hours"] > 0]
    leaves = []
    for (bucket, genre), group in artists.groupby(["bucket_name", "genre_name"], sort=False):
        ranked = group.sort_values(["hours", "artist_name"], ascending=[False, True])
        genre_id = f"{period}|{bucket}|{genre}"
        for row in ranked.head(top_artists).itertuples():
            leaves.append(
                (
                    f"{genre_id}|{row.artist_name}",
                    row.artist_name,
                    genre_id,
                    row.hours,
                    bucket,
                    genre,
                )
            )
        rest = ranked.iloc[top_artists:]
        if not rest.empty:
            noun = "artist" if len(rest) == 1 else "artists"
            leaves.append(
                (
                    f"{genre_id}|…rest",
                    f"{len(rest)} other {noun}",
                    genre_id,
                    float(rest["hours"].sum()),
                    bucket,
                    genre,
                )
            )
    leaf_frame = pd.DataFrame(leaves, columns=[*SUNBURST_COLUMNS, "genre"])
    genres = (
        leaf_frame.groupby(["parent", "bucket", "genre"], as_index=False, sort=False)["value"]
        .sum()
        .rename(columns={"parent": "id", "genre": "label"})
    )
    genres["parent"] = period + "|" + genres["bucket"]
    buckets = (
        genres.groupby(["parent", "bucket"], as_index=False, sort=False)["value"]
        .sum()
        .rename(columns={"parent": "id"})
    )
    buckets["label"] = buckets["bucket"]
    buckets["parent"] = ""
    nodes = pd.concat([buckets, genres, leaf_frame.drop(columns=["genre"])], ignore_index=True)
    return nodes[SUNBURST_COLUMNS]


def trailing_months(cov: Coverage, months: int = 12) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First and last month start of the trailing window ending in the export's last month."""
    last = pd.Timestamp(cov.window[1]).to_period("M").start_time
    return last - pd.DateOffset(months=months - 1), last


def _bucket_inputs(ctx: Any, cov: Coverage, what: str) -> tuple[pd.DataFrame, list[str]] | None:
    if cov.window is None:
        _callout(
            "CAUTION",
            f"**{NO_DATA}.** {what} needs listening time, which only the export carries, and none "
            f"is loaded for `{ctx.profile}` (§1).",
        )
        return None
    allocation = load_bucket_allocation(ctx)
    if allocation.empty or not allocation["allocated_ms"].fillna(0).sum():
        _callout(
            "CAUTION",
            f"**{NO_DATA}.** No music time for `{ctx.profile}` reaches a genre bucket yet (§4.1).",
        )
        return None
    return allocation, load_buckets(ctx)


def _in_months(frame: pd.DataFrame, column: str, first: pd.Timestamp, last: pd.Timestamp) -> Any:
    months = period_start(frame[column], "month") if column == "full_date" else frame[column]
    return (months >= first) & (months <= last)


def _print_reconciliation(
    ctx: Any,
    plays: pd.DataFrame,
    allocation: pd.DataFrame,
    first: pd.Timestamp,
    last: pd.Timestamp,
    period: str,
) -> None:
    """What every radar prints (spec §2, §4.1): the chain, its three gaps, the period's share."""
    steps = reconciliation_steps(ctx)
    if steps.empty:
        _callout("CAUTION", f"**{NO_DATA}.** No reconciliation for `{ctx.profile}` (§4.1).")
        return
    by_step = steps.set_index("step_number")
    music_hours = by_step.loc[2, "hours"]
    chain = pd.DataFrame(
        {
            "step": [
                f"{n} {name}" for n, name in zip(by_step.index, by_step["step_name"], strict=True)
            ],
            "hours": by_step["hours"].round(1).to_numpy(),
            "pct_of_music_time": [
                round(100 * h / music_hours, 2) if n >= 2 and music_hours else None
                for n, h in zip(by_step.index, by_step["hours"], strict=True)
            ],
            "gap": [
                str(r).split(" (")[0] if pd.notna(r) else None for r in by_step["residual_name"]
            ],
            "gap_hours": by_step["residual_hours"].round(1).to_numpy(),
        }
    )
    music = plays[(plays["source_system"] == "export") & (plays["category"] == "music")]
    period_music = music.loc[_in_months(music, "full_date", first, last), "ms_played"].sum()
    period_allocated = allocation.loc[
        _in_months(allocation, "month_start", first, last), "allocated_ms"
    ].sum()
    share = 100 * period_allocated / period_music if period_music else float("nan")
    unclassified = by_step.loc[4, "residual_hours"]
    _callout(
        "WARNING",
        f"**This is drawn on {share:.2f}% of the period's music time**: "
        f"{period_allocated / MS_PER_HOUR:,.0f} of {period_music / MS_PER_HOUR:,.0f} h, {period}. "
        "It describes the music whose primary artist is identified and has a MusicBrainz genre, "
        "not all listening. The chain over the whole export window, with the three gaps:",
    )
    _show(_blank(chain))
    _md(
        f"*Unclassified:* {unclassified:,.1f} h of artist-identified music belongs to artists with "
        "no MusicBrainz genre. It is in no bucket, and not in `other`, which holds only genres "
        "the mapping sends there or does not list (data-contracts §4)."
    )


def _radar(
    ax: Any, shares: pd.DataFrame, ceiling: float | None = None, labels: bool = True
) -> None:
    values = shares["share_pct"].fillna(0).to_numpy(dtype=float)
    angles = np.linspace(0, 2 * np.pi, len(values), endpoint=False)
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.plot(
        np.append(angles, angles[0]), np.append(values, values[0]), color=RADAR_COLOR, linewidth=1.5
    )
    ax.fill(
        np.append(angles, angles[0]), np.append(values, values[0]), color=RADAR_COLOR, alpha=0.25
    )
    ax.set_xticks(angles, list(shares["bucket"]) if labels else [""] * len(values), fontsize=8)
    ax.set_ylim(0, ceiling or max(5.0, float(values.max()) * 1.1))
    ax.tick_params(axis="y", labelsize=6)


def emit_bucket_radar(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    inputs = _bucket_inputs(ctx, cov, "The genre radar")
    if inputs is None:
        return
    allocation, buckets = inputs
    first, last = trailing_months(cov)
    period = allocation[_in_months(allocation, "month_start", first, last)]
    label = f"the trailing twelve months, {first:%Y-%m} → {last:%Y-%m}"
    _print_reconciliation(ctx, plays, allocation, first, last, label)
    shares = bucket_shares(period, buckets)
    plt = _plt()
    fig = plt.figure(figsize=(7.5, 7.5))
    ax = fig.add_subplot(projection="polar")
    _radar(ax, shares)
    ax.set_title(f"Share of allocated music time, {first:%Y-%m} → {last:%Y-%m}", pad=24)
    plt.show()
    if last + pd.offsets.MonthEnd(0) > pd.Timestamp(cov.window[1]):
        _md(
            f"{last:%B %Y} is a partial month: the export ends on {cov.window[1]:%Y-%m-%d}. As a "
            "share, not a total, it cannot read as a drop."
        )
    _md("**The underlying time**, bucket by bucket in the chart's order (spokes run clockwise).")
    table = shares.assign(
        allocated_ms=shares["allocated_ms"].round(0),
        hours=shares["hours"].round(1),
        share_pct=shares["share_pct"].round(2),
        allocated_plays=shares["allocated_plays"].round(1),
    )
    _show(_blank(table))


def emit_bucket_longitudinal(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    inputs = _bucket_inputs(ctx, cov, "The yearly genre radars")
    if inputs is None:
        return
    allocation, buckets = inputs
    window_first = pd.Timestamp(cov.window[0]).to_period("M").start_time
    window_last = pd.Timestamp(cov.window[1]).to_period("M").start_time
    _print_reconciliation(
        ctx, plays, allocation, window_first, window_last, "over the whole export window"
    )
    year_list = sorted(int(y) for y in allocation["year"].dropna().unique())
    years = clip_to_window(
        pd.DataFrame({"period": [pd.Timestamp(year=y, month=1, day=1) for y in year_list]}),
        "year",
        cov.window,
    )
    shares = {
        row.period.year: bucket_shares(allocation[allocation["year"] == row.period.year], buckets)
        for row in years.itertuples()
    }
    ceiling = max(float(s["share_pct"].fillna(0).max()) for s in shares.values()) * 1.05
    partial = {row.period.year for row in years.itertuples() if row.is_partial}
    plt = _plt()
    columns = 4
    rows = math.ceil(len(shares) / columns)
    fig = plt.figure(figsize=(12, 3.3 * rows))
    for index, (year, frame) in enumerate(shares.items()):
        ax = fig.add_subplot(rows, columns, index + 1, projection="polar")
        _radar(ax, frame, ceiling=ceiling, labels=False)
        hours = frame["hours"].sum()
        ax.set_title(
            f"{year}{' (partial)' if year in partial else ''} · {hours:,.0f} h", fontsize=9
        )
    plt.tight_layout()
    plt.show()
    _md(
        "Every radar uses the same scale and the same spoke order, clockwise from the top: "
        + ", ".join(f"`{b}`" for b in buckets)
        + "."
    )
    colors = bucket_colors(buckets)
    matrix = pd.DataFrame(
        {year: frame.set_index("bucket")["share_pct"] for year, frame in shares.items()}
    )
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.stackplot(
        list(matrix.columns),
        [matrix.loc[b].fillna(0).to_numpy() for b in buckets],
        labels=buckets,
        colors=[colors[b] for b in buckets],
    )
    for year in partial:
        ax.axvspan(year - 0.5, year + 0.5, color="white", alpha=0.45, zorder=3)
        ax.text(year, 101, "partial", ha="center", fontsize=7, color="0.4")
    ax.set_ylim(0, 106)
    ax.set_ylabel("% of allocated music time")
    ax.legend(loc="center left", bbox_to_anchor=(1, 0.5), fontsize=8)
    plt.show()
    music = plays[(plays["source_system"] == "export") & (plays["category"] == "music")]
    coverage = pd.DataFrame(
        {
            "music_hours": music.groupby("year")["ms_played"].sum() / MS_PER_HOUR,
            "allocated_hours": allocation.groupby("year")["allocated_ms"].sum() / MS_PER_HOUR,
            "allocated_plays": allocation.groupby("year")["allocated_plays"].sum(),
        }
    ).reindex(list(shares))
    coverage["pct_of_music_time_allocated"] = (
        100 * coverage["allocated_hours"] / coverage["music_hours"]
    )
    coverage["partial_year"] = [year in partial for year in coverage.index]
    _md("**How much of each year these shapes describe.**")
    _show(_blank(coverage.round(1).reset_index(names="year")))


def _drilldown_figure(buckets: list[str], periods: list[tuple[str, pd.DataFrame]]) -> Any:
    import plotly.graph_objects as go

    colors = bucket_colors(buckets)
    figure = go.Figure()
    for index, (label, frame) in enumerate(periods):
        nodes = sunburst_nodes(frame, label)
        figure.add_trace(
            go.Sunburst(
                ids=nodes["id"],
                labels=nodes["label"],
                parents=nodes["parent"],
                values=nodes["value"],
                branchvalues="total",
                maxdepth=2,
                marker={"colors": [colors.get(b, OTHER_COLOR) for b in nodes["bucket"]]},
                hovertemplate="<b>%{label}</b><br>%{value:,.1f} h"
                "<br>%{percentRoot:.1%} of the period<extra></extra>",
                name=label,
                visible=index == 0,
            )
        )
    buttons = [
        {
            "label": label,
            "method": "update",
            "args": [
                {"visible": [i == j for j in range(len(periods))]},
                {"title": {"text": f"Allocated music time: {label}"}},
            ],
        }
        for i, (label, _) in enumerate(periods)
    ]
    figure.update_layout(
        title={"text": f"Allocated music time: {periods[0][0]}"},
        updatemenus=[
            {
                "buttons": buttons,
                "direction": "down",
                "x": 0,
                "xanchor": "left",
                "y": 1.1,
                "yanchor": "top",
            }
        ],
        margin={"t": 90, "l": 10, "r": 10, "b": 10},
        height=680,
    )
    return figure


def emit_bucket_drilldown(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    inputs = _bucket_inputs(ctx, cov, "The genre drill-down")
    if inputs is None:
        return
    allocation, buckets = inputs
    first, last = trailing_months(cov)
    candidates = [
        (
            f"trailing 12 months ({first:%Y-%m} → {last:%Y-%m})",
            allocation[_in_months(allocation, "month_start", first, last)],
        ),
        ("all years", allocation),
    ] + [
        (str(int(year)), allocation[allocation["year"] == year])
        for year in sorted(allocation["year"].dropna().unique(), reverse=True)
    ]
    periods = [
        (label, frame) for label, frame in candidates if frame["allocated_ms"].fillna(0).sum()
    ]
    from IPython.display import HTML, display

    figure = _drilldown_figure(buckets, periods)
    display(
        HTML(
            figure.to_html(
                full_html=False,
                include_plotlyjs=True,
                div_id="genre-drilldown",
                config={"displaylogo": False, "responsive": True},
            )
        )
    )
    steps = reconciliation_steps(ctx)
    share = (
        100
        * steps.loc[steps["step_number"] == 4, "hours"].iloc[0]
        / steps.loc[steps["step_number"] == 2, "hours"].iloc[0]
        if not steps.empty
        else float("nan")
    )
    _md(
        f"**{len(periods)} periods** in the menu. Under each genre, the top {SUNBURST_TOP_ARTISTS} "
        "artists by allocated time, with the rest grouped. An artist appears under each of their "
        "genres with that genre's share of their time (1/N), so one artist can sit in several "
        "buckets. The plotly library is embedded in this page, so the chart needs no network."
    )
    _callout(
        "WARNING",
        f"Like the radars, this covers **{share:.2f}% of music time** over the whole window: music "
        "whose primary artist is identified and has a genre (§4.1). The time outside it is not in "
        "any slice, `other` included.",
    )


# --- §5 Artists and tracks ----------------------------------------------------------------------


def emit_top_artists(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Artist listening time")
    if export is None:
        return
    music = export[export["category"] == "music"]
    ranked = with_coverage(
        music.dropna(subset=["primary_creator_name"]), ["year", "primary_creator_name"]
    )
    ranked["rank"] = ranked.groupby("year")["hours"].rank(ascending=False, method="first")
    top = ranked[ranked["rank"] <= TOP_ARTISTS_PER_YEAR]
    repeat = top.groupby("primary_creator_name")["year"].nunique()
    names = (
        top[top["primary_creator_name"].isin(repeat[repeat >= 2].index)]
        .groupby("primary_creator_name")["hours"]
        .sum()
        .nlargest(12)
        .index
    )
    years = clip_to_window(
        pd.DataFrame(
            {"period": pd.to_datetime(sorted(music["year"].dropna().unique()), format="%Y")}
        ),
        "year",
        cov.window,
    )
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 5))
    for name in names:
        line = top[top["primary_creator_name"] == name].sort_values("year")
        ax.plot(line["year"], line["rank"], marker="o", linewidth=1.2)
        last = line.iloc[-1]
        ax.annotate(
            name,
            (last["year"], last["rank"]),
            xytext=(4, 0),
            textcoords="offset points",
            fontsize=7,
            va="center",
        )
    for row in years[years["is_partial"]].itertuples():
        ax.axvspan(row.period.year - 0.5, row.period.year + 0.5, color="0.9", zorder=0)
    ax.invert_yaxis()
    ax.set_yticks(range(1, TOP_ARTISTS_PER_YEAR + 1))
    ax.set_ylabel("rank by listening time")
    ax.set_xlabel("year (shaded: partial year at the window edge)")
    plt.show()
    leaders = (
        top[top["rank"] <= 5]
        .assign(
            entry=lambda f: (
                f["primary_creator_name"]
                + " ("
                + f["hours"].round(0).astype(int).astype(str)
                + " h)"
            )
        )
        .pivot(index="year", columns="rank", values="entry")
    )
    leaders.columns = [f"#{int(c)}" for c in leaders.columns]
    _md("**Top five by year.**")
    _show(leaders.reset_index())
    coverage = with_coverage(music, ["year"])
    coverage["pct_plays_with_artist_id"] = coverage["year"].map(
        100 * music.groupby("year")["has_artist_id"].mean()
    )
    _md("**Coverage behind each year:** music time, plays, duration and artist-id coverage.")
    _show(
        _tidy(coverage)[
            ["year", "hours", "play_count", "pct_rows_with_duration", "pct_plays_with_artist_id"]
        ]
    )
    name_share = _pct(int(music["primary_creator_name"].notna().sum()), len(music))
    id_share = _pct(int(music["has_artist_id"].fillna(False).sum()), len(music))
    _callout(
        "NOTE",
        f"**Artist here is a name, not an id.** It is `dim_content.primary_creator_name`, which "
        "for tracks known only from the export is the export's album-artist name. It is on "
        f"{name_share} of music plays; the id-based primary artist (`br_content_artist`) is on "
        f"only {id_share}. So same-name artists merge, a compilation counts under its album "
        "artist, and featured artists get nothing. The chart follows artists that reached the "
        "yearly top ten in more than one year.",
    )


def emit_longest_tail(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Track history")
    if export is None:
        return
    music = export[export["category"] == "music"]
    tracks = music.groupby("content_key").agg(
        track=("content_name", "first"),
        artist=("primary_creator_name", "first"),
        first_play=("full_date", "min"),
        last_play=("full_date", "max"),
    )
    tracks = tracks.join(with_coverage(music, ["content_key"]).set_index("content_key"))
    tracks["span_years"] = (tracks["last_play"] - tracks["first_play"]).dt.days / 365.25
    active_after = pd.Timestamp(cov.window[1]) - pd.Timedelta(days=ACTIVE_WITHIN_DAYS)
    tail = tracks[(tracks["play_count"] >= MIN_TRACK_PLAYS) & (tracks["last_play"] >= active_after)]
    tail = tail.nlargest(15, "span_years").reset_index()
    tail["first_play"] = tail["first_play"].dt.date
    tail["last_play"] = tail["last_play"].dt.date
    _md(
        f"Tracks played at least {MIN_TRACK_PLAYS} times and still played in the last "
        f"{ACTIVE_WITHIN_DAYS} days of the window, ranked by the time between first and last play."
    )
    if tail.empty:
        _md("No track met both conditions.")
        return
    _show(
        _tidy(tail)[
            [
                "track",
                "artist",
                "first_play",
                "last_play",
                "span_years",
                "play_count",
                "hours",
                "pct_rows_with_duration",
            ]
        ].round({"span_years": 1})
    )


def emit_one_hit_wonders(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Track history")
    if export is None:
        return
    music = export[export["category"] == "music"].assign(
        month=lambda f: period_start(f["full_date"], "month")
    )
    monthly = music.groupby(["content_key", "month"]).size().rename("plays").reset_index()
    peak = monthly.loc[monthly.groupby("content_key")["plays"].idxmax()].set_index("content_key")
    peak["lifetime_plays"] = monthly.groupby("content_key")["plays"].sum()
    peak["last_month"] = monthly.groupby("content_key")["month"].max()
    cutoff = pd.Timestamp(cov.window[1]) - pd.DateOffset(months=ONE_HIT_QUIET_MONTHS)
    hits = peak[
        (peak["lifetime_plays"] >= MIN_TRACK_PLAYS)
        & (peak["plays"] >= ONE_HIT_PEAK_SHARE * peak["lifetime_plays"])
        & (peak["last_month"] == peak["month"])
        & (peak["month"] <= cutoff)
    ]
    _md(
        f"A track played at least {MIN_TRACK_PLAYS} times, with at least "
        f"{ONE_HIT_PEAK_SHARE:.0%} of its plays in one month, never played after that month, and "
        f"with that month at least {ONE_HIT_QUIET_MONTHS} months before the window ends, so the "
        "silence afterwards is observable."
    )
    if hits.empty:
        _md("No track met all four conditions.")
        return
    names = music.groupby("content_key").agg(
        track=("content_name", "first"), artist=("primary_creator_name", "first")
    )
    table = (
        hits.join(names)
        .join(with_coverage(music, ["content_key"]).set_index("content_key"))
        .sort_values("plays", ascending=False)
        .head(15)
        .rename(columns={"plays": "plays_in_peak_month", "month": "peak_month"})
    )
    table["peak_month"] = table["peak_month"].dt.strftime("%Y-%m")
    _show(
        _tidy(table)[
            [
                "track",
                "artist",
                "peak_month",
                "plays_in_peak_month",
                "lifetime_plays",
                "hours",
                "pct_rows_with_duration",
            ]
        ]
    )


# --- §6 Behavior --------------------------------------------------------------------------------


def _yearly_shares(export: pd.DataFrame, column: str, top: int = 6) -> pd.DataFrame:
    values = export[column].fillna("not recorded")
    leaders = values.value_counts().head(top).index
    grouped = export.assign(_value=values.where(values.isin(leaders), "other"))
    counts = grouped.groupby(["year", "_value"]).size().unstack(fill_value=0)
    return counts.div(counts.sum(axis=1), axis=0) * 100


def _stacked_years(plt: Any, shares: pd.DataFrame, ylabel: str, window: tuple[date, date]) -> None:
    fig, ax = plt.subplots(figsize=(9, 4))
    bottom = np.zeros(len(shares))
    for column in shares.columns:
        ax.bar(shares.index, shares[column], bottom=bottom, label=str(column), width=0.8)
        bottom += shares[column].to_numpy()
    for edge in {window[0].year, window[1].year} & set(shares.index):
        ax.text(edge, 101, "partial", ha="center", fontsize=7, color="0.4")
    ax.set_ylim(0, 108)
    ax.set_ylabel(ylabel)
    ax.legend(loc="center left", bbox_to_anchor=(1, 0.5), fontsize=8)
    plt.show()


def emit_skip_rate(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Skips")
    if export is None:
        return
    series = time_series(export, "month", cov.window)
    series["skip_rate"] = (
        100 * series["skipped_count"] / series["play_count"].where(series["play_count"] > 0)
    )
    plt = _plt()
    _, top, bottom = _two_panels(plt)
    top.plot(
        series["period"], series["skip_rate"], linewidth=0.9, color="#C44E52", label="skip rate %"
    )
    top.set_ylabel("% of plays skipped")
    _shade_partial(top, series, "month")
    top.legend(loc="upper left", fontsize=8)
    _coverage_panel(bottom, series, "month")
    plt.show()
    shares = _yearly_shares(export, "reason_end")
    _md("**How plays ended, by year** (share of plays; the six most common reasons).")
    _stacked_years(plt, shares, "% of plays", cov.window)
    skipped = int(export["was_skipped"].fillna(False).sum())
    early = int((export["reason_end"] == "fwdbtn").sum())
    _md(
        f"`was_skipped` is Spotify's own flag: {skipped:,} plays ({_pct(skipped, len(export))}). "
        f"`reason_end = fwdbtn`, the forward button, ended {early:,} ({_pct(early, len(export))}). "
        "They measure different things, so both are shown."
    )


def emit_shuffle(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Shuffle")
    if export is None:
        return
    series = time_series(export, "month", cov.window)
    series["shuffle_share"] = (
        100 * series["shuffled_count"] / series["play_count"].where(series["play_count"] > 0)
    )
    plt = _plt()
    _, top, bottom = _two_panels(plt)
    top.plot(
        series["period"],
        series["shuffle_share"],
        linewidth=0.9,
        color="#8172B2",
        label="on shuffle %",
    )
    top.set_ylabel("% of plays on shuffle")
    _shade_partial(top, series, "month")
    top.legend(loc="upper left", fontsize=8)
    _coverage_panel(bottom, series, "month")
    plt.show()
    _md("**How plays started, by year** (share of plays; the six most common reasons).")
    _stacked_years(plt, _yearly_shares(export, "reason_start"), "% of plays", cov.window)


def emit_offline(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Offline play")
    if export is None:
        return
    series = time_series(export, "month", cov.window)
    series["offline_share"] = (
        100 * series["offline_count"] / series["play_count"].where(series["play_count"] > 0)
    )
    plt = _plt()
    _, top, bottom = _two_panels(plt)
    top.bar(
        series["period"],
        series["offline_share"].fillna(0),
        width=25,
        color="#937860",
        label="offline %",
    )
    top.set_ylabel("% of plays offline")
    _shade_partial(top, series, "month")
    top.legend(loc="upper left", fontsize=8)
    _coverage_panel(bottom, series, "month")
    plt.show()
    busiest = series[series["play_count"] >= PHASE_MIN_MONTH_PLAYS].nlargest(10, "offline_share")
    busiest = busiest.assign(month=busiest["period"].dt.strftime("%Y-%m"))
    _md(f"**The ten most offline months** (months with at least {PHASE_MIN_MONTH_PLAYS} plays).")
    _show(
        _tidy(busiest)[
            [
                "month",
                "play_count",
                "offline_share",
                "hours",
                "pct_rows_with_duration",
                "is_partial",
            ]
        ]
    )
    _callout(
        "NOTE",
        "**Offline is a proxy for travel, not a record of it.** Downloads played on a flight "
        "count, and so do a gym without signal, a phone on airplane mode, or poor reception.",
    )


def emit_platform(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Platform")
    if export is None:
        return
    families = export.assign(family=export["platform"].map(platform_family))
    counts = families.groupby(["year", "family"]).size().unstack(fill_value=0)
    shares = counts.div(counts.sum(axis=1), axis=0) * 100
    plt = _plt()
    _stacked_years(plt, shares, "% of plays", cov.window)
    _md("**Plays by year and device family.**")
    _show(counts.reset_index())
    _md(
        "Families are read from the start of Spotify's platform string (for example `windows`, "
        "`android`, `osx`); speakers, TVs and cast targets are grouped as *connected device*. The "
        "raw strings are not shown, because they can name a specific device model."
    )


def emit_relocation(ctx: Any, plays: pd.DataFrame, cov: Coverage) -> None:
    export = _export_plays(ctx, plays, cov, "Country and hour-of-day signals")
    if export is None:
        return
    _callout(
        "CAUTION",
        "**A shift is a measurement; a place is a diagnosis.** Every play's local hour is computed "
        f"in `{cov.home_timezone}`, for all years. Listening done on another clock therefore shows "
        "up as a shift: a stint two time zones east would appear about two hours *earlier*. But a "
        "new job's hours, a commute, a school term or a season of late nights leave the same "
        "signature. So this section reports **only the size and the dates** of each shift, and "
        "names no place on any chart or table: match the date ranges against trips you remember.",
    )

    _md("**Signal 1: the country each play connected from** (`conn_country`, a direct read).")
    countries = export.assign(country=export["conn_country"].fillna("not recorded"))
    table = with_coverage(countries, ["country"])
    spans = countries.groupby("country")["full_date"].agg(first_play="min", last_play="max")
    table = table.join(spans, on="country").sort_values("play_count", ascending=False)
    table["first_play"] = table["first_play"].dt.date
    table["last_play"] = table["last_play"].dt.date
    _show(
        _tidy(table)[
            ["country", "play_count", "hours", "pct_rows_with_duration", "first_play", "last_play"]
        ]
    )
    home = table.iloc[0]["country"]
    away = countries[countries["country"] != home]
    if not away.empty:
        monthly = time_series(away, "month", cov.window, by="country")
        pivot = monthly.pivot_table(
            index="period", columns="country", values="play_count", aggfunc="sum", fill_value=0
        )
        plt = _plt()
        fig, ax = plt.subplots(figsize=(9, 3.2))
        bottom = np.zeros(len(pivot))
        for column in pivot.columns:
            ax.bar(pivot.index, pivot[column], bottom=bottom, width=25, label=str(column))
            bottom += pivot[column].to_numpy()
        ax.set_ylabel(f"plays not from {home}")
        ax.legend(loc="upper left", fontsize=8)
        plt.show()
    if "ZZ" in set(table["country"]):
        _md(
            "`ZZ` is not a country: ISO 3166 reserves it for user-assigned use, and it is commonly "
            "used for an unknown location. Treat those plays as location not recorded."
        )

    _md(
        "**Signal 2: the phase of the listening day.** For each month, the circular mean of the "
        "local hour of every play (so 23:00 and 01:00 average to midnight), compared with the "
        f"same measure over the {PHASE_BASELINE_MONTHS} months either side, the month itself left "
        "out. A rolling baseline means a slow change in habits over the years does not flag every "
        f"month. Flagged: months with at least {PHASE_MIN_MONTH_PLAYS} plays whose mean moved by "
        f"{PHASE_THRESHOLD_HOURS:.0f} hour or more; consecutive flagged months moving the same way "
        "form one run."
    )
    phase = clip_to_window(monthly_phase(export), "month", cov.window, period_column="month")
    # an edge month holds only the days inside the window: its mean hour is drawn, never flagged
    runs = shift_runs(phase[~phase["is_partial"]])
    plt = _plt()
    fig, ax = plt.subplots(figsize=(9, 3.5))
    enough = phase["plays"] >= PHASE_MIN_MONTH_PLAYS
    ax.axhspan(-PHASE_THRESHOLD_HOURS, PHASE_THRESHOLD_HOURS, color="0.93", zorder=0)
    ax.plot(phase["month"], phase["shift_hours"], linewidth=0.6, color="0.6")
    ax.scatter(
        phase.loc[enough, "month"],
        phase.loc[enough, "shift_hours"],
        s=8,
        color="#4C72B0",
        label=f"month with ≥{PHASE_MIN_MONTH_PLAYS} plays",
    )
    for row in phase[phase["is_partial"]].itertuples():
        ax.axvspan(row.month, row.month + pd.offsets.MonthEnd(0), color="0.8", zorder=0)
    ax.axhline(0, color="0.3", linewidth=0.6)
    ax.set_ylabel("shift of mean hour (h)")
    ax.legend(loc="upper left", fontsize=8)
    plt.show()
    if runs.empty:
        _md(f"No month moved by {PHASE_THRESHOLD_HOURS:.0f} hour or more against its neighbours.")
        return
    shown = runs.assign(
        first_month=runs["first_month"].dt.strftime("%Y-%m"),
        last_month=runs["last_month"].dt.strftime("%Y-%m"),
        mean_shift_hours=runs["mean_shift_hours"].round(2),
    )
    _md(
        f"**{len(runs)} run(s) of shifted months.** A negative shift means listening happened "
        "earlier by the home-timezone clock than in the surrounding months."
    )
    _show(shown)
    profile = _hour_shares(export)
    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.plot(profile.index, profile.to_numpy(), color="0.2", linewidth=2.2, label="whole window")
    for number, run in enumerate(runs.head(6).itertuples(), start=1):
        inside = export[
            (period_start(export["full_date"], "month") >= run.first_month)
            & (period_start(export["full_date"], "month") <= run.last_month)
        ]
        shares = _hour_shares(inside)
        ax.plot(
            shares.index,
            shares.to_numpy(),
            linewidth=1.1,
            label=f"run {number}: {run.first_month:%Y-%m} → {run.last_month:%Y-%m}",
        )
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel(f"local hour in {cov.home_timezone}")
    ax.set_ylabel("% of plays")
    ax.legend(loc="upper left", fontsize=7)
    plt.show()


def _hour_shares(plays: pd.DataFrame) -> pd.Series:
    counts = plays["hour"].dropna().astype(int).value_counts().reindex(range(24), fill_value=0)
    return 100 * counts / counts.sum() if counts.sum() else counts.astype(float)


PANELS = (
    emit_coverage,
    emit_daily_minutes,
    emit_monthly_totals,
    emit_dow_daypart,
    emit_qualified,
    emit_time_share,
    emit_count_share,
    emit_podcast_leaderboard,
    emit_podcast_daypart,
    emit_reconciliation,
    emit_bucket_radar,
    emit_bucket_longitudinal,
    emit_bucket_drilldown,
    emit_top_artists,
    emit_longest_tail,
    emit_one_hit_wonders,
    emit_skip_rate,
    emit_shuffle,
    emit_offline,
    emit_platform,
    emit_relocation,
)
