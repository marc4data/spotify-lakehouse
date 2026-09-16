"""Playlists: ingest, and the three-tier comparison asked of them (spot-main-R-054).

Two halves, deliberately separable.

**Ingest.** `GET /me/playlists` and `GET /playlists/{id}/items`, both paged to exhaustion, one
stored response per page. Other people's identities never reach `raw`: `raw_store.scrub` discards
`items[].owner.*` and `items[].added_by` before anything is written (R-054 — discard beats redact,
because a field that is never written cannot leak from a table nobody thought to check).

🚨 **The lock is `spot_playlists`, never `spot_refresh` (R-040).** A long job holding the poller's
lock stops the 30-minute poll, and `recently-played` keeps 50 items and cannot page backward, so a
missed poll is lost plays that no later run can recover.

**Paging without trusting the URL.** CLAUDE.md §5: a `next` link is untrusted input, because the
client attaches Marc's bearer token to whatever it requests. This module computes its own `offset`
and uses the response's `next` only as a stop signal, passing it through
`paging.offset_next_params` as a cross-check — so a link pointing somewhere unexpected is a finding
rather than a fetch.

**Comparison.** `data-contracts.md` §5 contracts three tiers, most reliable first: `content_uri`,
`isrc`, then the loose `content_match_key`. `miss_count` is **directional** — "47 misses between A
and B" is meaningless; "47 tracks in A that are not in B" is the answer — and every output here
carries its direction in a label, which `tests/test_playlists.py::test_misses_are_directional`
exists to keep true.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import psycopg

from spotify_lakehouse.api import SpotifyApiError, SpotifyClient
from spotify_lakehouse.paging import PLAYLISTS_PATH, UnsafeNextLink, offset_next_params
from spotify_lakehouse.raw_store import feed_for_endpoint, insert_response, scrub, write_response

LOCK_NAME = "spot_playlists"
PLAYLIST_PAGE_LIMIT = 50  # Spotify's maximum for /me/playlists
ITEM_PAGE_LIMIT = 100  # Spotify's maximum for /playlists/{id}/items
# Measured, not configured: R-039 ran 1.13 s per call against 1.0 s client pacing (R-040 F2).
SECONDS_PER_CALL_ESTIMATE = 1.13
ITEMS_ROUTE = "items"
TRACKS_ROUTE = "tracks"  # the older route; R-008 F3 observed it serving when /items did not

# §5's three tiers, most reliable first. The gap between them is itself the story.
TIERS = ("content_uri", "isrc", "content_match_key")
TIER_LABELS = {
    "content_uri": "tier 1 — same Spotify URI (strict)",
    "isrc": "tier 2 — same recording by ISRC (API-resolved tracks only)",
    "content_match_key": "tier 3 — normalized name + primary creator (loose)",
}


@dataclass(frozen=True)
class PlaylistRef:
    playlist_id: str
    name: str
    track_total: int
    is_collaborative: bool
    is_public: bool | None
    snapshot_id: str


@dataclass
class LoadResult:
    """Everything the round has to report, counted rather than estimated."""

    playlists: int = 0
    playlist_pages: int = 0
    items_seen: int = 0
    item_pages: int = 0
    routes: dict[str, str] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    unsafe_next: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    calls: int = 0

    @property
    def route_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for route in self.routes.values():
            counts[route] = counts.get(route, 0) + 1
        return counts


def page_key(playlist_id: str | None, page: int) -> str:
    """A `write_response` file key: letters and digits only, one per page per playlist."""
    return f"{playlist_id}p{page}" if playlist_id else f"p{page}"


def _store(
    conn: psycopg.Connection,
    profile: str,
    run_id: str,
    endpoint: str,
    payload: dict[str, Any],
    key: str,
) -> None:
    feed = feed_for_endpoint(endpoint)
    clean, _ = scrub(endpoint, payload)
    source_file = write_response(profile, feed, clean, run_id, datetime.now(UTC), key=key)
    insert_response(conn, clean, source_file, profile, feed)


def _next_offset(
    payload: dict[str, Any], path: str, offset: int, limit: int, result: LoadResult
) -> int | None:
    """The next offset, or None when the page was the last one.

    The URL is never requested. It is parsed and checked; the offset we use is our own, and a link
    that disagrees or points elsewhere is recorded instead of followed.
    """
    next_url = payload.get("next")
    if not next_url:
        return None
    expected = offset + limit
    try:
        parsed = offset_next_params(str(next_url), expected_path=path)
    except UnsafeNextLink as exc:
        result.unsafe_next.append(f"{path}: {exc}")
        return None
    if int(parsed["offset"]) != expected:
        result.unsafe_next.append(
            f"{path}: next says offset={parsed['offset']}, expected {expected}; stopping"
        )
        return None
    return expected


def fetch_playlists(
    api: SpotifyClient, conn: psycopg.Connection, profile: str, run_id: str, result: LoadResult
) -> list[PlaylistRef]:
    """Every playlist the token can see, paged to exhaustion. One stored response per page."""
    refs: list[PlaylistRef] = []
    offset = 0
    page = 0
    while True:
        payload = api.get(PLAYLISTS_PATH, {"limit": PLAYLIST_PAGE_LIMIT, "offset": offset})
        page += 1
        result.playlist_pages = page
        _store(conn, profile, run_id, PLAYLISTS_PATH, payload, page_key(None, page))
        for item in payload.get("items") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            refs.append(
                PlaylistRef(
                    playlist_id=str(item["id"]),
                    name=str(item.get("name") or ""),
                    track_total=int((item.get("tracks") or {}).get("total") or 0),
                    is_collaborative=bool(item.get("collaborative")),
                    is_public=item.get("public"),
                    snapshot_id=str(item.get("snapshot_id") or ""),
                )
            )
        following = _next_offset(payload, PLAYLISTS_PATH, offset, PLAYLIST_PAGE_LIMIT, result)
        if following is None:
            break
        offset = following
    result.playlists = len(refs)
    return refs


def _fetch_item_pages(
    api: SpotifyClient,
    conn: psycopg.Connection,
    profile: str,
    run_id: str,
    ref: PlaylistRef,
    path: str,
    result: LoadResult,
) -> int:
    """Every page of one route. Raises if the FIRST page is refused; a later refusal is recorded.

    The distinction matters: a first-page refusal means try the other route, while a refusal
    half-way through means keep what this playlist gave and move on.
    """
    offset = 0
    page = 0
    while True:
        try:
            payload = api.get(path, {"limit": ITEM_PAGE_LIMIT, "offset": offset})
        except SpotifyApiError as exc:
            if page == 0:
                raise
            result.partial.append(f"{ref.playlist_id}: {path} stopped after page {page} ({exc})")
            return page
        page += 1
        result.item_pages += 1
        _store(conn, profile, run_id, path, payload, page_key(ref.playlist_id, page))
        result.items_seen += len(payload.get("items") or [])
        following = _next_offset(payload, path, offset, ITEM_PAGE_LIMIT, result)
        if following is None:
            return page
        offset = following


def fetch_items(
    api: SpotifyClient,
    conn: psycopg.Connection,
    profile: str,
    run_id: str,
    ref: PlaylistRef,
    result: LoadResult,
) -> None:
    """One playlist's items, with the `/tracks` fallback (R-008 F3).

    🚨 A playlist this token cannot read does NOT stop the load. R-054 measured Spotify refusing
    both routes with 403 for at least one playlist; aborting there would have left the other
    playlists unfetched and the round unable to answer Marc's question at all. The refusal is
    recorded and reported, which is the honest answer, and the run continues.
    """
    for route in (ITEMS_ROUTE, TRACKS_ROUTE):
        path = f"/playlists/{ref.playlist_id}/{route}"
        try:
            _fetch_item_pages(api, conn, profile, run_id, ref, path, result)
        except SpotifyApiError as exc:
            result.refusals.append(f"{ref.playlist_id} via /{route}: {exc.status}")
            if route == ITEMS_ROUTE:
                # One route's refusal is not a capability claim: try the documented older route.
                result.fallbacks.append(f"{ref.playlist_id}: /items {exc.status}, trying /tracks")
            continue
        result.routes[ref.playlist_id] = route
        return
    result.skipped.append(ref.playlist_id)


def load(api: SpotifyClient, conn: psycopg.Connection, profile: str, run_id: str) -> LoadResult:
    """Fetch and store every playlist and every item. Measures its own cost."""
    result = LoadResult()
    started = time.monotonic()
    for ref in fetch_playlists(api, conn, profile, run_id, result):
        fetch_items(api, conn, profile, run_id, ref, result)
    result.elapsed_seconds = time.monotonic() - started
    result.calls = api.calls
    return result


# --- §5 comparison: three tiers, and every answer names its direction ---------------------------


class AmbiguousPlaylist(ValueError):
    """A name matched zero playlists, or more than one. The caller prints candidates and stops."""


@dataclass(frozen=True)
class PlaylistMatch:
    """What a name resolved to, and *how* — because a substring match can be incidental.

    R-054 measured the failure this exists to prevent: "Connor" is a substring of "O'Connor", so a
    unique-substring rule resolved it to a Sinéad O'Connor playlist and answered a question nobody
    asked. The match was wrong but not ambiguous, so no exception could have caught it. The defence
    is that `exact` is False and every caller shows it.
    """

    needle: str
    name: str
    exact: bool
    candidates: list[str]

    @property
    def warning(self) -> str | None:
        if self.exact:
            return None
        return (
            f"{self.needle!r} has no playlist of that exact name. It was matched by substring to "
            f"{self.name!r} — check that this is the playlist you meant."
        )


# Spotify stores what the user typed, and phone keyboards type a typographic apostrophe. Marc's
# playlist is `Connor\u2019s Playlist`; searching for `Connor's Playlist` with an ASCII apostrophe
# matches nothing at all (R-054, Marc 2026-09-16: "compare on a form that tolerates both").
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "`": "'"})


def normalise_name(value: object) -> str:
    """Casefolded, trimmed, and with every apostrophe variant folded to ASCII."""
    if not isinstance(value, str):
        return ""
    return value.translate(_APOSTROPHES).strip().lower()


def candidates(frame: pd.DataFrame, needle: str, *, column: str = "playlist_name") -> list[str]:
    """Every playlist name containing `needle`, ignoring case and apostrophe style."""
    target = normalise_name(needle)
    normalised = frame[column].map(normalise_name)
    return sorted(frame.loc[normalised.str.contains(target, regex=False), column].unique())


def resolve(frame: pd.DataFrame, needle: str, *, column: str = "playlist_name") -> PlaylistMatch:
    """Resolve a name, preferring an exact match and never resolving silently.

    An exact (case-insensitive, trimmed) name wins outright. Otherwise a unique substring match is
    returned with `exact=False` and a warning the caller must display; zero or several raise.
    """
    found = candidates(frame, needle, column=column)
    target = normalise_name(needle)
    exact = [name for name in found if normalise_name(name) == target]
    if len(exact) == 1:
        return PlaylistMatch(needle, exact[0], True, found)
    if len(exact) > 1:
        raise AmbiguousPlaylist(f"{needle!r} matches {len(exact)} playlists exactly: {exact}.")
    if len(found) != 1:
        raise AmbiguousPlaylist(
            f"{needle!r} matched {len(found)} playlist(s): {found or 'none'}. "
            "Name it exactly, or pick from the candidates shown."
        )
    return PlaylistMatch(needle, found[0], False, found)


def resolve_one(frame: pd.DataFrame, needle: str, *, column: str = "playlist_name") -> str:
    """The resolved name only. Prefer `resolve`, whose result says whether the match was exact."""
    return resolve(frame, needle, column=column).name


@dataclass(frozen=True)
class DirectionalMiss:
    """Tracks in `left` that are not in `right`, at one tier. The direction is part of the value."""

    left_name: str
    right_name: str
    tier: str
    rows: pd.DataFrame
    left_size: int
    left_with_key: int

    @property
    def miss_count(self) -> int:
        return len(self.rows)

    @property
    def label(self) -> str:
        return f"Tracks in {self.left_name} that are not in {self.right_name}"

    @property
    def coverage_note(self) -> str:
        if self.left_size == 0:
            return "no tracks"
        share = 100 * self.left_with_key / self.left_size
        return (
            f"{self.left_with_key} of {self.left_size} tracks in {self.left_name} "
            f"carry a {self.tier} ({share:.1f}%)"
        )


def misses(
    left: pd.DataFrame, right: pd.DataFrame, *, left_name: str, right_name: str, tier: str
) -> DirectionalMiss:
    """In `left`, not in `right`, matched on `tier`. A row with no key at this tier is a miss.

    A row with no key cannot be shown to be present in `right`, so calling it a match would be an
    assumption; it is reported as a miss and `coverage_note` says how many such rows there are.
    """
    if tier not in TIERS:
        raise ValueError(f"{tier!r} is not one of {TIERS}")
    keys = set(right[tier].dropna()) if len(right) else set()
    has_key = left[tier].notna() if len(left) else pd.Series(dtype=bool)
    present = left[tier].isin(keys) & has_key if len(left) else pd.Series(dtype=bool)
    rows = left[~present] if len(left) else left
    return DirectionalMiss(
        left_name=left_name,
        right_name=right_name,
        tier=tier,
        rows=rows.reset_index(drop=True),
        left_size=len(left),
        left_with_key=int(has_key.sum()) if len(left) else 0,
    )


def tier_table(
    left: pd.DataFrame, right: pd.DataFrame, *, left_name: str, right_name: str
) -> pd.DataFrame:
    """All three tiers side by side, with the direction spelled out on every row."""
    rows = []
    for tier in TIERS:
        result = misses(left, right, left_name=left_name, right_name=right_name, tier=tier)
        rows.append(
            {
                "direction": result.label,
                "tier": TIER_LABELS[tier],
                "misses": result.miss_count,
                "of": result.left_size,
                "tier_coverage": result.coverage_note,
            }
        )
    return pd.DataFrame(rows)


# --- Loaders for notebooks/05_sandbox.ipynb -----------------------------------------------------


def load_playlists(ctx: Any) -> pd.DataFrame:
    """Every playlist loaded, current version, with its observed track count.

    No owner column: owner identity is discarded at ingest (R-054), so "is this mine?" is not
    answerable from the warehouse. Stated in the notebook rather than guessed at.
    """
    return ctx.frame(
        "select playlist.playlist_name, playlist.track_total, playlist.is_collaborative, "
        "playlist.is_public, playlist.valid_from::date as first_seen, "
        "count(member.content_uri) as tracks_loaded "
        "from {mart}.dim_playlist as playlist "
        "left join {mart}.fct_playlist_membership as member "
        "on member.playlist_key = playlist.playlist_key "
        "where playlist.is_current and playlist.playlist_key > 0 "
        "group by 1, 2, 3, 4, 5 order by playlist.playlist_name"
    )


def load_membership(ctx: Any) -> pd.DataFrame:
    """Every track in every playlist at the latest snapshot, with all three tier keys.

    Tier 3 is computed from the playlist's own fields when `dim_content` has no row, so a track
    nobody has played still compares — that row is exactly what "in A, not in B" is hunting.
    """
    return ctx.frame(
        "select playlist.playlist_name, member.snapshot_date, "
        "coalesce(member.primary_creator_name, content.primary_creator_name) as artist, "
        "coalesce(member.content_name, content.content_name) as title, "
        "member.content_uri, "
        "coalesce(member.isrc, detail.isrc) as isrc, "
        "coalesce(content.content_match_key, "
        "  lower(trim(coalesce(member.content_name, ''))) || '|' "
        "  || lower(trim(coalesce(member.primary_creator_name, '')))) as content_match_key, "
        "(content.content_key is not null) as in_dim_content "
        "from {mart}.fct_playlist_membership as member "
        "join {mart}.dim_playlist as playlist on playlist.playlist_key = member.playlist_key "
        "left join {mart}.dim_content as content on content.content_uri = member.content_uri "
        "left join {mart}.dim_track_detail as detail on detail.content_key = content.content_key "
        "where member.snapshot_date = ("
        "  select max(snapshot_date) from {mart}.fct_playlist_membership)"
    )


def orphan_summary(ctx: Any) -> pd.DataFrame:
    """How many playlist tracks have no `dim_content` row, and what share of membership that is."""
    return ctx.frame(
        "select count(*) as membership_rows, "
        "count(*) filter (where content.content_key is null) as rows_without_content, "
        "count(distinct member.content_uri) as distinct_tracks, "
        "count(distinct member.content_uri) filter (where content.content_key is null) "
        "  as distinct_tracks_without_content "
        "from {mart}.fct_playlist_membership as member "
        "left join {mart}.dim_content as content on content.content_uri = member.content_uri"
    )
