"""Notebook entry point: `ctx = setup(profile="marc")` (docs/notebook-specs.md §0).

Resolves `.session`, reads credentials from ~/.config/spot/, opens the Postgres connection,
configures pandas / matplotlib / plotly, and returns a context. A notebook holds no connection
string and no credential: without this package it cannot run at all, which is the point.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import psycopg
from psycopg import sql

from spotify_lakehouse import rate_limits
from spotify_lakehouse.api import SpotifyClient
from spotify_lakehouse.config import (
    ConfigError,
    Settings,
    load_settings,
    mart_schema,
    session,
    stg_schema,
)
from spotify_lakehouse.db import connect, refresh_lock

# Slower than the poller's pacing: a notebook makes a burst of calls and must not 429 halfway.
API_MIN_INTERVAL_SECONDS = 1.5

# Marc's standing preference for DataFrame tables in notebooks (light, print-friendly).
TABLE_CSS = """
<style>
table.dataframe {
    border-collapse: collapse !important;
    border: 2px solid rgba(0,0,0,0.2) !important;
}
table.dataframe td, table.dataframe th {
    border: 1.5px solid rgba(0,0,0,0.15) !important;
}
table.dataframe thead th {
    background-color: #f0f0f0 !important;
    font-weight: bold !important;
}
</style>
"""


@dataclass
class NotebookContext:
    profile: str
    session: str
    stg_schema: str
    mart_schema: str
    started_at: datetime
    conn: psycopg.Connection = field(repr=False)
    settings: Settings = field(repr=False)

    def _compose(self, statement: str) -> sql.Composed:
        """`{stg}` / `{mart}` become this session's quoted schemas; double any literal brace."""
        return sql.SQL(statement).format(
            stg=sql.Identifier(self.stg_schema), mart=sql.Identifier(self.mart_schema)
        )

    def frame(self, statement: str, params: Any = None) -> pd.DataFrame:
        with self.conn.cursor() as cur:
            cur.execute(self._compose(statement), params)
            columns = [column.name for column in cur.description] if cur.description else []
            return pd.DataFrame(cur.fetchall(), columns=columns)

    def rows(self, statement: str, params: Any = None) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(self._compose(statement), params)
            return cur.fetchall()

    def scalar(self, statement: str, params: Any = None) -> Any:
        result = self.rows(statement, params)
        return result[0][0] if result else None

    @contextmanager
    def api(self) -> Iterator[SpotifyClient]:
        """A paced Spotify client, holding the `spot_refresh` lock so the poller skips meanwhile."""
        run_id = f"notebook-{self.session}-{self.started_at:%Y%m%dT%H%M%S}"
        with (
            refresh_lock(self.conn),
            SpotifyClient.for_profile(
                self.profile,
                self.settings,
                min_interval=API_MIN_INTERVAL_SECONDS,
                on_rate_limited=rate_limits.recorder(
                    self.conn, run_id=run_id, command="notebook", profile=self.profile
                ),
            ) as client,
        ):
            yield client

    def close(self) -> None:
        self.conn.close()


def _configure_libraries(table_css: bool) -> None:
    import matplotlib
    import plotly.io as pio

    pd.set_option("display.max_columns", 60)
    pd.set_option("display.max_colwidth", 60)
    pd.set_option("display.width", 200)
    matplotlib.rcParams.update(
        {
            "figure.figsize": (9, 4),
            "figure.dpi": 110,
            "axes.grid": True,
            "grid.alpha": 0.3,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.size": 10,
        }
    )
    pio.templates.default = "plotly_white"
    if table_css:
        from IPython.display import HTML, display

        display(HTML(TABLE_CSS))


def setup(profile: str = "marc", *, table_css: bool = True) -> NotebookContext:
    """Open the notebook context for one profile. Raises ConfigError, with the fix, if it cannot."""
    session_name = session()
    settings = load_settings()
    conn = connect(settings, autocommit=True)
    registered = [
        row[0]
        for row in conn.execute(
            "select profile_slug from spot_meta.profile_registry order by profile_slug"
        )
    ]
    if profile not in registered:
        conn.close()
        raise ConfigError(
            f"Profile {profile!r} is not in spot_meta.profile_registry "
            f"(registered: {', '.join(registered) or 'none'}). Fix: add a row to "
            "~/.config/spot/profiles.csv, then run `uv run spot sync-profiles`."
        )
    _configure_libraries(table_css)
    return NotebookContext(
        profile=profile,
        session=session_name,
        stg_schema=stg_schema(session_name),
        mart_schema=mart_schema(session_name),
        started_at=datetime.now(UTC),
        conn=conn,
        settings=settings,
    )
