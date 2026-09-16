"""Redaction of the warehouse samples in 01_data_inventory (spot-main-R-042)."""

from __future__ import annotations

import pandas as pd

from spotify_lakehouse import data_inventory
from spotify_lakehouse.redact import REDACTED


def test_warehouse_samples_mask_identifier_shaped_columns() -> None:
    frame = pd.DataFrame(
        [
            {
                "content_uri": "spotify:track:x",
                "artist_id": "a",
                "artist_mbid": "m",
                "request_key": "USX",
                "id": "i",
                "display_name": "someone",
                "track_name": "Song",
                "ms_played": 1000,
            },
            {
                "content_uri": None,
                "artist_id": "b",
                "artist_mbid": None,
                "request_key": "USY",
                "id": "j",
                "display_name": None,
                "track_name": "Other",
                "ms_played": 2000,
            },
        ]
    )
    out = data_inventory.redact_sample(frame)
    for column in ("content_uri", "artist_id", "artist_mbid", "request_key", "id", "display_name"):
        assert set(out[column].dropna()) == {REDACTED}, column
    assert out["content_uri"].isna().tolist() == [False, True]  # nulls stay null
    assert out["track_name"].tolist() == ["Song", "Other"]
    assert out["ms_played"].tolist() == [1000, 2000]


def test_schema_examples_of_identifier_fields_are_masked() -> None:
    row = data_inventory._schema_row("spotify_track_uri", "string", 5, 10, "spotify:track:x")
    assert row["example"] == REDACTED
    assert row["null_rate"] == 0.5
    kept = data_inventory._schema_row("track_name", "string", 10, 10, "Song")
    assert kept["example"] == "Song"


def test_schema_examples_of_value_sensitive_columns_are_masked() -> None:
    """R-052: `platform` names a device, so a generic profiler must never sample its value.

    This is the second site of R-050 F1. `_schema_row` is shared by `column_schema` and
    `payload_key_schema`, so this one assertion covers every table either is pointed at.
    """
    row = data_inventory._schema_row("platform", "string", 10, 10, "Windows 8 (6.2.9200; x64)")
    assert row["example"] == REDACTED
    # and by payload key name, as `payload_key_schema` supplies it
    assert data_inventory._schema_row("platform", "string", 5, 5, "osx")["example"] == REDACTED


def test_samples_mask_value_sensitive_columns() -> None:
    frame = pd.DataFrame(
        {"platform": ["Windows 8 (6.2.9200; x64)", None], "track_name": ["A", "B"]}
    )
    out = data_inventory.redact_sample(frame)
    assert set(out["platform"].dropna()) == {REDACTED}
    assert out["platform"].isna().tolist() == [False, True]  # nulls stay null
    assert out["track_name"].tolist() == ["A", "B"]
