"""§11's catalog must not silently skip an object (spot-main-R-049).

The catalog is introspected when the notebook runs. Its whole value is that it lists what the
database has, so the guard is coverage: every object in every schema the project owns must appear.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spotify_lakehouse import data_inventory


def _context(db):
    from spotify_lakehouse.config import mart_schema, session, stg_schema
    from spotify_lakehouse.notebook import NotebookContext

    name = session()
    return NotebookContext(
        profile="marc",
        session=name,
        stg_schema=stg_schema(name),
        mart_schema=mart_schema(name),
        started_at=datetime.now(UTC),
        conn=db,
        settings=None,
    )


def test_the_catalog_covers_every_object_the_database_reports(db) -> None:
    """The R-049 guard: no schema the project owns may be missing from the catalog."""
    ctx = _context(db)
    schemas = [schema for _, schema in data_inventory.catalog_schemas(ctx)]
    assert {"raw", "spot_meta", ctx.stg_schema, ctx.mart_schema} <= set(schemas)

    expected = {
        (schema, name)
        for schema, name in db.execute(
            "select table_schema, table_name from information_schema.tables "
            "where table_schema = any(%s)",
            (schemas,),
        ).fetchall()
    }
    catalog = data_inventory.catalog_objects(ctx)
    listed = set(zip(catalog["schema"], catalog["name"], strict=True))
    assert expected, "the database reported no objects at all"
    assert listed == expected, f"missing from the catalog: {sorted(expected - listed)}"


def test_every_object_carries_a_row_count_and_a_description(db) -> None:
    catalog = data_inventory.catalog_objects(_context(db))
    assert (catalog["rows"] >= 0).all()
    assert catalog["what_it_is"].str.len().gt(0).all()
    assert catalog["columns"].gt(0).all()


@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        (
            ["play_event_key", "ended_at_utc", "ms_played", "platform"],
            "ended_at_utc, ms_played, platform",
        ),
        (["genre_key", "genre_name", "tag_type"], "genre_name, tag_type"),
        (["content_key"], "content_key"),
    ],
)
def test_example_query_prefers_columns_that_say_something(
    columns: list[str], expected: str
) -> None:
    statement = data_inventory.example_query("mart_main", "thing", columns)
    assert statement == f"select {expected} from mart_main.thing limit 5"


def test_headers_come_from_the_files_not_from_prose() -> None:
    headers = data_inventory.object_headers()
    comment, source = headers["fct_play_event"]
    assert source.endswith("fct_play_event.sql")
    assert comment, "the model has a header comment and the catalog must use it"
    assert "genre_bucket_map" in headers  # seeds describe themselves in their .yml
