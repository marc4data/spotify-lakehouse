"""`spot` command line: auth, probe, extract-artists, sync-profiles, migrate."""

from __future__ import annotations

import argparse
import logging
import sys
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
from spotify_lakehouse.raw_store import feed_for_endpoint, insert_response, scrub, write_response
from spotify_lakehouse.shape import describe_shape

PROBE_ENDPOINTS: tuple[tuple[str, dict[str, Any] | None], ...] = (
    ("/me", None),
    ("/me/player/recently-played", {"limit": 50}),
)


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
    except (ConfigError, auth.AuthError, SpotifyApiError, migrate.MigrationError) as exc:
        print(f"spot {args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
