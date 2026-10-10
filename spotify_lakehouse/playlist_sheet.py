"""One playlist as a spreadsheet row per track, for hand-annotation in Excel.

Marc's request, 2026-10-09: *"gather the songs in a playlist parameter … Output is something I can
copy/paste into excel and create some metadata."* So this is a worksheet, not a report: every column
either carries data or is deliberately blank for him to fill.

🚨 **`key` and `bpm` are blank columns BY DESIGN, not omissions.** They come from Spotify's
`/audio-features`, cut off for apps registered after 2024-11-27 (CLAUDE.md §4) — and that is
confirmed against this warehouse, not taken from the docs: **0 rows carry a `"tempo": <number>`
field**, and `danceability`, `valence`, `time_signature` and `loudness` appear in **0 rows**. Since
an audio-features payload always carries danceability alongside tempo, its absence is the control:
there is no audio-feature data here to find. The columns ship empty because annotating them by hand
is the stated point of the sheet.

**Where each column comes from, and why not from the obvious place:**

- `duration` and `isrc` come from the **playlist payload** (`int_playlist_content`,
  `fct_playlist_membership`), which carries both for 100% of tracks — *not* from `dim_content` /
  `dim_track_detail`, which carry them for 20%. Measured on `College Throwbacks`: 87 of 87 in the
  playlist payload, 18 durations and 17 ISRCs in the marts. The gap is structural, not missing
  data: 62 of those 87 reach `dim_content` by its export branch, which sets `duration_ms = null`,
  so a track the household has actually *played* loses the duration the playlist supplied.
- `album`, `year`, `explicit`, `track_no`, `album_type` need an API track lookup and are therefore
  **sparse** (17 of 87 above). `spot resolve-tracks --run` fills them in at ~1 call/s.
- `genre` is MusicBrainz's, never Spotify's — Spotify returns no `genres` key to this app at all
  (measured 55/55, R-004 §1).
- `plays`, `minutes`, `first_played`, `last_played` come from the household's own fact table and are
  the densest enrichment here — 86 of 87 above. The warehouse knows more about how these tracks were
  *listened to* than about what they are.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

PLAYLIST_ENV = "SPOT_PLAYLIST"
DEFAULT_PLAYLIST = "College Daze Revisited"

#: Blank for hand-annotation in Excel. See the module docstring: not available from any source here.
ANNOTATION_COLUMNS: tuple[str, ...] = ("key", "bpm", "notes")

#: Column order for a spreadsheet: identity, then what we know, then what Marc fills in.
SHEET_COLUMNS: tuple[str, ...] = (
    "#",
    "track_id",
    "song",
    "artist",
    "album",
    "year",
    "duration",
    "duration_ms",
    "genre",
    "plays",
    "minutes",
    "first_played",
    "last_played",
    "isrc",
    "added_at",
    "explicit",
    "track_no",
    "album_type",
    "resolved",
    *ANNOTATION_COLUMNS,
)

_SHEET_SQL = """
with target as (
    select playlist_key, playlist_name, track_total
    from {mart}.dim_playlist
    where playlist_name = %(playlist)s and is_current
),

latest as (
    select max(snapshot_date) as snapshot_date
    from {mart}.fct_playlist_membership as member
    inner join target on target.playlist_key = member.playlist_key
),

members as (
    select member.*
    from {mart}.fct_playlist_membership as member
    inner join target on target.playlist_key = member.playlist_key
    inner join latest on latest.snapshot_date = member.snapshot_date
),

-- The densest enrichment available: the household's own listening, scoped to one profile.
plays as (
    select
        fact.content_key,
        count(*) as plays,
        count(*) filter (where fact.is_qualified_play) as qualified_plays,
        sum(fact.ms_played) as ms_played,
        min(fact.ended_at_utc) as first_played,
        max(fact.ended_at_utc) as last_played
    from {mart}.fct_play_event as fact
    inner join {mart}.dim_profile as profile on profile.profile_key = fact.profile_key
    where profile.profile_slug = %(profile)s
    group by fact.content_key
),

-- One genre per track: MusicBrainz's curated `genre` list before its folksonomy `tag`s, then by
-- vote count, so a track gets its best-supported label rather than an arbitrary one.
genre as (
    select distinct on (credit.content_key)
        credit.content_key,
        dim_genre.genre_name
    from {mart}.br_content_artist as credit
    inner join {mart}.br_artist_genre as bridge on bridge.artist_key = credit.artist_key
    inner join {mart}.dim_genre as dim_genre on dim_genre.genre_key = bridge.genre_key
    where credit.is_primary
    order by
        credit.content_key,
        (dim_genre.tag_type = 'genre') desc,
        bridge.vote_count desc nulls last
)

