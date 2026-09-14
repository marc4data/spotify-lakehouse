from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated fake checkout, so the real .session never leaks into a test."""
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setenv("SPOT_REPO_ROOT", str(root))
    return root


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated ~/.config/spot, so tests never read or write real credentials."""
    path = tmp_path / "config-spot"
    path.mkdir(mode=0o700)
    monkeypatch.setenv("SPOT_CONFIG_DIR", str(path))
    return path


def fake_hex(length: int = 32) -> str:
    # Built at runtime so no credential-shaped literal exists in the test source.
    return ("0123456789abcdef" * 8)[:length]
