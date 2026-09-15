"""Genre-bucket panels of 02_listening_patterns (spot-main-R-015): shares, drill-down hierarchy."""

from __future__ import annotations

import pandas as pd
import pytest

from spotify_lakehouse import listening

BUCKETS = ["rock", "pop", "classical", "other"]


def _allocation(rows: list[tuple[str, str, str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["bucket_name", "genre_name", "artist_name", "allocated_ms", "allocated_plays"],
    )


def test_bucket_shares_are_shares_of_allocated_time_in_fixed_order() -> None:
    """Spec §2: normalise to share of allocated music time, never raw ms; fixed bucket order."""
    light = _allocation([("pop", "pop", "A", 1_000.0, 1.0), ("rock", "rock", "B", 3_000.0, 3.0)])
    heavy = light.assign(allocated_ms=light["allocated_ms"] * 50)
    for frame in (light, heavy):
        shares = listening.bucket_shares(frame, BUCKETS)
        assert shares["bucket"].tolist() == BUCKETS  # empty buckets kept, order never re-sorted
        assert shares["share_pct"].tolist() == pytest.approx([75.0, 25.0, 0.0, 0.0])
    assert listening.bucket_shares(heavy, BUCKETS)["allocated_ms"].iloc[0] == 150_000.0


def test_sunburst_nodes_sum_children_into_parents_and_keep_top_artists() -> None:
    frame = _allocation(
        [
            ("rock", "rock", "A", 3_600_000.0, 1.0),
            ("rock", "rock", "B", 1_800_000.0, 1.0),
            ("rock", "rock", "C", 900_000.0, 1.0),
            ("rock", "blues rock", "A", 3_600_000.0, 1.0),
            ("pop", "pop", "D", 7_200_000.0, 2.0),
        ]
    )
    nodes = listening.sunburst_nodes(frame, "all", top_artists=2)
    assert nodes["id"].is_unique
    by_id = nodes.set_index("id")
    children = nodes[nodes["parent"] != ""].groupby("parent")["value"].sum()
    for parent, total in children.items():
        assert by_id.loc[parent, "value"] == pytest.approx(total)  # branchvalues="total" holds
    assert by_id.loc["all|rock", "value"] == pytest.approx(2.75)  # 1 + 0.5 + 0.25 + 1
    rock_artists = nodes[nodes["parent"] == "all|rock|rock"]["label"].tolist()
    assert rock_artists == ["A", "B", "1 other artist"]
