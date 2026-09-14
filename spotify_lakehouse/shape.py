"""Describe the shape of a JSON payload — key paths and types only, never values."""

from __future__ import annotations

from typing import Any


def describe_shape(value: Any, path: str = "$") -> dict[str, str]:
    """Map each key path to the set of JSON types seen there, e.g. `$.items[].played_at: str`."""
    found: dict[str, set[str]] = {}
    _walk(value, path, found)
    return {p: "|".join(sorted(types)) for p, types in sorted(found.items())}


def _walk(value: Any, path: str, found: dict[str, set[str]]) -> None:
    found.setdefault(path, set()).add(type(value).__name__ if value is not None else "null")
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{path}.{key}", found)
    elif isinstance(value, list):
        for child in value:
            _walk(child, f"{path}[]", found)
