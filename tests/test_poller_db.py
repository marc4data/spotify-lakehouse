"""Integration tests for the poller against the shared Postgres. Nothing written here survives."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from spotify_lakehouse import poller


class _Rollback(Exception):
    pass


def _in_rolled_back_transaction(conn, work) -> None:
    try:
        with conn.transaction():
            work()
            raise _Rollback
    except _Rollback:
        pass


def _insert_raw(conn, profile: str, *played_at: str) -> int:
    from psycopg.types.json import Jsonb

    payload = {"items": [{"played_at": value} for value in played_at]}
    row = conn.execute(
        "insert into raw.api_response (feed, payload, source_file, profile_slug) "
        "values ('recently_played', %s, 'pytest', %s) returning id",
        (Jsonb(payload), profile),
    ).fetchone()
    return int(row[0])


def test_high_water_mark_and_gap_rows(db) -> None:
    profile = f"pytest_{uuid.uuid4().hex[:8]}"

    def work() -> None:
        assert poller.high_water_mark(db, profile) is None
        _insert_raw(db, profile, "2026-09-14T07:00:00.000Z", "2026-09-14T06:00:00.000Z")
        previous = poller.high_water_mark(db, profile)
        assert previous == datetime(2026, 9, 14, 7, tzinfo=UTC)

        page = {"items": [{"played_at": "2026-09-14T09:00:00.000Z"}]}
        raw_id = _insert_raw(db, profile, "2026-09-14T09:00:00.000Z")
        window = poller.window_of(page)
        gap = poller.detect_gap(previous, window)
        poll_id = poller.record_poll(
            db,
            run_id="pytest",
            profile=profile,
            started_at=datetime.now(UTC),
            raw_response_id=raw_id,
            window=window,
            previous_high_water_mark=previous,
            gap=gap,
        )
        gap_row = db.execute(
            "select gap_start, gap_end, items_returned from spot_meta.overflow_gap "
            "where poll_run_id = %s",
            (poll_id,),
        ).fetchone()
        assert gap_row == (previous, datetime(2026, 9, 14, 9, tzinfo=UTC), 1)

        status = poller.profile_status(db, profile, datetime.now(UTC))
        assert status.gap_count == 1
        assert status.last_poll_status == "ok"
        assert status.high_water_mark == datetime(2026, 9, 14, 9, tzinfo=UTC)

    _in_rolled_back_transaction(db, work)


def test_overlapping_poll_records_no_gap_row(db) -> None:
    profile = f"pytest_{uuid.uuid4().hex[:8]}"

    def work() -> None:
        _insert_raw(db, profile, "2026-09-14T07:00:00.000Z")
        previous = poller.high_water_mark(db, profile)
        raw_id = _insert_raw(db, profile, "2026-09-14T08:00:00.000Z", "2026-09-14T07:00:00.000Z")
        window = poller.window_of(
            {
                "items": [
                    {"played_at": "2026-09-14T08:00:00.000Z"},
                    {"played_at": "2026-09-14T07:00:00.000Z"},
                ]
            }
        )
        poll_id = poller.record_poll(
            db,
            run_id="pytest",
            profile=profile,
            started_at=datetime.now(UTC),
            raw_response_id=raw_id,
            window=window,
            previous_high_water_mark=previous,
            gap=poller.detect_gap(previous, window),
        )
        count = db.execute(
            "select count(*) from spot_meta.overflow_gap where poll_run_id = %s", (poll_id,)
        ).fetchone()
        assert count == (0,)

    _in_rolled_back_transaction(db, work)


# --- Required test 2: lock contention exits 0 and records no poll. ------------------------------


def test_refresh_exits_zero_and_records_nothing_while_the_lock_is_held(db, capsys) -> None:
    import psycopg

    from spotify_lakehouse import cli
    from spotify_lakehouse.config import load_settings

    settings = load_settings(require_spotify=False)
    holder = psycopg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_database,
        user=settings.pg_user,
        password=settings.pg_password,
        autocommit=True,
    )
    try:
        holder.execute("select pg_advisory_lock(hashtext('spot_refresh'))")
        before = db.execute("select count(*) from spot_meta.poll_run").fetchone()
        exit_code = cli.main(["refresh"])
        after = db.execute("select count(*) from spot_meta.poll_run").fetchone()
    finally:
        holder.execute("select pg_advisory_unlock(hashtext('spot_refresh'))")
        holder.close()
    assert exit_code == 0
    assert after == before
    assert "lock is held elsewhere; skipped" in capsys.readouterr().out


def test_status_makes_no_api_call_and_exits_zero(
    db, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spotify_lakehouse import cli

    def no_api(*args, **kwargs):
        raise AssertionError("--status must not construct an API client")

    monkeypatch.setattr(cli.SpotifyClient, "for_profile", no_api)
    assert cli.main(["refresh", "--status"]) == 0
    assert "spot refresh --status" in capsys.readouterr().out
