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

import httpx
import structlog

from spotify_lakehouse import auth, export, migrate, musicbrainz, poller, profiles, tracks
from spotify_lakehouse.api import SpotifyApiError, SpotifyClient
from spotify_lakehouse.artists import artist_ids_to_fetch
from spotify_lakehouse.config import ConfigError, load_settings, musicbrainz_contact, session
from spotify_lakehouse.db import connect, refresh_lock, try_advisory_lock, try_refresh_lock
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
    # Resolve sys.stderr when a logger is created, not once at configure time: a stream captured at
    # configure time goes stale if stderr is swapped later (a closed test capture stream, R-033).
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=lambda *args: structlog.PrintLogger(sys.stderr),
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


def cmd_drop_session_schemas(args: argparse.Namespace) -> int:
    if args.session_name == session():
        raise ConfigError(
            f"Refusing to drop this checkout's own session schemas ({args.session_name}). "
            "Run it from another checkout."
        )
    with connect() as conn:
        dropped = migrate.drop_session_schemas(conn, args.session_name)
    print(f"dropped session schemas (if present): {', '.join(dropped)}")
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


def cmd_resolve_musicbrainz(args: argparse.Namespace) -> int:
    settings = load_settings(require_spotify=False)
    contact = musicbrainz_contact(settings)
    run_id = f"musicbrainz-{session()}-{uuid.uuid4().hex[:8]}"
    with (
        connect(settings, autocommit=True) as conn,
        try_advisory_lock(conn, musicbrainz.LOCK_NAME) as acquired,
    ):
        if not acquired:
            print(
                f"spot resolve-musicbrainz: the {musicbrainz.LOCK_NAME} lock is held by another "
                "session; nothing fetched",
                file=sys.stderr,
            )
            return 1
        isrcs = len(musicbrainz.isrcs_to_fetch(conn))
        print(
            f"spot resolve-musicbrainz  run_id={run_id}  ISRCs to fetch: {isrcs} "
            f"(~{isrcs * musicbrainz.MIN_INTERVAL_SECONDS:.0f} s, then one call per new artist)"
        )
        with musicbrainz.MusicBrainzClient(contact) as client:
            summary = musicbrainz.resolve(client, conn, run_id)
    for name, feed in (("isrc_lookup", summary.isrc), ("artist", summary.artist)):
        print(
            f"  {name}: to fetch {feed.to_fetch}, stored {feed.found + feed.not_found} "
            f"(found {feed.found}, unknown to MusicBrainz {feed.not_found})"
        )
    return 0