select
    -- row_number, not `position`, and not position + 1: the payload's base is the payload's
    -- business. Measured 2026-10-09 it is 1-based, so a +1 numbered the first track 2. This is
    -- contiguous 1..N whatever the source does, which is what a spreadsheet's first column wants.
    row_number() over (order by members.position) as "#",
    split_part(members.content_uri, ':', 3) as track_id,
    coalesce(members.content_name, content.content_name) as song,
    coalesce(members.primary_creator_name, content.primary_creator_name) as artist,
    album.album_name as album,
    -- release_date is text with varying precision (data-contracts §3); a direct cast fails, but the
    -- first four characters are the year at every precision Spotify emits.
    nullif(left(album.release_date, 4), '')::int as year,
    -- the playlist payload's duration, not dim_content's: see the module docstring
    coalesce(playlist_content.duration_ms, content.duration_ms) as duration_ms,
    genre.genre_name as genre,
    plays.plays,
    plays.qualified_plays,
    plays.ms_played,
    plays.first_played,
    plays.last_played,
    coalesce(members.isrc, detail.isrc) as isrc,
    members.added_at,
    detail.is_explicit as explicit,
    detail.track_number as track_no,
    album.album_type,
    content.is_resolved as resolved
from members
left join {mart}.dim_content as content on content.content_key = members.content_key
left join {mart}.dim_track_detail as detail on detail.content_key = members.content_key
left join {mart}.dim_album as album on album.album_key = detail.album_key
left join {stg}.int_playlist_content as playlist_content
    on playlist_content.content_uri = members.content_uri
left join plays on plays.content_key = members.content_key
left join genre on genre.content_key = members.content_key
order by members.position
"""


def candidates(ctx: Any, name: str, limit: int = 15) -> pd.DataFrame:
    """Current playlists whose name contains any word of `name`, with how much is loaded.

    Shown when a name does not resolve. A playlist Spotify refused (403) or one created after the
    last `make playlists` run has no membership, so `loaded` is what says whether it is usable —
    a name that resolves but loads nothing answers a different question (R-054 F3).
    """
    words = [word for word in name.replace("'", " ").split() if len(word) > 2]
    if not words:
        words = [name]
    return ctx.frame(
        "select playlist.playlist_name, playlist.track_total, "
        "count(member.content_uri) as loaded "
        "from {mart}.dim_playlist as playlist "
        "left join {mart}.fct_playlist_membership as member "
        "  on member.playlist_key = playlist.playlist_key "
        "where playlist.is_current and playlist.playlist_name ilike any(%(patterns)s) "
        "group by 1, 2 order by 3 desc, 1 limit %(limit)s",
        {"patterns": [f"%{word}%" for word in words], "limit": limit},
    )


def duration_text(ms: Any) -> str:
    """`m:ss`, blank where unknown. Excel reads it as text, so `duration_ms` rides along too."""
    if ms is None or pd.isna(ms):
        return ""
    total = int(ms) // 1000
    return f"{total // 60}:{total % 60:02d}"


def sheet(ctx: Any, playlist_name: str, profile: str | None = None) -> pd.DataFrame:
    """One row per track in `playlist_name`, in playlist order, shaped for a spreadsheet."""
    frame = ctx.frame(_SHEET_SQL, {"playlist": playlist_name, "profile": profile or ctx.profile})
    if frame.empty:
        return pd.DataFrame(columns=list(SHEET_COLUMNS))

    frame["duration"] = frame["duration_ms"].map(duration_text)
    frame["minutes"] = (frame["ms_played"] / 60000).round(1)
    for column in ("first_played", "last_played", "added_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
    for column in ANNOTATION_COLUMNS:
        frame[column] = ""
    # A track nobody has played gets 0, not blank: "never played" is information, and Excel can sum
    # a column of numbers. `first_played` / `last_played` stay blank, which is where "never" shows.
    frame["plays"] = frame["plays"].fillna(0).astype(int)
    frame["qualified_plays"] = frame["qualified_plays"].fillna(0).astype(int)
    frame["minutes"] = frame["minutes"].fillna(0.0)
    return frame.reindex(columns=list(SHEET_COLUMNS))


def coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """How much of each column is populated — the sheet's own caveat, computed not asserted.

    A column that exists is not a column that has data, and on this project that has cost rounds.
    Anyone pasting this into Excel should see which columns are thin before building on them.
    """
    if frame.empty:
        return pd.DataFrame(columns=["column", "populated", "of", "pct"])
    rows = []
    for column in frame.columns:
        if column in ANNOTATION_COLUMNS:
            continue
        series = frame[column]
        filled = int((series.notna() & (series.astype(str).str.strip() != "")).sum())
        rows.append(
            {
                "column": column,
                "populated": filled,
                "of": len(frame),
                "pct": f"{100 * filled / len(frame):.0f}%",
            }
        )
    return pd.DataFrame(rows).sort_values("populated", ascending=False).reset_index(drop=True)


def write_sheet(frame: pd.DataFrame, playlist_name: str, directory: Path) -> Path:
    """Write the sheet as CSV for Excel. `published/` is gitignored, which is why it lands there."""
    directory.mkdir(parents=True, exist_ok=True)
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in playlist_name)
    path = directory / f"playlist_{safe}.csv"
    frame.to_csv(path, index=False)
    return path
