"""Numbered, checksummed, idempotent migrations for the shared `raw` schema."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from spotify_lakehouse.config import mart_schema, repo_root, stg_schema

FILENAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def migrations_dir() -> Path:
    return repo_root() / "migrations"


def discover(directory: Path | None = None) -> list[Migration]:
    directory = directory or migrations_dir()
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME.fullmatch(path.name)
        if not match:
            raise MigrationError(f"{path.name}: migration files must be named NNNN_snake_case.sql")
        migrations.append(Migration(int(match.group(1)), path.name, path.read_text()))
    expected = list(range(1, len(migrations) + 1))
    actual = [m.version for m in migrations]
    if actual != expected:
        raise MigrationError(f"Migration versions must run 1..N with no gaps; found {actual}")
    return migrations


def apply(conn: psycopg.Connection, directory: Path | None = None) -> list[str]:
    """Apply pending migrations in one transaction. Returns filenames applied."""
    applied_now = []
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(hashtext('spot_migrate'))")
        conn.execute("create schema if not exists spot_meta")
        conn.execute(
            """
            create table if not exists spot_meta.schema_migrations (
                version int primary key,
                filename text not null,
                checksum text not null,
                applied_at timestamptz not null default now()
            )
            """
        )
        rows = conn.execute("select version, checksum from spot_meta.schema_migrations").fetchall()
        applied = dict(rows)
        for migration in discover(directory):
            if migration.version in applied:
                if applied[migration.version] != migration.checksum:
                    raise MigrationError(
                        f"{migration.filename} was edited after it was applied. "
                        "Never edit an applied migration; add a new numbered file instead."
                    )
                continue
            conn.execute(migration.sql)
            conn.execute(
                "insert into spot_meta.schema_migrations (version, filename, checksum) "
                "values (%s, %s, %s)",
                (migration.version, migration.filename, migration.checksum),
            )
            applied_now.append(migration.filename)
    return applied_now


def pending(conn: psycopg.Connection, directory: Path | None = None) -> list[str]:
    """Filenames not yet applied. Read-only: never creates, applies or records anything.

    Raises MigrationError if an applied migration was edited, exactly as apply() would.
    """
    exists = conn.execute(
        "select to_regclass('spot_meta.schema_migrations') is not null"
    ).fetchone()
    applied: dict[int, str] = {}
    if exists and exists[0]:
        applied = dict(
            conn.execute("select version, checksum from spot_meta.schema_migrations").fetchall()
        )
    unapplied = []
    for migration in discover(directory):
        if migration.version not in applied:
            unapplied.append(migration.filename)
        elif applied[migration.version] != migration.checksum:
            raise MigrationError(
                f"{migration.filename} was edited after it was applied. "
                "Never edit an applied migration; add a new numbered file instead."
            )
    return unapplied


def ensure_session_schemas(conn: psycopg.Connection, session_name: str) -> tuple[str, str]:
    """Create stg_<session> and mart_<session> if absent. Never touches `analytics`."""
    schemas = (stg_schema(session_name), mart_schema(session_name))
    with conn.transaction():
        for schema in schemas:
            conn.execute(
                psycopg.sql.SQL("create schema if not exists {}").format(
                    psycopg.sql.Identifier(schema)
                )
            )
    return schemas
