from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from spotify_lakehouse import migrate
from tests.conftest import REAL_MIGRATIONS


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


# --- Integration: shared Postgres. The `db` fixture never applies migrations. ---------------


class _Rollback(Exception):
    pass


def _in_rolled_back_transaction(conn, work) -> None:
    try:
        with conn.transaction():
            work()
            raise _Rollback
    except _Rollback:
        pass


def test_pending_lists_unapplied_without_applying_them(db, tmp_path: Path) -> None:
    for path in REAL_MIGRATIONS.glob("*.sql"):
        shutil.copy(path, tmp_path / path.name)
    next_version = len(migrate.discover(REAL_MIGRATIONS)) + 1
    extra = f"{next_version:04d}_pytest_never_applied.sql"
    (tmp_path / extra).write_text("create table pytest_must_not_exist (id int);")

    before = db.execute("select count(*) from spot_meta.schema_migrations").fetchone()
    assert migrate.pending(db, tmp_path) == [extra]
    after = db.execute("select count(*) from spot_meta.schema_migrations").fetchone()
    exists = db.execute("select to_regclass('public.pytest_must_not_exist')").fetchone()
    assert before == after
    assert exists == (None,)


def test_raw_api_response_is_append_only(db) -> None:
    import psycopg

    marker = f"pytest-{uuid.uuid4().hex[:8]}"

    def work() -> None:
        row = db.execute(
            "insert into raw.api_response (feed, payload, source_file, profile_slug) "
            "values ('pytest', '{}'::jsonb, %s, 'pytest') returning id",
            (marker,),
        ).fetchone()
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            db.execute("update raw.api_response set profile_slug = 'x' where id = %s", row)

    _in_rolled_back_transaction(db, work)
    left = db.execute(
        "select count(*) from raw.api_response where source_file = %s", (marker,)
    ).fetchone()
    assert left == (0,)


def test_raw_api_response_requires_feed(db) -> None:
    import psycopg

    def work() -> None:
        with pytest.raises(psycopg.errors.NotNullViolation):
            db.execute(
                "insert into raw.api_response (payload, source_file, profile_slug) "
                "values ('{}'::jsonb, 'pytest', 'pytest')"
            )

    _in_rolled_back_transaction(db, work)


def test_raw_api_response_feed_format_is_checked(db) -> None:
    import psycopg

    def work() -> None:
        with pytest.raises(psycopg.errors.CheckViolation, match="api_response_feed_format"):
            db.execute(
                "insert into raw.api_response (feed, payload, source_file, profile_slug) "
                "values ('Recently-Played', '{}'::jsonb, 'pytest', 'pytest')"
            )

    _in_rolled_back_transaction(db, work)


def test_profile_registry_rejects_unknown_role(db) -> None:
    import psycopg

    def work() -> None:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "insert into spot_meta.profile_registry "
                "(profile_slug, household_role, home_timezone) values ('pytest', 'parent', 'UTC')"
            )

    _in_rolled_back_transaction(db, work)
