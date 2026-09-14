"""Helpers for notebooks/01_api_inventory.ipynb (spot-main-R-008, docs/notebook-specs.md §1).

One emitter builds every endpoint section, so the sections cannot drift apart. Surfaces already
in the warehouse are read from `raw.api_response`; surfaces never extracted are called live, once,
under the `spot_refresh` lock. Every sample and every schema example passes through
`redact.redact_profile`. Nothing here follows a URL a response chose (CLAUDE.md §5): ids taken
from responses are validated before they are placed in a path.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from spotify_lakehouse.api import DEAD_ENDPOINTS, SpotifyApiError, SpotifyClient
from spotify_lakehouse.redact import redact_profile

SAMPLE_ROWS = 5
EXAMPLE_WIDTH = 60
ERROR_WIDTH = 180
TIME_RANGES = ("short_term", "medium_term", "long_term")
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

# Record-level fields per object, transcribed from the Spotify Web API reference for R-008.
# This is documentation, not measurement: the schema tables compare the live response against it.
DOCUMENTED_FIELDS: dict[str, tuple[str, ...]] = {
    "user": (
        "country",
        "display_name",
        "email",
        "explicit_content",
        "external_urls",
        "followers",
        "href",
        "id",
        "images",
        "product",
        "type",
        "uri",
    ),
    "saved_track": ("added_at", "track"),
    "saved_album": ("added_at", "album"),
    "saved_show": ("added_at", "show"),
    "artist": (
        "external_urls",
        "followers",
        "genres",
        "href",
        "id",
        "images",
        "name",
        "popularity",
        "type",
        "uri",
    ),
    "playlist": (
        "collaborative",
        "description",
        "external_urls",
        "href",
        "id",
        "images",
        "name",
        "owner",
        "public",
        "snapshot_id",
        "tracks",
        "type",
        "uri",
    ),
    "playlist_item": ("added_at", "added_by", "is_local", "track"),
    "play_history": ("track", "played_at", "context"),
    "track": (
        "album",
        "artists",
        "available_markets",
        "disc_number",
        "duration_ms",
        "explicit",
        "external_ids",
        "external_urls",
        "href",
        "id",
        "is_playable",
        "linked_from",
        "restrictions",
        "name",
        "popularity",
        "preview_url",
        "track_number",
        "type",
        "uri",
        "is_local",
    ),
    "album": (
        "album_type",
        "total_tracks",
        "available_markets",
        "external_urls",
        "href",
        "id",
        "images",
        "name",
        "release_date",
        "release_date_precision",
        "restrictions",
        "type",
        "uri",
        "artists",
        "tracks",
        "copyrights",
        "external_ids",
        "genres",
        "label",
        "popularity",
    ),
    "show": (
        "available_markets",
        "copyrights",
        "description",
        "html_description",
        "explicit",
        "external_urls",
        "href",
        "id",
        "images",
        "is_externally_hosted",
        "languages",
        "media_type",
        "name",
        "publisher",
        "type",
        "uri",
        "total_episodes",
        "episodes",
    ),
    "episode": (
        "audio_preview_url",
        "description",
        "html_description",
        "duration_ms",
        "explicit",
        "external_urls",
        "href",
        "id",
        "images",
        "is_externally_hosted",
        "is_playable",
        "language",
        "languages",
        "name",
        "release_date",
        "release_date_precision",
        "resume_point",
        "type",
        "uri",
        "restrictions",
        "show",
    ),
}
# Documented as returned only in some conditions (a market, relinking, a restriction, a scope).
CONDITIONAL_FIELDS = frozenset(
    {"linked_from", "restrictions", "is_playable", "resume_point", "language", "email"}
)

DOCUMENTATION_CAVEAT = (
    "documented fields are transcribed from the Spotify Web API reference for this report: "
    "documentation, not measurement"
)


@dataclass(frozen=True)
class EndpointSpec:
    number: str
    path: str
    title: str
    scope: str
    feeds: str

    @property
    def domain(self) -> str:
        return self.number.split(".", 1)[0]

    @property
    def heading(self) -> str:
        return f"{self.number} {self.path} — {self.title}"


SPECS: tuple[EndpointSpec, ...] = (
    EndpointSpec(
        "1.1", "/me", "Current User Profile", "user-read-private", "stg_spotify__me → dim_profile"
    ),
    EndpointSpec(
        "2.1",
        "/me/tracks",
        "Saved Tracks",
        "user-library-read",
        "not modelled (fct_library_snapshot, later round)",
    ),
    EndpointSpec(
        "2.2",
        "/me/albums",
        "Saved Albums",
        "user-library-read",
        "not modelled (fct_library_snapshot, later round)",
    ),
    EndpointSpec("2.3", "/me/following", "Followed Artists", "user-follow-read", "not modelled"),
    EndpointSpec(
        "3.1",
        "/me/playlists",
        "Playlist Inventory",
        "playlist-read-private, playlist-read-collaborative",
        "not modelled (dim_playlist, R-006)",
    ),
    EndpointSpec(
        "3.2",
        "/playlists/{id}/items",
        "Playlist Contents",
        "playlist-read-private",
        "not modelled (fct_playlist_membership, R-006)",
    ),
    EndpointSpec(
        "4.1",
        "/me/player/recently-played",
        "Recent Plays",
        "user-read-recently-played",
        "stg_spotify__recently_played → fct_play_event, dim_content, dim_track_detail, "
        "dim_album, br_content_artist",
    ),
    EndpointSpec(
        "4.2",
        "/me/top/artists",
        "Top Artists (×3 time ranges)",
        "user-top-read",
        "not modelled (fct_top_item)",
    ),
    EndpointSpec(
        "4.3",
        "/me/top/tracks",
        "Top Tracks (×3 time ranges)",
        "user-top-read",
        "not modelled (fct_top_item)",
    ),
    EndpointSpec(
        "5.1",
        "/artists/{id}",
        "Artist (incl. deprecated `genres`)",
        "none (catalog)",
        "stg_spotify__artist → dim_artist",
    ),
    EndpointSpec(
        "5.2",
        "/albums/{id}",
        "Album",
        "none (catalog)",
        "not extracted (dim_album is built from track objects)",
    ),
    EndpointSpec(
        "5.3",
        "/tracks/{id}",
        "Track",
        "none (catalog)",
        "not extracted (dim_content is built from recently-played)",
    ),
    EndpointSpec(
        "5.4",
        "/shows/{id} & /episodes/{id}",
        "Podcasts",
        "none (catalog)",
        "not modelled (dim_show, dim_episode_detail)",
    ),
    EndpointSpec("5.5", "/search", "Catalog Search", "none (catalog)", "not modelled"),
)
SPEC_BY_NUMBER = {spec.number: spec for spec in SPECS}


@dataclass
class Part:
    label: str
    object_kind: str
    request: str
    status: str
    ok: bool
    records: list[dict[str, Any]] = field(default_factory=list)
    requested: bool = False  # True only when an HTTP request was actually sent


@dataclass
class Capture:
    spec: EndpointSpec
    source: str  # "warehouse" or "live"
    parts: list[Part] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return any(part.ok and part.records for part in self.parts)

    @property
    def records_total(self) -> int:
        return sum(len(part.records) for part in self.parts)

    @property
    def live_calls(self) -> int:
        return sum(1 for part in self.parts if part.requested)


# --- Pure helpers --------------------------------------------------------------------------------


def is_spotify_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= _ID_CHARS


def records_from(payload: Any, path: str) -> list[dict[str, Any]]:
    """Records at a dotted path: `self` is the payload itself; `items`, `artists.items`, …"""
    if path == "self":
        node: Any = payload
    else:
        node = payload
        for key in path.split("."):
            node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node = [node]
    return [record for record in node or [] if isinstance(record, dict)]


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    return "object"


def _summary(value: Any) -> Any:
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"object({len(value)} keys)"
    if isinstance(value, str) and len(value) > EXAMPLE_WIDTH:
        return value[: EXAMPLE_WIDTH - 1] + "…"
    return value


def _one_level(record: dict[str, Any]) -> list[tuple[str, Any]]:
    """Top-level fields, plus the first level inside object-valued fields as `parent.child`."""
    pairs: list[tuple[str, Any]] = []
    for key, value in record.items():
        pairs.append((key, value))
        if isinstance(value, dict):
            pairs.extend((f"{key}.{child}", inner) for child, inner in value.items())
    return pairs


def missing_documented(
    records: list[dict[str, Any]], object_kind: str
) -> tuple[list[str], list[str]]:
    """(documented fields absent from every record, of which conditional) at record level."""
    documented = DOCUMENTED_FIELDS.get(object_kind, ())
    seen = {key for record in records for key in record}
    missing = sorted(name for name in documented if name not in seen)
    return [m for m in missing if m not in CONDITIONAL_FIELDS], [
        m for m in missing if m in CONDITIONAL_FIELDS
    ]


def undocumented(records: list[dict[str, Any]], object_kind: str) -> list[str]:
    documented = DOCUMENTED_FIELDS.get(object_kind)
    if documented is None:
        return []
    return sorted({key for record in records for key in record} - set(documented))


def schema_frame(records: list[dict[str, Any]], object_kind: str) -> pd.DataFrame:
    """Field, JSON type(s), presence, null rate and a redacted example, from the response."""
    total = len(records)
    stats: dict[str, dict[str, Any]] = {}
    for record in records:
        for name, value in _one_level(record):
            entry = stats.setdefault(
                name, {"types": set(), "present": 0, "non_null": 0, "example": None}
            )
            entry["types"].add(_type_name(value))
            entry["present"] += 1
            if value is not None:
                entry["non_null"] += 1
                if entry["example"] is None:
                    entry["example"] = _summary(value)
    documented = set(DOCUMENTED_FIELDS.get(object_kind, ()))
    rows = [
        {
            "field": name,
            "type": "|".join(sorted(entry["types"])),
            "present_in": f"{entry['present']}/{total}",
            "null_rate": round(1 - entry["non_null"] / total, 2) if total else None,
            "documented": ("yes" if name.split(".", 1)[0] in documented else "no")
            if documented
            else "n/a",
            "example": entry["example"],
        }
        for name, entry in stats.items()
    ]
    required, conditional = missing_documented(records, object_kind)
    for name in required + conditional:
        rows.append(
            {
                "field": name,
                "type": "ABSENT",
                "present_in": f"0/{total}",
                "null_rate": 1.0 if total else None,
                "documented": "yes (conditional)" if name in CONDITIONAL_FIELDS else "yes",
                "example": None,
            }
        )
    frame = pd.DataFrame(
        rows, columns=["field", "type", "present_in", "null_rate", "documented", "example"]
    )
    if frame.empty:
        return frame
    # Redact examples through the same path as the samples: one row, one column per field.
    examples = pd.DataFrame([dict(zip(frame["field"], frame["example"], strict=True))])
    redacted = redact_profile(examples)
    frame["example"] = [redacted.iloc[0][name] for name in frame["field"]]
    return frame


def sample_frame(
    records: list[dict[str, Any]], rows: int = SAMPLE_ROWS, label: str | None = None
) -> pd.DataFrame:
    """Up to `rows` records, one level flattened, nested values summarised, then redacted."""
    flattened = []
    for record in records[:rows]:
        flat = {
            name: _summary(value)
            for name, value in _one_level(record)
            if not isinstance(value, dict)
        }
        if label is not None:
            flat = {"sample": label, **flat}
        flattened.append(flat)
    return redact_profile(pd.DataFrame(flattened))


# --- Collection ------------------------------------------------------------------------------


def _request(path_template: str, params: dict[str, Any] | None) -> str:
    return f"GET {path_template}" + (f"?{urlencode(params)}" if params else "")


def _live(
    api: SpotifyClient,
    label: str,
    object_kind: str,
    path: str,
    path_template: str,
    records_at: str,
    params: dict[str, Any] | None = None,
) -> tuple[Part, Any]:
    request = _request(path_template, params)
    try:
        payload = api.get(path, params)
    except SpotifyApiError as exc:
        message = str(exc).replace("\n", " ")
        if len(message) > ERROR_WIDTH:
            message = message[: ERROR_WIDTH - 1] + "…"
        status = f"HTTP {exc.status}: {message}"
        return Part(label, object_kind, request, status, False, requested=True), None
    records = records_from(payload, records_at)
    return Part(label, object_kind, request, "200", True, records, requested=True), payload


def _unavailable(label: str, object_kind: str, request: str, reason: str) -> Part:
    return Part(label, object_kind, request, f"not requested: {reason}", False)


def warehouse_capture(
    number: str,
    object_kind: str,
    rows: list[tuple[int, datetime, Any]],
    records_at: str,
    label: str,
) -> Capture:
    """A capture from raw.api_response rows: (id, ingested_at, payload)."""
    capture = Capture(SPEC_BY_NUMBER[number], "warehouse")
    if not rows:
        capture.parts.append(
            _unavailable(label, object_kind, "raw.api_response", "no rows captured")
        )
        return capture
    records: list[dict[str, Any]] = []
    for _, _, payload in rows:
        records.extend(records_from(payload, records_at))
    ids = [row[0] for row in rows]
    newest = max(row[1] for row in rows)
    noun = "response" if len(rows) == 1 else "responses"
    span = f"id {ids[0]}" if len(rows) == 1 else f"ids {min(ids)}–{max(ids)}"
    request = (
        f"raw.api_response {span} ({len(rows)} {noun}), newest ingested {newest:%Y-%m-%d %H:%M}Z"
    )
    capture.parts.append(Part(label, object_kind, request, "warehouse", True, records))
    return capture


def collect_live(
    api: SpotifyClient, *, album_id: str | None, track_id: str | None
) -> dict[str, Capture]:
    """Every section not yet extracted, one call each (top items: one per time range)."""
    captures: dict[str, Capture] = {}

    def add(number: str, *parts: Part, notes: list[str] | None = None) -> Capture:
        capture = Capture(SPEC_BY_NUMBER[number], "live", list(parts), list(notes or []))
        captures[number] = capture
        return capture

    part, _ = _live(
        api, "saved tracks", "saved_track", "/me/tracks", "/me/tracks", "items", {"limit": 5}
    )
    add("2.1", part)
    part, _ = _live(
        api, "saved albums", "saved_album", "/me/albums", "/me/albums", "items", {"limit": 5}
    )
    add("2.2", part)
    part, _ = _live(
        api,
        "followed artists",
        "artist",
        "/me/following",
        "/me/following",
        "artists.items",
        {"type": "artist", "limit": 5},
    )
    add("2.3", part)

    playlists, playlists_payload = _live(
        api, "playlists", "playlist", "/me/playlists", "/me/playlists", "items", {"limit": 5}
    )
    add("3.1", playlists)
    playlist_ids = [p.get("id") for p in records_from(playlists_payload, "items")]
    playlist_id = next((pid for pid in playlist_ids if is_spotify_id(pid)), None)
    if playlist_id is None:
        add(
            "3.2",
            _unavailable(
                "playlist items",
                "playlist_item",
                "GET /playlists/{id}/items",
                "no valid playlist id to sample",
            ),
        )
    else:
        items, _ = _live(
            api,
            "playlist items (first playlist)",
            "playlist_item",
            f"/playlists/{playlist_id}/items",
            "/playlists/{id}/items",
            "items",
            {"limit": 5},
        )
        parts = [items]
        notes = []
        if not items.ok:
            # One route's refusal is not a capability claim: try the older documented route too.
            fallback, _ = _live(
                api,
                "playlist tracks (older route)",
                "playlist_item",
                f"/playlists/{playlist_id}/tracks",
                "/playlists/{id}/tracks",
                "items",
                {"limit": 5},
            )
            parts.append(fallback)
            notes.append(
                "`/playlists/{id}/items` was refused, so `/playlists/{id}/tracks` was tried "
                f"as well. Result: {fallback.status}."
            )
        add("3.2", *parts, notes=notes)

    for number, path, kind in (
        ("4.2", "/me/top/artists", "artist"),
        ("4.3", "/me/top/tracks", "track"),
    ):
        parts = []
        for time_range in TIME_RANGES:
            part, _ = _live(
                api, time_range, kind, path, path, "items", {"time_range": time_range, "limit": 5}
            )
            parts.append(part)
        add(number, *parts)

    if is_spotify_id(album_id):
        part, _ = _live(api, "album", "album", f"/albums/{album_id}", "/albums/{id}", "self")
    else:
        part = _unavailable("album", "album", "GET /albums/{id}", "no album id in the warehouse")
    add("5.2", part)
    if is_spotify_id(track_id):
        part, _ = _live(api, "track", "track", f"/tracks/{track_id}", "/tracks/{id}", "self")
    else:
        part = _unavailable("track", "track", "GET /tracks/{id}", "no track id in the warehouse")
    add("5.3", part)

    captures["5.4"] = _collect_podcasts(api)

    part, _ = _live(
        api,
        "search: tracks",
        "track",
        "/search",
        "/search",
        "tracks.items",
        {"q": "the", "type": "track", "limit": 5},
    )
    add("5.5", part)
    return captures


def _collect_podcasts(api: SpotifyClient) -> Capture:
    capture = Capture(SPEC_BY_NUMBER["5.4"], "live")
    saved, saved_payload = _live(
        api,
        "saved shows (to find a show id)",
        "saved_show",
        "/me/shows",
        "/me/shows",
        "items",
        {"limit": 1},
    )
    capture.parts.append(saved)
    show_id = next(
        (
            i.get("show", {}).get("id")
            for i in records_from(saved_payload, "items")
            if isinstance(i.get("show"), dict)
        ),
        None,
    )
    if not is_spotify_id(show_id):
        found, found_payload = _live(
            api,
            "search: shows (no saved show)",
            "show",
            "/search",
            "/search",
            "shows.items",
            {"q": "podcast", "type": "show", "limit": 1},
        )
        capture.parts.append(found)
        show_id = next((s.get("id") for s in records_from(found_payload, "shows.items")), None)
    if not is_spotify_id(show_id):
        capture.parts.append(_unavailable("show", "show", "GET /shows/{id}", "no show id found"))
        return capture
    show, show_payload = _live(
        api, "show", "show", f"/shows/{show_id}", "/shows/{id}", "self", {"market": "from_token"}
    )
    capture.parts.append(show)
    episode_id = next((e.get("id") for e in records_from(show_payload, "episodes.items")), None)
    if not is_spotify_id(episode_id):
        capture.parts.append(
            _unavailable("episode", "episode", "GET /episodes/{id}", "no episode id in the show")
        )
        return capture
    episode, _ = _live(
        api,
        "episode",
        "episode",
        f"/episodes/{episode_id}",
        "/episodes/{id}",
        "self",
        {"market": "from_token"},
    )
    capture.parts.append(episode)
    return capture


def objects_by_kind(captures: dict[str, Capture]) -> dict[str, list[dict[str, Any]]]:
    """Full objects of each kind across every capture, including those nested one level down."""
    objects: dict[str, list[dict[str, Any]]] = {
        "track": [],
        "artist": [],
        "album": [],
        "user": [],
        "show": [],
        "episode": [],
    }
    nested = {
        "saved_track": ("track", "track"),
        "saved_album": ("album", "album"),
        "playlist_item": ("track", "track"),
        "play_history": ("track", "track"),
        "saved_show": ("show", "show"),
    }
    for capture in captures.values():
        for part in capture.parts:
            if part.object_kind in objects:
                objects[part.object_kind].extend(part.records)
            elif part.object_kind in nested:
                key, kind = nested[part.object_kind]
                objects[kind].extend(r[key] for r in part.records if isinstance(r.get(key), dict))
    return objects


FIELD_CHECKS: tuple[tuple[str, str], ...] = (
    ("artist", "genres"),
    ("artist", "popularity"),
    ("artist", "followers"),
    ("track", "popularity"),
    ("track", "available_markets"),
    ("track", "preview_url"),
    ("album", "popularity"),
    ("album", "genres"),
    ("album", "available_markets"),
    ("user", "email"),
    ("user", "followers"),
)


def field_presence_frame(captures: dict[str, Capture]) -> pd.DataFrame:
    """Measured in this run: how many objects of each kind carry each watched field."""
    objects = objects_by_kind(captures)
    rows = []
    for kind, name in FIELD_CHECKS:
        examined = objects.get(kind, [])
        present = sum(1 for obj in examined if name in obj)
        verdict = (
            "not examined"
            if not examined
            else (
                "absent"
                if present == 0
                else "present"
                if present == len(examined)
                else "partly present"
            )
        )
        rows.append(
            {
                "object": kind,
                "field": name,
                "objects_examined": len(examined),
                "objects_with_key": present,
                "verdict": verdict,
            }
        )
    return pd.DataFrame(rows)


def dead_endpoint_frame() -> pd.DataFrame:
    probes = (
        "/audio-features",
        "/audio-analysis/{id}",
        "/recommendations",
        "/artists/{id}/related-artists",
        "/browse/featured-playlists",
        "/browse/categories/{id}/playlists",
    )
    return pd.DataFrame(
        [
            {
                "endpoint": path,
                "called in this report": "no (CLAUDE.md §4 policy)",
                "refused locally by the client": "yes"
                if DEAD_ENDPOINTS.match(path.replace("{id}", "x"))
                else "no",
                "basis": "Spotify's 2024-11-27 cutoff for new apps (documented, not measured here)",
            }
            for path in probes
        ]
    )


def coverage_frame(captures: dict[str, Capture]) -> pd.DataFrame:
    rows = []
    for spec in SPECS:
        capture = captures.get(spec.number)
        if capture is None:
            rows.append(
                {
                    "section": spec.number,
                    "endpoint": spec.path,
                    "source": "not collected",
                    "reachable": "no",
                    "live calls": 0,
                    "records sampled": 0,
                    "fields observed": 0,
                    "feeds": spec.feeds,
                }
            )
            continue
        fields = {
            name
            for part in capture.parts
            for record in part.records
            for name, _ in _one_level(record)
        }
        failed = [part.status for part in capture.parts if not part.ok]
        reachable = "yes" if capture.ok else ("no: " + (failed[0] if failed else "no records"))
        rows.append(
            {
                "section": spec.number,
                "endpoint": spec.path,
                "source": capture.source,
                "reachable": reachable,
                "live calls": capture.live_calls,
                "records sampled": capture.records_total,
                "fields observed": len(fields),
                "feeds": spec.feeds,
            }
        )
    return pd.DataFrame(rows)


def coverage_counts(captures: dict[str, Capture]) -> Counter:
    counts: Counter = Counter()
    for spec in SPECS:
        capture = captures.get(spec.number)
        if capture is None or not capture.ok:
            counts["not sampled"] += 1
        else:
            counts[capture.source] += 1
        if capture is not None:
            counts["live calls"] += capture.live_calls
    return counts


# --- Emitters (notebook side) ----------------------------------------------------------------


def _md(text: str) -> None:
    from IPython.display import Markdown, display

    display(Markdown(text))


def _show(frame: pd.DataFrame) -> None:
    from IPython.display import display

    display(frame)


def _callout(kind: str, text: str) -> None:
    body = "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
    _md(f"> [!{kind}]\n{body}")


def emit_endpoint(capture: Capture) -> None:
    """The one helper that writes a §1 endpoint section: H3 plus the four H4 subsections."""
    spec = capture.spec
    _md(f"### {spec.heading}")

    _md("#### Endpoint & scope")
    source = (
        "Read from the warehouse — this surface is already extracted, so no API call was made."
        if capture.source == "warehouse"
        else "Called live for this report, under the `spot_refresh` lock, because this surface "
        "is not extracted."
    )
    _md(f"{source} **Scope:** {spec.scope}. **Feeds:** {spec.feeds}.")
    _show(
        pd.DataFrame(
            [
                {
                    "part": p.label,
                    "request": p.request,
                    "status": p.status,
                    "records": len(p.records),
                }
                for p in capture.parts
            ]
        )
    )

    _md("#### Schema")
    if not capture.ok:
        _md("No records were returned, so there is no schema to derive.")
    for part in capture.parts:
        if part.ok and part.records:
            _md(
                f"**{part.label}** — {len(part.records)} record(s); fields derived from the "
                f"response ({DOCUMENTATION_CAVEAT})."
            )
            _show(schema_frame(part.records, part.object_kind))

    _md("#### Sample")
    shown = False
    for part in capture.parts:
        if part.ok and part.records:
            _md(f"**{part.label}** — up to {SAMPLE_ROWS} rows, redacted.")
            _show(sample_frame(part.records))
            shown = True
    if not shown:
        _md("Nothing to sample.")

    _md("#### Notes & limits")
    wrote = False
    for part in capture.parts:
        if not part.ok:
            _callout(
                "CAUTION", f"**{part.label}** was not sampled. `{part.request}` → {part.status}"
            )
            wrote = True
            continue
        if not part.records:
            continue
        required, conditional = missing_documented(part.records, part.object_kind)
        if required:
            _callout(
                "WARNING",
                f"**{part.label}:** documented field(s) absent from every record: "
                f"`{'`, `'.join(required)}`. An extractor relying on them breaks.",
            )
            wrote = True
        if conditional:
            names = "`, `".join(conditional)
            _md(f"**{part.label}:** documented-but-conditional field(s) not returned: `{names}`.")
            wrote = True
        extra = undocumented(part.records, part.object_kind)
        if extra:
            names = "`, `".join(extra)
            _callout(
                "NOTE",
                f"**{part.label}:** returned but not in the documented field list: `{names}`.",
            )
            wrote = True
    for note in capture.notes:
        _callout("NOTE", note)
        wrote = True
    if not wrote:
        _md(
            "Nothing surprising: every documented field was returned and no undocumented "
            "field appeared."
        )


def emit_domain(captures: dict[str, Capture], domain: str) -> None:
    for spec in SPECS:
        if spec.domain == domain and spec.number in captures:
            emit_endpoint(captures[spec.number])


def dumps_payload(value: Any) -> str:
    """Compact JSON for debugging a capture; never displayed in the report."""
    return json.dumps(value, sort_keys=True)[:500]


# --- Notebook sections that read the context (01_api_inventory.ipynb) ----------------------------


def _section_key(number: str) -> list[int]:
    return [int(piece) for piece in number.split(".")]


def collect(ctx: Any) -> dict[str, Capture]:
    """Warehouse sections from raw.api_response; everything else live, once, under the lock."""
    latest = (
        "select id, ingested_at, payload from raw.api_response "
        "where feed = %s and profile_slug = %s order by id desc limit 1"
    )
    captures = {
        "1.1": warehouse_capture(
            "1.1", "user", ctx.rows(latest, ("me", ctx.profile)), "self", "latest /me response"
        ),
        "4.1": warehouse_capture(
            "4.1",
            "play_history",
            ctx.rows(latest, ("recently_played", ctx.profile)),
            "items",
            "latest recently-played response",
        ),
        "5.1": warehouse_capture(
            "5.1",
            "artist",
            ctx.rows(
                "select id, ingested_at, payload from raw.api_response "
                "where feed = 'artist' order by id"
            ),
            "self",
            "artist responses",
        ),
    }

    polls = ctx.rows(
        "select count(*) filter (where status = 'ok'), count(*) filter (where status = 'error'), "
        "max(finished_at) from spot_meta.poll_run where profile_slug = %s",
        (ctx.profile,),
    )[0]
    gaps = ctx.scalar(
        "select count(*) from spot_meta.overflow_gap where profile_slug = %s", (ctx.profile,)
    )
    captures["4.1"].notes.append(
        f"The poller (`spot refresh`, every 30 minutes) has {polls[0]} successful and "
        f"{polls[1]} failed poll(s) recorded for `{ctx.profile}`, the latest at "
        f"{polls[2]:%Y-%m-%d %H:%M} UTC; overflow gaps recorded: {gaps}. **A gap is evidence of "
        "possible loss, not proof**: a gap with fewer than 50 items returned cannot be an "
        "overflow (CLAUDE.md §4)."
        if polls[2]
        else f"No poll has been recorded for `{ctx.profile}` yet; overflow gaps recorded: {gaps}."
    )
    artist_records = captures["5.1"].parts[0].records
    with_genres = sum(1 for record in artist_records if "genres" in record)
    captures["5.1"].notes.append(
        f"The heading says *deprecated* `genres`; measured, it is **absent**: {with_genres} of "
        f"{len(artist_records)} stored artist responses carry a `genres` key (R-004 found 0 of "
        "55 across three endpoints). `dim_artist.genre_source` stays NULL until a genre source "
        "exists (R-024)."
    )

    album_id = ctx.scalar(
        "select album_id from {mart}.dim_album where album_key > 0 order by album_key limit 1"
    )
    track_id = ctx.scalar(
        "select content_id from {stg}.int_tracks__latest order by first_seen_at limit 1"
    )
    with ctx.api() as api:
        captures.update(collect_live(api, album_id=album_id, track_id=track_id))
    return dict(sorted(captures.items(), key=lambda item: _section_key(item[0])))


def emit_intro(ctx: Any, captures: dict[str, Capture]) -> None:
    counts = coverage_counts(captures)
    plays, tracks, artists, albums = ctx.rows(
        "select (select count(*) from {mart}.fct_play_event), "
        "(select count(*) from {mart}.dim_content where content_key > 0), "
        "(select count(*) from {mart}.dim_artist where artist_key > 0), "
        "(select count(*) from {mart}.dim_album where album_key > 0)"
    )[0]
    _md(
        f"**Run:** {ctx.started_at:%Y-%m-%d %H:%M} UTC · **Profile:** `{ctx.profile}` · "
        f"**Session:** `{ctx.session}` (schemas `{ctx.stg_schema}`, `{ctx.mart_schema}`)\n\n"
        f"**Warehouse at run time:** {plays} plays, {tracks} tracks, {artists} artists, "
        f"{albums} albums.\n\n"
        f"**Coverage of the {len(SPECS)} endpoint sections in §1–§5:** "
        f"{counts['warehouse']} read from the warehouse, {counts['live']} called live "
        f"({counts['live calls']} API calls), {counts['not sampled']} could not be sampled. "
        "Six removed endpoints were deliberately not called (§6.1). §7 has the full map."
    )


def emit_local_vs_utc(ctx: Any) -> None:
    import matplotlib.pyplot as plt

    total, shifted, timezone = ctx.rows(
        "select count(*), count(*) filter (where f.date_key <> "
        "to_char(f.ended_at_utc at time zone 'UTC', 'YYYYMMDD')::int), max(p.home_timezone) "
        "from {mart}.fct_play_event f join {mart}.dim_profile p using (profile_key) "
        "where p.profile_slug = %s",
        (ctx.profile,),
    )[0]
    share = shifted / total if total else 0
    _md(
        f"**{shifted} of {total} plays ({share:.0%})** fall on a different calendar date in "
        f"`{timezone}` than in UTC. `date_key` is derived from the local date "
        "(data-contracts §2), never from UTC."
    )
    frame = ctx.frame(
        "select to_char(f.ended_at_utc at time zone 'UTC', 'YYYY-MM-DD') as utc_date, "
        "to_char(d.full_date, 'YYYY-MM-DD') as local_date, count(*) as plays "
        "from {mart}.fct_play_event f join {mart}.dim_date d using (date_key) "
        "join {mart}.dim_profile p using (profile_key) where p.profile_slug = %s "
        "group by 1, 2 order by 1, 2",
        (ctx.profile,),
    )
    _show(frame)
    by_utc = frame.groupby("utc_date")["plays"].sum()
    by_local = frame.groupby("local_date")["plays"].sum()
    dates = sorted(set(by_utc.index) | set(by_local.index))
    positions = range(len(dates))
    fig, ax = plt.subplots()
    ax.bar(
        [p - 0.2 for p in positions],
        [int(by_utc.get(d, 0)) for d in dates],
        0.4,
        label="counted by UTC date",
    )
    ax.bar(
        [p + 0.2 for p in positions],
        [int(by_local.get(d, 0)) for d in dates],
        0.4,
        label=f"counted by local date ({timezone})",
    )
    ax.set_xticks(list(positions), dates)
    ax.set_ylabel("plays")
    ax.legend()
    plt.show()
    if shifted:
        _callout(
            "WARNING",
            f"Grouping these plays by UTC date would put {shifted} of them on the wrong day — a "
            "late-evening session in the home timezone becomes the next morning's listening. "
            "Every daily or day-of-week figure in this project reads `date_key`, not the UTC "
            "timestamp.",
        )


def emit_api_window(ctx: Any) -> None:
    plays, first_play, last_play = ctx.rows(
        "select count(*), min(f.ended_at_utc), max(f.ended_at_utc) from {mart}.fct_play_event f "
        "join {mart}.dim_profile p using (profile_key) where p.profile_slug = %s",
        (ctx.profile,),
    )[0]
    model_start = ctx.scalar(
        "select api_coverage_start from {mart}.dim_profile where profile_slug = %s", (ctx.profile,)
    )
    contract_start = ctx.scalar(
        "select min(finished_at) from spot_meta.poll_run where status = 'ok' and profile_slug = %s",
        (ctx.profile,),
    )
    before = (
        ctx.scalar(
            "select count(*) from {mart}.fct_play_event f "
            "join {mart}.dim_profile p using (profile_key) "
            "where p.profile_slug = %s and f.ended_at_utc < %s",
            (ctx.profile, contract_start),
        )
        if contract_start
        else plays
    )

    def stamp(value: Any) -> str:
        return f"{value:%Y-%m-%d %H:%M:%S} UTC" if value else "none"

    _show(
        pd.DataFrame(
            [
                {"fact": "plays in fct_play_event", "value": str(plays)},
                {"fact": "earliest play", "value": stamp(first_play)},
                {"fact": "latest play", "value": stamp(last_play)},
                {
                    "fact": "api_coverage_start per data-contracts §3 (first ok poll)",
                    "value": stamp(contract_start),
                },
                {"fact": "dim_profile.api_coverage_start as built", "value": stamp(model_start)},
                {"fact": "plays captured before the poller's first ok poll", "value": str(before)},
            ]
        )
    )
    _callout(
        "CAUTION",
        f"**This is the whole API history.** Every play the warehouse holds for `{ctx.profile}` "
        f"falls between {stamp(first_play)} and {stamp(last_play)}. The API returns at most 50 "
        "recent plays and does not page backwards (R-018), so **everything before the earliest "
        "play needs the Extended Streaming History export** (R-003, R-014).",
    )
    if contract_start and model_start != contract_start:
        _callout(
            "WARNING",
            f"`dim_profile.api_coverage_start` is {stamp(model_start)}, the interim definition "
            "(first recently-played ingestion). data-contracts §3 now defines it as the first ok "
            f"poll, {stamp(contract_start)}. {before} of {plays} plays predate that poll: they "
            "were first captured by one-off probes, which are real data but not coverage.",
        )


def emit_cannot_get(ctx: Any, captures: dict[str, Capture]) -> None:
    _md("### 6.1 Removed endpoints (Nov 2024)")
    _md(
        "These were cut off for apps registered after 2024-11-27, which includes this one. This "
        "report does not call them, and the project's API client refuses them before any request "
        "is sent."
    )
    _show(dead_endpoint_frame())
    _callout(
        "CAUTION",
        "No audio features (tempo, energy, valence, danceability), audio analysis, "
        "recommendations or related artists are available to this project. The refusal column is "
        "measured (the client's own check); the cutoff itself is Spotify's announcement, not "
        "something this report re-tested.",
    )

    _md("### 6.2 Absent by design — full history, podcast plays, listening duration")
    rows_total, with_duration = ctx.rows(
        "select count(*), count(ms_played) from {mart}.fct_play_event"
    )[0]
    types = ctx.rows(
        "select coalesce(i.value -> 'track' ->> 'type', 'missing'), count(*) "
        "from raw.api_response r "
        "cross join lateral jsonb_array_elements(r.payload -> 'items') i "
        "where r.feed = 'recently_played' group by 1 order by 1"
    )
    captures_count = ctx.scalar(
        "select count(*) from raw.api_response where feed = 'recently_played'"
    )
    type_text = ", ".join(f"{kind}: {count}" for kind, count in types) or "none"
    _show(
        pd.DataFrame(
            [
                {
                    "missing": "Full listening history",
                    "evidence": "Following recently-played's `next` returned 0 items and "
                    "`next = null` while 14 older plays were demonstrably available "
                    "a day earlier.",
                    "measured": "R-018 (not re-run: it costs calls and changes nothing)",
                    "only source": "Extended Streaming History export",
                },
                {
                    "missing": "Podcast plays",
                    "evidence": f"Item types across all {captures_count} stored "
                    f"recently-played responses — {type_text}.",
                    "measured": "this run (warehouse)",
                    "only source": "Extended Streaming History export",
                },
                {
                    "missing": "Listening duration (ms_played)",
                    "evidence": f"{with_duration} of {rows_total} fct_play_event rows "
                    "carry ms_played.",
                    "measured": "this run (warehouse)",
                    "only source": "Extended Streaming History export",
                },
                {
                    "missing": "Batch artist lookup",
                    "evidence": "`GET /artists?ids=` returned HTTP 403 in development mode.",
                    "measured": "R-004 (not re-run)",
                    "only source": "one `GET /artists/{id}` per artist",
                },
            ]
        )
    )
    _callout(
        "CAUTION",
        f"**No listening time from the API.** `ms_played` is NULL on "
        f"{rows_total - with_duration} of {rows_total} plays, by contract: any figure that sums "
        "time listened is empty for API data, not zero.",
    )

    _md("### 6.3 Field-level deprecations")
    _md(
        "Measured in this run across every object this report captured, including objects "
        "nested one level down (the track inside a saved-track item, and so on). Absent means "
        "the key is not in the response at all, which is different from present-but-null."
    )
    presence = field_presence_frame(captures)
    _show(presence)
    absent = presence[presence["verdict"] == "absent"]
    if not absent.empty:
        listed = ", ".join(f"`{row.object}.{row.field}`" for row in absent.itertuples())
        _callout(
            "WARNING",
            f"Absent from every object examined: {listed}. Use `->>` / `.get()` and treat these "
            "as missing keys, not nulls. `genres` in particular is absent, not merely deprecated "
            "(R-004 §1).",
        )


def emit_coverage(captures: dict[str, Capture]) -> None:
    counts = coverage_counts(captures)
    _md(
        f"**{counts['warehouse']}** section(s) read from the warehouse · **{counts['live']}** "
        f"called live ({counts['live calls']} API calls) · **{counts['not sampled']}** could not "
        "be sampled · six removed endpoints not called (§6.1)."
    )
    frame = coverage_frame(captures)
    _show(frame)
    unreachable = frame[~frame["reachable"].str.startswith("yes")]
    if not unreachable.empty:
        listed = "; ".join(
            f"{row.section} `{row.endpoint}` — {row.reachable}" for row in unreachable.itertuples()
        )
        _callout("WARNING", f"Not sampled in this run: {listed}.")
