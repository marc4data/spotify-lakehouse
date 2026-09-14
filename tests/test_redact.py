from __future__ import annotations

import pandas as pd

from spotify_lakehouse.redact import REDACTED, redact_mapping, redact_profile
from spotify_lakehouse.shape import describe_shape


def test_redact_profile_masks_identifiers_keeps_analytics_fields() -> None:
    df = pd.DataFrame(
        {
            "email": ["a@example.org"],
            "id": ["user-1"],
            "display_name": ["Someone"],
            "external_urls.spotify": ["https://open.spotify.com/user/user-1"],
            "country": ["US"],
            "followers.total": [3],
        }
    )
    out = redact_profile(df)
    for column in ("email", "id", "display_name", "external_urls.spotify"):
        assert out.loc[0, column] == REDACTED
    assert out.loc[0, "country"] == "US"
    assert out.loc[0, "followers.total"] == 3
    assert df.loc[0, "email"] == "a@example.org"  # input untouched


def test_redact_profile_keeps_nulls_null() -> None:
    out = redact_profile(pd.DataFrame({"email": [None]}))
    assert out["email"].isna().all()


def test_redact_mapping_is_recursive() -> None:
    out = redact_mapping({"owner": {"id": "u", "type": "user"}, "items": [{"uri": "x"}]})
    assert out == {"owner": {"id": REDACTED, "type": "user"}, "items": [{"uri": REDACTED}]}


def test_describe_shape_reports_paths_and_types_never_values() -> None:
    shape = describe_shape({"items": [{"played_at": "2026-01-01", "n": None}], "next": None})
    assert shape["$.items[].played_at"] == "str"
    assert shape["$.items[].n"] == "null"
    assert "2026-01-01" not in repr(shape)
