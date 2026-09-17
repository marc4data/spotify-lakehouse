"""Notebook entry point: `ctx = setup(profile="marc")` (docs/notebook-specs.md §0).

Resolves `.session`, reads credentials from ~/.config/spot/, opens the Postgres connection,
configures pandas / matplotlib / plotly, and returns a context. A notebook holds no connection
string and no credential: without this package it cannot run at all, which is the point.
"""

from __future__ import annotations

import os
import sys
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
from spotify_lakehouse.db import connect, try_advisory_lock

# Slower than the poller's pacing: a notebook makes a burst of calls and must not 429 halfway.
API_MIN_INTERVAL_SECONDS = 1.5
# The notebook's own lock, never spot_refresh (R-042): see NotebookContext.api.
NOTEBOOK_LOCK_NAME = "spot_notebook"
# Which person a notebook runs for, set by `make report-02 PROFILE=<slug>` (R-009).
PROFILE_ENV = "SPOT_PROFILE"
DEFAULT_PROFILE = "marc"

# Marc's standing preference for DataFrame tables in notebooks (light, print-friendly).
TABLE_CSS = """
<style>
table.dataframe {
    border-collapse: collapse !important;
    border: 2px solid rgba(0,0,0,0.2) !important;
}
table.dataframe td, table.dataframe th {
    border: 1.5px solid rgba(0,0,0,0.15) !important;
    /* Left-align every cell, headers included (Marc, 2026-09-17).

       Measured in the rendered report rather than assumed: no td or th carries a style
       attribute, so values already fall back to the browser's left default. What was
       right-aligned is the HEADER row. pandas emits a thead-th rule setting text-align right,
       once per table, plus an inline text-align right on the header tr itself; both land after
       nb2report's own left rule at equal specificity and so win on document order. The result
       was right-aligned labels sitting over left-aligned data.

       !important beats a non-important rule at any specificity, and a direct rule on th beats
       the inline style inherited from tr, so this settles it here rather than in nb2report,
       which is another project and not this one's to edit. No numeric column changes: they
       were already left.

       Deliberately no braces or backticks in this comment: it is emitted verbatim into the
       report's stylesheet, and brace characters inside a comment survive a real CSS parser but
       break naive ones, including the extractor used to verify this. */
    text-align: left !important;
}
table.dataframe thead th {
    background-color: #f0f0f0 !important;
    font-weight: bold !important;
}
</style>
"""


def aligned_table_html(frame: pd.DataFrame) -> str | None:
    """A DataFrame rendered with strings left-aligned and numbers right-aligned, or None.

    Marc, 2026-09-17: string columns read as right-aligned when running cells in VS Code.
    `TABLE_CSS` fixes the rendered report, but it is injected once as the setup cell's output and
    VS Code renders each cell's output in its own context, so a stylesheet from one cell need not
    reach another. pandas has no such problem because it emits a `<style>` block with EVERY table —
    so this does the same, via `Styler`, and the alignment travels with the table it describes.

    Returning None means "no opinion": the caller falls back to pandas' own renderer. That happens
    whenever styling would change behaviour rather than just appearance:

    * **Frames pandas would truncate.** `_repr_html_` honours `display.max_rows`; `Styler` does not,
      and would render all of a long frame — measured at 3.1x the bytes for 500 rows. The options
      are read at call time, never baked in, because `_configure_libraries` changes them after
      import (and `display.max_columns` is 0 by default, which means *unlimited*, so a naive `>`
      comparison misfires).
    * **Non-unique columns or index.** `Styler.apply`/`.map` raise `KeyError` on these — measured,
      not guessed — and a raise inside `_repr_html_` breaks the cell's whole output, which is far
      worse than misaligned text.

    `class="dataframe"` is pinned deliberately: nb2report detects tables with a plain
    `'dataframe' in html` substring test (`parser.py:178`), and without it the report would silently
    lose its table wrapper and column sorting.

    The emitted rules are id-scoped (`#T_xxx…`) and `!important`, so they beat TABLE_CSS's blanket
    `table.dataframe td { text-align: left !important }` on specificity — an id (1,0,0) outranks a
    class-plus-elements selector (0,1,2). Without `!important` on both sides that blanket rule would
    silently flatten the numeric columns back to the left.
    """
    max_rows = pd.get_option("display.max_rows")
    if max_rows and len(frame) > max_rows:
        return None
    if frame.size > pd.get_option("styler.render.max_elements"):
        return None
    if not frame.columns.is_unique or not frame.index.is_unique:
        return None

    numeric = frame.select_dtypes("number").columns.tolist()
    other = [column for column in frame.columns if column not in numeric]
    try:
        styler = frame.style.set_table_attributes('border="1" class="dataframe"').set_table_styles(
            [{"selector": "th", "props": [("text-align", "left !important")]}]
        )
        if other:
            styler = styler.set_properties(subset=other, **{"text-align": "left !important"})
        if numeric:
            styler = styler.set_properties(subset=numeric, **{"text-align": "right !important"})
        return styler.to_html()
    except Exception:  # noqa: BLE001 - a repr must never raise; fall back to pandas'
        return None


