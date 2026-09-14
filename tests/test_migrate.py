from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from spotify_lakehouse import migrate
from spotify_lakehouse.config import ConfigError, load_settings

REAL_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_real_migrations_are_contiguous_and_well_named() -> None:
    migrations = migrate.discover(REAL_MIGRATIONS)
    assert migrations[0].filename == "0001_raw_api_response.sql"


def test_bad_filename_rejected(tmp_path: Path) -> None:
    (tmp_path / "create_stuff.sql").write_text("select 1")
    with pytest.raises(migrate.MigrationError, match="NNNN_snake_case"):
        migrate.discover(tmp_path)


def test_gap_rejected(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("select 1")
    (tmp_path / "0003_c.sql").write_text("select 1")
    with pytest.raises(migrate.MigrationError, match="no gaps"):
        migrate.discover(tmp_path)


# --- Integration: runs only when the shared Postgres is reachable. Leaves nothing behind. --------


class _Rollback(Exception):
    pass


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    try:
        settings = load_settings(require_spotify=False)
        connection = psycopg.connect(
            host=settings.pg_host,
            port=settings.pg_port,
            dbname=settings.pg_database,
            user=settings.pg_user,
            password=settings.pg_password,
            connect_timeout=2,
        )
    except (ConfigError, psycopg.OperationalError) as exc:
        pytest.skip(f"database not available: {exc.__class__.__name__}")
    migrate.apply(connection, REAL_MIGRATIONS)
    yield connection
    connection.close()


def _in_rolled_back_transaction(conn, work) -> None:
    try:
        with conn.transaction():
            work()
            raise _Rollback
    except _Rollback:
        pass


def test_migrate_is_idempotent(conn) -> None:
    assert migrate.apply(conn, REAL_MIGRATIONS) == []


def test_raw_api_response_is_append_only(conn) -> None:
    import psycopg

    marker = f"pytest-{uuid.uuid4().hex[:8]}"

    def work() -> None:
        row = conn.execute(
            "insert into raw.api_response (feed, payload, source_file, profile_slug) "
            "values ('pytest', '{}'::jsonb, %s, 'pytest') returning id",
            (marker,),
        ).fetchone()
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("update raw.api_response set profile_slug = 'x' where id = %s", row)

    _in_rolled_back_transaction(conn, work)
    left = conn.execute(
        "select count(*) from raw.api_response where source_file = %s", (marker,)
    ).fetchone()
    assert left == (0,)


def test_raw_api_response_requires_feed(conn) -> None:
    import psycopg

    def work() -> None:
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute(
                "insert into raw.api_response (payload, source_file, profile_slug) "
                "values ('{}'::jsonb, 'pytest', 'pytest')"
            )

    _in_rolled_back_transaction(conn, work)


def test_raw_api_response_feed_format_is_checked(conn) -> None:
    import psycopg

    def work() -> None:
        with pytest.raises(psycopg.errors.CheckViolation, match="api_response_feed_format"):
            conn.execute(
                "insert into raw.api_response (feed, payload, source_file, profile_slug) "
                "values ('Recently-Played', '{}'::jsonb, 'pytest', 'pytest')"
            )

    _in_rolled_back_transaction(conn, work)


def test_profile_registry_rejects_unknown_role(conn) -> None:
    import psycopg

    def work() -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "insert into spot_meta.profile_registry "
                "(profile_slug, household_role, home_timezone) values ('pytest', 'parent', 'UTC')"
            )

    _in_rolled_back_transaction(conn, work)
