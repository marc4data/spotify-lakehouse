"""Follow Spotify's `next` links without trusting them (spot-main-R-018, extended R-054).

The client attaches the profile's bearer token to every request, so a `next` URL taken from a
response is never requested as given. It is parsed, checked against the endpoint it may point at,
and reduced to query parameters that are re-issued through the normal paced client.

Two shapes, because Spotify pages two ways. Recently-played pages by a `before` cursor
(`next_page_params`). Playlists page by `offset` (`offset_next_params`, R-054) — CLAUDE.md §5 says
each new pager extends this module rather than trusting the URL, so playlists get a checker of
their own instead of a loosened one.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

RECENTLY_PLAYED_PATH = "/me/player/recently-played"
_ALLOWED_SCHEME = "https"
_ALLOWED_HOST = "api.spotify.com"
_ALLOWED_URL_PATH = f"/v1{RECENTLY_PLAYED_PATH}"
_ALLOWED_PARAMS = frozenset({"before", "limit"})
MAX_FOLLOW_NEXT = 5


class UnsafeNextLink(ValueError):
    """A `next` link that is not recently-played on api.spotify.com with a `before` cursor."""


def next_page_params(next_url: str) -> dict[str, str]:
    """Return the query parameters of a recently-played `next` link, or raise UnsafeNextLink."""
    parts = urlsplit(next_url)
    if parts.scheme != _ALLOWED_SCHEME or parts.hostname != _ALLOWED_HOST or parts.port is not None:
        raise UnsafeNextLink(f"next link is not https://{_ALLOWED_HOST}; refusing to send a token")
    if parts.username or parts.password:
        raise UnsafeNextLink("next link carries credentials in the URL")
    if parts.path != _ALLOWED_URL_PATH:
        raise UnsafeNextLink(f"next link path is {parts.path!r}, expected {_ALLOWED_URL_PATH!r}")
    query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=False)
    unexpected = set(query) - _ALLOWED_PARAMS
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise UnsafeNextLink(f"next link has unexpected parameter(s): {names}")
    if any(len(values) != 1 for values in query.values()):
        raise UnsafeNextLink("next link repeats a parameter")
    params = {key: values[0] for key, values in query.items()}
    if not params.get("before", "").isdigit():
        raise UnsafeNextLink("next link has no numeric `before` cursor")
    if "limit" in params and not params["limit"].isdigit():
        raise UnsafeNextLink("next link has a non-numeric `limit`")
    return params


# --- Offset paging: /me/playlists and /playlists/{id}/items (spot-main-R-054) -------------------

PLAYLISTS_PATH = "/me/playlists"
PLAYLIST_ITEMS_PATH = "/playlists/{id}/items"
PLAYLIST_TRACKS_PATH = "/playlists/{id}/tracks"  # the older route, kept as a fallback (R-008 F3)
_OFFSET_PARAMS = frozenset({"offset", "limit"})


def offset_next_params(next_url: str, *, expected_path: str) -> dict[str, str]:
    """Parameters of an offset-paged `next` link, or raise UnsafeNextLink.

    `expected_path` is the API path the caller just requested (`/me/playlists`, or
    `/playlists/<id>/items`), without the `/v1` prefix. The link must point at exactly that path on
    api.spotify.com over https, carry only `offset`/`limit`, and both must be numeric.

    The caller does not have to trust even this: the extractor computes its own offset and uses the
    parsed value as a cross-check, so a link that disagrees is a finding rather than a fetch.
    """
    parts = urlsplit(next_url)
    if parts.scheme != _ALLOWED_SCHEME or parts.hostname != _ALLOWED_HOST or parts.port is not None:
        raise UnsafeNextLink(f"next link is not https://{_ALLOWED_HOST}; refusing to send a token")
    if parts.username or parts.password:
        raise UnsafeNextLink("next link carries credentials in the URL")
    allowed = f"/v1{expected_path}"
    if parts.path != allowed:
        raise UnsafeNextLink(f"next link path is {parts.path!r}, expected {allowed!r}")
    query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=False)
    unexpected = set(query) - _OFFSET_PARAMS
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise UnsafeNextLink(f"next link has unexpected parameter(s): {names}")
    if any(len(values) != 1 for values in query.values()):
        raise UnsafeNextLink("next link repeats a parameter")
    params = {key: values[0] for key, values in query.items()}
    if not params.get("offset", "").isdigit():
        raise UnsafeNextLink("next link has no numeric `offset`")
    if not params.get("limit", "").isdigit():
        raise UnsafeNextLink("next link has no numeric `limit`")
    return params
