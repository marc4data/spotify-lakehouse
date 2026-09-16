"""Redaction for every display of profile, user, or follower data (CLAUDE.md §5)."""

from __future__ import annotations

from typing import Any

import pandas as pd

REDACTED = "<redacted>"
SENSITIVE_FIELDS = frozenset(
    {
        "account_id",  # observed on /me 2026-09-13; not in Spotify's documented user object
        "birthdate",
        "display_name",
        "email",
        "external_urls",
        "href",
        "id",
        "images",
        "uri",
    }
)


# Columns whose *values* must never render, as distinct from SENSITIVE_FIELDS above, which names
# columns identifying a person. A generic helper — one that iterates columns and samples, ranges or
# counts each the same way — never meets a rule written for a display somebody chose, so it consults
# this list instead (R-052, from R-050 F1 and R-049 F4).
# Extend THIS list when a new sensitive column appears; the call sites do not change.
SENSITIVE_VALUE_COLUMNS = frozenset(
    {
        "platform",  # names a device model, e.g. a full Windows build string (R-009, R-050 F1)
    }
)


def hides_values(column: Any) -> bool:
    """Whether a column's values must never be rendered — not as an example, a range or a mode."""
    return any(part in SENSITIVE_VALUE_COLUMNS for part in str(column).split("."))


def _is_sensitive(column: str) -> bool:
    # Handles json_normalize-style dotted names such as `external_urls.spotify` or `owner.id`.
    return any(part in SENSITIVE_FIELDS for part in str(column).split("."))


def redact_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with every identifying column masked. Non-null values become `<redacted>`."""
    out = df.copy()
    for column in out.columns:
        if _is_sensitive(column):
            out[column] = out[column].where(out[column].isna(), REDACTED)
    return out


def redact_mapping(value: Any) -> Any:
    """Recursive redaction for dict/list payloads (e.g. a raw /me response)."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if k in SENSITIVE_FIELDS else redact_mapping(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(v) for v in value]
    return value