def _install_dataframe_alignment() -> None:
    """Route `display(df)` through `aligned_table_html`, falling back to pandas untouched.

    Idempotent on purpose: re-running a notebook's setup cell calls this again, and without the
    marker each call would wrap the previous wrapper.
    """
    original = pd.DataFrame._repr_html_
    if getattr(original, "_spot_aligned", False):
        return

    def _repr_html_(self: pd.DataFrame) -> str | None:
        return aligned_table_html(self) or original(self)

    _repr_html_._spot_aligned = True  # type: ignore[attr-defined]
    pd.DataFrame._repr_html_ = _repr_html_  # type: ignore[method-assign]


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
        """A paced Spotify client under the notebook's own `spot_notebook` lock (R-042, R-041 F3).

        Not `spot_refresh`: the poller takes that lock without waiting and skips when it is held, so
        a notebook holding it for a sampling burst made polls skip, and recently-played keeps only
        50 items. Nor `spot_refresh` per call: a poll landing during one call, or during a
        Retry-After sleep of up to 120 s, would still skip. Running beside the poller costs one
        overlapping call every 30 minutes, the same trade `resolve-tracks` already makes.
        """
        run_id = f"notebook-{self.session}-{self.started_at:%Y%m%dT%H%M%S}"
        with try_advisory_lock(self.conn, NOTEBOOK_LOCK_NAME) as acquired:
            if not acquired:
                raise ConfigError(
                    f"Another notebook holds the {NOTEBOOK_LOCK_NAME} lock and is calling Spotify. "
                    "Fix: let it finish, then re-run this cell."
                )
            with self._client(run_id) as client:
                yield client

    def _client(self, run_id: str) -> SpotifyClient:
        return SpotifyClient.for_profile(
            self.profile,
            self.settings,
            min_interval=API_MIN_INTERVAL_SECONDS,
            on_rate_limited=rate_limits.recorder(
                self.conn, run_id=run_id, command="notebook", profile=self.profile
            ),
        )

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
        # The stylesheet above covers the rendered report; this covers the notebook UI, where a
        # style from one cell's output need not reach another's. Gated by the same flag so tests
        # opt out of both presentation side effects, pandas' global _repr_html_ included.
        _install_dataframe_alignment()


def registered_profiles(conn: psycopg.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "select profile_slug from spot_meta.profile_registry order by profile_slug"
        )
    ]


def unregistered_message(profile: str, registered: list[str]) -> str:
    return (
        f"Profile {profile!r} is not in spot_meta.profile_registry "
        f"(registered: {', '.join(registered) or 'none'}). Fix: add a row to "
        "~/.config/spot/profiles.csv, then run `uv run spot sync-profiles`."
    )


def setup(profile: str | None = None, *, table_css: bool = True) -> NotebookContext:
    """Open the notebook context for one profile. Raises ConfigError, with the fix, if it cannot.

    The profile defaults from `SPOT_PROFILE` (R-009), so a notebook's own cell names nobody and
    `make report-02 PROFILE=<slug>` chooses the person; without the variable it is `marc`.
    """
    profile = profile or os.environ.get(PROFILE_ENV) or DEFAULT_PROFILE
    session_name = session()
    settings = load_settings()
    conn = connect(settings, autocommit=True)
    registered = registered_profiles(conn)
    if profile not in registered:
        conn.close()
        raise ConfigError(unregistered_message(profile, registered))
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


def main() -> int:
    """`python -m spotify_lakehouse.notebook`: check that `SPOT_PROFILE` names a registered profile.

    `make report-02` runs this before executing the notebook, so an unknown slug fails in one line
    naming the registered slugs, not in a traceback from inside a kernel.
    """
    profile = os.environ.get(PROFILE_ENV, "")
    try:
        with connect(load_settings(require_spotify=False)) as conn:
            registered = registered_profiles(conn)
    except (ConfigError, psycopg.OperationalError) as exc:
        print(f"spot notebook: {exc}", file=sys.stderr)
        return 2
    if profile not in registered:
        message = unregistered_message(profile or "(unset)", registered)
        print(f"spot notebook: {message}", file=sys.stderr)
        return 2
    print(f"spot notebook: profile {profile!r} is registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
