"""Token presence made observable to dbt (spot-main-R-033, R-008 F5). Nothing here persists."""

from __future__ import annotations

import pytest

from spotify_lakehouse import poller


class _Rollback(Exception):
    pass


def test_record_api_access_writes_one_boolean_per_registry_profile(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(poller.auth, "stored_profiles", lambda: ["pytest_has_token"])
    try:
        with db.transaction():
            db.execute(
                "insert into spot_meta.profile_registry "
                "(profile_slug, household_role, home_timezone) values "
                "('pytest_has_token', 'friend', 'UTC'), ('pytest_no_token', 'friend', 'UTC')"
            )
            to_poll, skipped = poller.record_api_access(db, "pytest-run")
            assert "pytest_has_token" in to_poll
            assert "pytest_no_token" in skipped
            rows = dict(
                db.execute(
                    "select profile_slug, has_stored_token from spot_meta.api_access_observation "
                    "where run_id = 'pytest-run'"
                ).fetchall()
            )
            assert rows["pytest_has_token"] is True
            assert rows["pytest_no_token"] is False
            columns = {
                row[0]
                for row in db.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'spot_meta' and table_name = 'api_access_observation'"
                )
            }
            assert not any("token" in c and c != "has_stored_token" for c in columns)
            raise _Rollback
    except _Rollback:
        pass


def test_a_skipped_poll_records_no_api_access_observation(db, capsys) -> None:
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
        before = db.execute("select count(*) from spot_meta.api_access_observation").fetchone()
        assert cli.main(["refresh"]) == 0
        after = db.execute("select count(*) from spot_meta.api_access_observation").fetchone()
    finally:
        holder.execute("select pg_advisory_unlock(hashtext('spot_refresh'))")
        holder.close()
    assert after == before
