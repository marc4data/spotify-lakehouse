"""The CLI's structlog setup must follow the current sys.stderr (spot-main-R-033)."""

from __future__ import annotations

import io
import sys

import pytest
import structlog

from spotify_lakehouse import cli


def test_logging_writes_to_the_stderr_current_at_log_time(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_with = io.StringIO()
    monkeypatch.setattr(sys, "stderr", configured_with)
    cli._configure_logging()
    configured_with.close()  # e.g. a capture stream that has since been torn down

    current = io.StringIO()
    monkeypatch.setattr(sys, "stderr", current)
    structlog.get_logger("spot.test").info("logged_after_stderr_changed")

    assert "logged_after_stderr_changed" in current.getvalue()
