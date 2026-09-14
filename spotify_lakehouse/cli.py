"""`spot` command line: auth, probe, extract-artists, sync-profiles, migrate."""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import structlog

from spotify_lakehouse import auth, migrate, profiles
from spotify_lakehouse.api import SpotifyApiError, SpotifyClient
from spotify_lakehouse.artists import artist_ids_to_fetch
from spotify_lakehouse.config import ConfigError, load_settings, session
from spotify_lakehouse.db import connect, refresh_lock
from spotify_lakehouse.paging import (
    MAX_FOLLOW_NEXT,
    RECENTLY_PLAYED_PATH,
    UnsafeNextLink,
    next_page_params,
)
from spotify_lakehouse.raw_store import feed_for_endpoint, insert_response, scrub, write_response
from spotify_lakehouse.shape import describe_shape

PROBE_ENDPOINTS: tuple[tuple[str, dict[str, Any] | None], ...] = (
    ("/me", None),
    (RECENTLY_PLAYED_PATH, {"limit": 50}),
)
# Extra courtesy pause between followed pages, on top of the client's own pacing and Retry-After.
PAGE_PAUSE_SECONDS = 2.0


def _configure_logging() -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
    )


def cmd_auth(args: argparse.Namespace) -> int:
    result = auth.authorize(args.profile, load_settings(), open_browser=not args.no_browser)
    print(
        f"Authorized profile '{args.profile}' (account product: {result['product']}). "
        f"Refresh token stored in {auth.tokens_path()} (mode 600)."
    )
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    session_name = session()
    with connect() as conn:
        applied = migrate.apply(conn)
        schemas = migrate.ensure_session_schemas(conn, session_name)
    print(f"migrations applied: {', '.join(applied) if applied else 'none pending'}")
    print(f"session schemas present: {', '.join(schemas)}")
    return 0


def cmd_sync_profiles(args: argparse.Namespace) -> int:
    entries = profiles.load_registry()
    with connect() as conn:
        count = profiles.sync_registry(conn, entries)
    print(f"profile registry synced: {count} profile(s) from {profiles.registry_path()}")
    return 0


def _summarize_me(payload: dict[str, Any], removed: list[str]) -> list[str]:
    followers = (payload.get("followers") or {}).get("total")
    return [
        f"  keys returned: {', '.join(sorted(set(payload) | set(removed)))}",
        f"  email: {'present -> discarded before storage' if removed else 'not returned'}",
        f"  product: {payload.get('product', 'not returned')}",
        f"  country: {'present' if payload.get('country') else 'not returned'}",
        f"  followers.total: {followers if followers is not None else 'not returned'}",
        "  id / display_name / uri: <redacted>",
    ]


def _summarize_recent(payload: dict[str, Any]) -> list[str]:
    items = payload.get("items") or []
    played = sorted(item["played_at"] for item in items if item.get("played_at"))
    tracks = {(item.get("track") or {}).get("uri") for item in items}
    types = Counter((item.get("track") or {}).get("type", "missing") for item in items)
    contexts = Counter((item.get("context") or {}).get("type", "none") for item in items)
    return [
        f"  items: {len(items)} (limit 50), distinct content uris: {len(tracks - {None})}",
        f"  played_at range: {played[0]} .. {played[-1]}" if played else "  played_at: none",
        f"  item.track.type: {dict(types)}",
        f"  context.type: {dict(contexts)}",
        f"  cursors: {'present' if payload.get('cursors') else 'absent'}; "
        f"next: {'present' if payload.get('next') else 'null'}",
    ]


def _played_at_values(page: dict[str, Any]) -> list[str]:
    return [item["played_at"] for item in page.get("items") or [] if item.get("played_at")]


def _page_line(number: int, page: dict[str, Any], new_count: int, detail: str = "") -> str:
    played = sorted(_played_at_values(page))
    span = f"played_at {played[0]} .. {played[-1]}" if played else "played_at: none"
    next_state = "non-null" if page.get("next") else "null"
    return (
        f"  page {number}: items={len(page.get('items') or [])}, {span}, "
        f"not seen on earlier pages={new_count}, next={next_state}{detail}"
    )


def _follow_next(
    api: SpotifyClient,
    conn: Any,
    profile: str,
    run_id: str,
    first_page: dict[str, Any],
    count: int,
) -> list[str]:
    """Follow `next` up to `count` times after page 1, persisting every page (spot-main-R-018)."""
    feed = feed_for_endpoint(RECENTLY_PLAYED_PATH)
    seen = set(_played_at_values(first_page))
    lines = [
        f"\nfollow-next: up to {count} page(s) after page 1",
        _page_line(1, first_page, len(seen)),
    ]
    page = first_page
    for number in range(2, count + 2):
        next_url = page.get("next")
        if not next_url:
            lines.append(f"  stopped before page {number}: page {number - 1} returned next = null")
            break
        params = next_page_params(next_url)
        before_utc = datetime.fromtimestamp(int(params["before"]) / 1000, tz=UTC)
        time.sleep(PAGE_PAUSE_SECONDS)
        payload = api.get(RECENTLY_PLAYED_PATH, params)
        clean, _ = scrub(RECENTLY_PLAYED_PATH, payload)
        source_file = write_response(
            profile, feed, clean, run_id, datetime.now(UTC), key=f"page{number}"
        )
        row_id = insert_response(conn, clean, source_file, profile, feed)
        played = set(_played_at_values(clean))
        new_count = len(played - seen)
        seen |= played
        detail = (
            f", requested before={before_utc:%Y-%m-%dT%H:%M:%S.%f}"[:-3]
            + f"Z, raw.api_response.id={row_id}"
        )
        lines.append(_page_line(number, clean, new_count, detail))
        page = clean
    return lines