def cmd_resolve_tracks(args: argparse.Namespace) -> int:
    profile = auth.validate_slug(args.profile)
    settings = load_settings()
    run_id = f"tracks-{session()}-{uuid.uuid4().hex[:8]}"
    with (
        connect(settings, autocommit=True) as conn,
        try_advisory_lock(conn, tracks.LOCK_NAME) as acquired,
    ):
        if not acquired:
            print(
                f"spot resolve-tracks: the {tracks.LOCK_NAME} lock is held elsewhere",
                file=sys.stderr,
            )
            return 1
        pending = len(tracks.track_ids_to_fetch(conn))
        calls = pending if args.limit is None else min(args.limit, pending)
        hours = calls * tracks.SECONDS_PER_CALL_ESTIMATE / 3600
        print(
            f"spot resolve-tracks  profile={profile}  unresolved export track ids: {pending}  "
            f"this run: {calls} GET /tracks/{{id}} call(s), ~{hours:.1f} h at 1 call/s"
        )
        if not args.run:
            print(
                "dry run: no API call made. Batch GET /tracks?ids= returns 403 to this app "
                "(R-037), so every id is one call. Add --run to make them."
            )
            return 0
        with SpotifyClient.for_profile(profile, settings) as api:
            summary = tracks.resolve(api, conn, profile, run_id, limit=args.limit)
    print(
        f"to fetch {summary.to_fetch}: resolved {summary.resolved}, not found {summary.not_found}, "
        f"failed {summary.failed} (retried next run), relinked {summary.relinked}"
    )
    return 0


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}") from exc
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def cmd_load_export(args: argparse.Namespace) -> int:
    profile = auth.validate_slug(args.profile)
    profile_dir = export.exports_dir() / profile
    if not profile_dir.is_dir():
        raise ConfigError(f"{profile_dir} does not exist. Put {profile}'s export zip there first.")
    settings = load_settings(require_spotify=False)
    with (
        connect(settings, autocommit=True) as conn,
        try_advisory_lock(conn, export.LOCK_NAME) as acquired,
    ):
        if not acquired:
            print(
                f"spot load-export: the {export.LOCK_NAME} lock is held elsewhere", file=sys.stderr
            )
            return 1
        registered = conn.execute(
            "select 1 from spot_meta.profile_registry where profile_slug = %s", (profile,)
        ).fetchone()
        if not registered:
            raise ConfigError(
                f"'{profile}' is not in spot_meta.profile_registry. Fix: add it to "
                "~/.config/spot/profiles.csv, then `uv run spot sync-profiles`."
            )
        extracted = export.extract_archives(profile_dir)
        print(f"spot load-export  profile={profile}  newly unzipped members: {len(extracted)}")
        summary = export.load_profile(conn, profile, profile_dir)
    for result in summary.files:
        print(f"  {result.source_file}: records {result.records}, inserted {result.inserted}")
    rate = summary.inserted / summary.seconds if summary.seconds else 0.0
    print(
        f"files {len(summary.files)}, records {summary.records}, inserted {summary.inserted} "
        f"in {summary.seconds:.1f} s ({rate:,.0f} rows/s)"
    )
    return 0


def _iso(value: datetime | None) -> str:
    if value is None:
        return "none"
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _refresh_line(stamp: str, run_id: str, outcome: poller.PollOutcome) -> str:
    window = outcome.window
    verdict = (
        f"OVERFLOW GAP RECORDED {_iso(outcome.gap.start)} .. {_iso(outcome.gap.end)} "
        f"({outcome.gap.items_returned} items returned; plays in this interval may be lost)"
        if outcome.gap
        else "no gap"
    )
    return (
        f"{stamp} spot refresh {run_id} {outcome.profile}: items={window.items_returned} "
        f"window {_iso(window.oldest)} .. {_iso(window.newest)} "
        f"high-water-before={_iso(outcome.previous_high_water_mark)} "
        f"raw_id={outcome.raw_response_id} poll_id={outcome.poll_run_id}: {verdict}"
    )


