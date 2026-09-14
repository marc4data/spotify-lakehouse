"""Redaction for every display of profile, user, or follower data (CLAUDE.md §5)."""

from __future__ import annotations

from typing import Any

import pandas as pd

REDACTED = "<redacted>"
SENSITIVE_FIELDS = frozenset(
    {"email", "id", "uri", "href", "external_urls", "display_name", "images", "birthdate"}
)


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
