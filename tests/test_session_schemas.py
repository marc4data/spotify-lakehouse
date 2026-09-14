"""migrate.drop_session_schemas: worktree sessions only, never main or analytics (R-033)."""

from __future__ import annotations

import pytest

from spotify_lakehouse import migrate


class _Rollback(Exception):
    pass


@pytest.mark.parametrize(
    "session_name", ["main", "analytics", "wtab", "", "wt1", "stg_wtc", "prod"]
)
def test_drop_refuses_anything_but_a_worktree_token(db, session_name: str) -> None:
    # Inside a transaction that is always rolled back: if the guard is ever broken, this test fails
    # without really dropping stg_main / mart_main (Postgres DDL is transactional).
    before = db.execute("select count(*) from pg_namespace").fetchone()
    try:
        with db.transaction():
            with pytest.raises(migrate.MigrationError, match="Refusing to drop"):
                migrate.drop_session_schemas(db, session_name)
            assert db.execute("select count(*) from pg_namespace").fetchone() == before
            raise _Rollback
    except _Rollback:
        pass


def test_drop_removes_only_the_named_worktree_schemas(db) -> None:
    def names() -> set[str]:
        return {
            row[0]
            for row in db.execute("select nspname from pg_namespace where nspname ~ '^(stg|mart)_'")
        }

    try:
        with db.transaction():
            db.execute("create schema stg_wty")
            db.execute("create schema mart_wty")
            db.execute("create table stg_wty.t (id int)")
            assert {"stg_wty", "mart_wty", "stg_main", "mart_main"} <= names()
            dropped = migrate.drop_session_schemas(db, "wty")
            assert dropped == ("stg_wty", "mart_wty")
            remaining = names()
            assert "stg_wty" not in remaining and "mart_wty" not in remaining
            assert {"stg_main", "mart_main"} <= remaining
            raise _Rollback
    except _Rollback:
        pass


def test_dropping_absent_schemas_is_a_no_op(db) -> None:
    try:
        with db.transaction():
            assert migrate.drop_session_schemas(db, "wtx") == ("stg_wtx", "mart_wtx")
            raise _Rollback
    except _Rollback:
        pass