def _refresh_status() -> int:
    now = datetime.now(UTC)
    stale_after = 2 * poller.POLL_INTERVAL_SECONDS
    with connect(load_settings(require_spotify=False)) as conn:
        to_poll, without_token = poller.api_profiles(conn)
        print(f"spot refresh --status  as of {_iso(now)}")
        print(f"launchd agent {poller.LAUNCHD_LABEL}: {poller.launchd_state()}")
        if not to_poll and not without_token:
            print("no profiles in spot_meta.profile_registry (run `spot sync-profiles`)")
        for slug in to_poll + without_token:
            s = poller.profile_status(conn, slug, now)
            age = (now - s.last_ok_at).total_seconds() if s.last_ok_at else None
            freshness = (
                "never polled"
                if age is None
                else f"{int(age // 60)} min ago" + (" — STALE" if age > stale_after else "")
            )
            last_gap = ""
            if s.last_gap:
                last_gap = f"; last gap {_iso(s.last_gap.start)} .. {_iso(s.last_gap.end)}"
            print(
                f"  {slug}: token {'yes' if s.has_token else 'NO (skipped)'}; "
                f"last successful poll {_iso(s.last_ok_at)} ({freshness}); "
                f"last poll status {s.last_poll_status or 'none'}; "
                f"polls in 24 h {s.polls_24h} (errors {s.errors_24h}); "
                f"high-water mark {_iso(s.high_water_mark)}; "
                f"overflow gaps recorded {s.gap_count}{last_gap}"
            )
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    if args.status:
        return _refresh_status()
    stamp = f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}"
    run_id = f"refresh-{session()}-{uuid.uuid4().hex[:8]}"
    failures = 0
    with (
        connect(load_settings(require_spotify=False), autocommit=True) as conn,
        try_refresh_lock(conn) as acquired,
    ):
        if not acquired:
            print(
                f"{stamp} spot refresh: spot_refresh lock is held elsewhere; "
                "skipped, nothing recorded"
            )
            return 0
        settings = load_settings()
        to_poll, without_token = poller.record_api_access(conn, run_id)
        for slug in without_token:
            print(f"{stamp} spot refresh: {slug} has no stored token; skipped (spot auth {slug})")
        if not to_poll:
            print(f"{stamp} spot refresh: no API-enabled profiles to poll", file=sys.stderr)
            return 1
        for slug in to_poll:
            started_at = datetime.now(UTC)
            try:
                with SpotifyClient.for_profile(slug, settings) as api:
                    outcome = poller.poll_profile(api, conn, slug, run_id)
            except (SpotifyApiError, auth.AuthError, httpx.HTTPError) as exc:
                failures += 1
                poller.record_poll_error(
                    conn, run_id=run_id, profile=slug, started_at=started_at, message=str(exc)
                )
                print(f"{stamp} spot refresh {run_id} {slug}: FAILED: {exc}", file=sys.stderr)
                continue
            print(_refresh_line(stamp, run_id, outcome))
    return 1 if failures else 0


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

    p_refresh = sub.add_parser(
        "refresh",
        help="poll recently-played once for every API-enabled profile and record overflow gaps",
    )
    p_refresh.add_argument(
        "--status",
        action="store_true",
        help="last successful poll, high-water mark and gaps per profile; makes no API call",
    )
    p_refresh.set_defaults(func=cmd_refresh)

    p_artists = sub.add_parser(
        "extract-artists", help="GET /artists/{id} for every artist credited in recently-played"
    )
    p_artists.add_argument("--profile", required=True, help="profile whose token makes the calls")
    p_artists.add_argument(
        "--refresh", action="store_true", help="re-observe artists that already have a response"
    )
    p_artists.set_defaults(func=cmd_extract_artists)

    p_tracks = sub.add_parser(
        "resolve-tracks",
        help="GET /tracks/{id} for export track URIs; a dry run (count and estimate) unless --run",
    )
    p_tracks.add_argument("--profile", required=True, help="profile whose token makes the calls")
    p_tracks.add_argument(
        "--run", action="store_true", help="make the calls (one per track, ~1/s; resumable)"
    )
    p_tracks.add_argument(
        "--limit", type=_positive_int, default=None, help="look up at most N ids this run"
    )
    p_tracks.set_defaults(func=cmd_resolve_tracks)

    p_export = sub.add_parser(
        "load-export",
        help="unzip and load a profile's Extended Streaming History into raw.export_record",
    )
    p_export.add_argument("--profile", required=True, help="profile slug, e.g. marc")
    p_export.set_defaults(func=cmd_load_export)

    p_mb = sub.add_parser(
        "resolve-musicbrainz",
        help="ISRC -> MusicBrainz recording -> artist genres and tags; resumable, skips stored ids",
    )
    p_mb.set_defaults(func=cmd_resolve_musicbrainz)

    p_profiles = sub.add_parser(
        "sync-profiles", help="load ~/.config/spot/profiles.csv into spot_meta.profile_registry"
    )
    p_profiles.set_defaults(func=cmd_sync_profiles)

    p_drop = sub.add_parser(
        "drop-session-schemas", help="drop stg_<session> / mart_<session> of a removed worktree"
    )
    p_drop.add_argument("session_name", metavar="session", help="worktree session, e.g. wtc")
    p_drop.set_defaults(func=cmd_drop_session_schemas)

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
        musicbrainz.MusicBrainzError,
        export.ExportError,
    ) as exc:
        print(f"spot {args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
