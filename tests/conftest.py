from __future__ import annotations

from pathlib import Path

import pytest

REAL_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated fake checkout, so the real .session never leaks into a test."""
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setenv("SPOT_REPO_ROOT", str(root))
    return root


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated ~/.config/spot, so tests never read or write real credentials."""
    path = tmp_path / "config-spot"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("SPOT_CONFIG_DIR", str(path))
    return path


@pytest.fixture
def db():
    """A connection to the shared Postgres, for integration tests. Skips when it is unreachable.

    Never applies migrations: `make migrate` is the only schema writer (R-004 C2, closed R-017).
    If migrations are pending the tests skip and say so, rather than changing the shared database.
    """
    psycopg = pytest.importorskip("psycopg")
    from spotify_lakehouse import migrate
    from spotify_lakehouse.config import ConfigError, load_settings

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
    try:
        unapplied = migrate.pending(connection, REAL_MIGRATIONS)
    except migrate.MigrationError as exc:
        connection.close()
        pytest.fail(str(exc))
    if unapplied:
        connection.close()
        pytest.skip(f"migrations pending ({', '.join(unapplied)}): run `make migrate`")
    yield connection
    connection.close()


def fake_hex(length: int = 32) -> str:
    # Built at runtime so no credential-shaped literal exists in the test source.
    return ("0123456789abcdef" * 8)[:length]