def cmd_probe(args: argparse.Namespace) -> int:
    profile = auth.validate_slug(args.profile)
    settings = load_settings()
    run_id = f"probe-{session()}-{uuid.uuid4().hex[:8]}"
    lines = [f"spot probe  profile={profile}  run_id={run_id}"]
    with (
        connect(settings, autocommit=True) as conn,
        refresh_lock(conn),
        SpotifyClient.for_profile(profile, settings) as api,
    ):
        for endpoint, params in PROBE_ENDPOINTS:
            feed = feed_for_endpoint(endpoint)
            payload = api.get(endpoint, params)
            shape = describe_shape(payload)
            clean, removed = scrub(endpoint, payload)
            del payload  # nothing below may touch the unscrubbed response
            source_file = write_response(profile, feed, clean, run_id, datetime.now(UTC))
            row_id = insert_response(conn, clean, source_file, profile, feed)
            lines.append(f"\nGET {endpoint}  (feed={feed})")
            lines.append(f"  data/raw/{source_file}")
            lines.append(f"  raw.api_response.id = {row_id}")
            if endpoint == "/me":
                lines.extend(_summarize_me(clean, removed))
            else:
                lines.extend(_summarize_recent(clean))
            if args.shape:
                lines.append("  shape (as received, types only):")
                lines.extend(f"    {path}: {types}" for path, types in shape.items())
            if endpoint == RECENTLY_PLAYED_PATH and args.follow_next:
                lines.extend(_follow_next(api, conn, profile, run_id, clean, args.follow_next))
    print("\n".join(lines))
    return 0


def cmd_extract_artists(args: argparse.Namespace) -> int:
    profile = auth.validate_slug(args.profile)
    settings = load_settings()
    run_id = f"artists-{session()}-{uuid.uuid4().hex[:8]}"
    stored = 0
    with_genres_key = 0
    with (
        connect(settings, autocommit=True) as conn,
        refresh_lock(conn),
        SpotifyClient.for_profile(profile, settings) as api,
    ):
        artist_ids = artist_ids_to_fetch(conn, refresh=args.refresh)
        print(
            f"spot extract-artists  profile={profile}  run_id={run_id}  to fetch: {len(artist_ids)}"
        )
        for artist_id in artist_ids:
            endpoint = f"/artists/{artist_id}"
            feed = feed_for_endpoint(endpoint)
            payload = api.get(endpoint)
            clean, _ = scrub(endpoint, payload)
            source_file = write_response(
                profile, feed, clean, run_id, datetime.now(UTC), key=artist_id
            )
            insert_response(conn, clean, source_file, profile, feed)
            stored += 1
            with_genres_key += "genres" in clean
    print(
        f"stored {stored} artist response(s) as feed=artist; with a 'genres' key: {with_genres_key}"
    )
    return 0


def _follow_next_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"N must be a whole number, got {value!r}") from exc
    if not 1 <= count <= MAX_FOLLOW_NEXT:
        raise argparse.ArgumentTypeError(f"N must be between 1 and {MAX_FOLLOW_NEXT}, got {count}")
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spot", description="spotify-lakehouse tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="log a profile in to Spotify and store its refresh token")
    p_auth.add_argument("profile", help="profile slug, e.g. marc")
    p_auth.add_argument("--no-browser", action="store_true", help="print the URL only")
    p_auth.set_defaults(func=cmd_auth)

    p_probe = sub.add_parser("probe", help="GET /me and recently-played; persist raw responses")
    p_probe.add_argument("--profile", required=True, help="profile slug, e.g. marc")
    p_probe.add_argument("--shape", action="store_true", help="print key paths and types")
    p_probe.add_argument(
        "--follow-next",
        nargs="?",
        const=1,
        default=0,
        type=_follow_next_count,
        metavar="N",
        help=f"after recently-played page 1, follow `next` N more times (bare flag: 1, max "
        f"{MAX_FOLLOW_NEXT}); every page is persisted",
    )
    p_probe.set_defaults(func=cmd_probe)

    p_artists = sub.add_parser(
        "extract-artists", help="GET /artists/{id} for every artist credited in recently-played"
    )
    p_artists.add_argument("--profile", required=True, help="profile whose token makes the calls")
    p_artists.add_argument(
        "--refresh", action="store_true", help="re-observe artists that already have a response"
    )
    p_artists.set_defaults(func=cmd_extract_artists)

    p_profiles = sub.add_parser(
        "sync-profiles", help="load ~/.config/spot/profiles.csv into spot_meta.profile_registry"
    )
    p_profiles.set_defaults(func=cmd_sync_profiles)

    p_migrate = sub.add_parser("migrate", help="apply raw migrations and create session schemas")
    p_migrate.set_defaults(func=cmd_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        ConfigError,
        auth.AuthError,
        SpotifyApiError,
        migrate.MigrationError,
        UnsafeNextLink,
    ) as exc:
        print(f"spot {args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
