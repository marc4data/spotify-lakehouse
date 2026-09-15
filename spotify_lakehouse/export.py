"""Load Spotify Extended Streaming History exports into raw.export_record (spot-main-R-003).

Files live outside the repo under ~/spot-data/exports/<profile_slug>/ (linked as data/exports). The
zip Spotify sends is kept as the immutable original and unzipped beside it, never overwritten.

`ip_addr` and `user_agent` are discarded before anything is written (data-contracts §1/§2): raw is
append-only, so a field that reaches it can never be removed. The table's check constraint is the
backstop. Loading is one COPY per file into a temp table, then one insert that skips positions
already present, so a re-run inserts zero rows and an interrupted run resumes at the first
unfinished file.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import psycopg

from spotify_lakehouse.config import ConfigError, repo_root

LOCK_NAME = "spot_export"
DISCARDED_FIELDS: tuple[str, ...] = (
    "ip_addr",
    "ip_addr_decrypted",  # the same fields under the names older extended exports used
    "user_agent",
    "user_agent_decrypted",
)
EXTENDED_FILE = re.compile(r"Streaming_History_(Audio|Video)_[0-9_-]+\.json")
# The 1-year account-data package: a different product, a different schema, no ms-level detail.
ACCOUNT_DATA_FILE = re.compile(r"StreamingHistory(_music|_podcast)?_?[0-9]+\.json")


class ExportError(RuntimeError):
    """The export on disk is not something this loader may load. The message says why."""


def exports_dir() -> Path:
    path = repo_root() / "data" / "exports"
    if not path.is_dir():
        raise ConfigError(
            f"{path} does not exist. Fix: run `make bootstrap` "
            "(it links data/exports -> ~/spot-data/exports)."
        )
    return path


def scrub_record(record: dict[str, Any]) -> dict[str, Any]:
    """A copy of one export record without the contract-discarded fields."""
    clean = copy.deepcopy(record)
    for name in DISCARDED_FIELDS:
        clean.pop(name, None)
    return clean


def extract_archives(profile_dir: Path) -> list[str]:
    """Unzip every archive in profile_dir beside itself. Never overwrites; refuses unsafe paths."""
    extracted: list[str] = []
    root = profile_dir.resolve()
    for archive in sorted(profile_dir.glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                name = PurePosixPath(member.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise ExportError(f"{archive.name}: unsafe member path {member.filename!r}")
                target = (root / name).resolve()
                if not target.is_relative_to(root):
                    raise ExportError(f"{archive.name}: member escapes {root}: {member.filename!r}")
                if member.is_dir() or target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                target.chmod(0o600)
                extracted.append(member.filename)
    return extracted


def discover_files(profile_dir: Path) -> list[Path]:
    """Extended Streaming History JSON files under profile_dir, sorted. Refuses the wrong one."""
    json_files = sorted(p for p in profile_dir.rglob("*.json") if p.is_file())
    extended = [p for p in json_files if EXTENDED_FILE.fullmatch(p.name)]
    account_data = [p for p in json_files if ACCOUNT_DATA_FILE.fullmatch(p.name)]
    if account_data and not extended:
        raise ExportError(
            f"{profile_dir} holds the 1-year account-data package ({account_data[0].name}, ...), "
            "not Extended Streaming History. Nothing loaded. Request 'Extended streaming history' "
            "at https://www.spotify.com/account/privacy/."
        )
    names = [p.name for p in extended]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ExportError(f"{profile_dir}: the same export filename appears twice: {duplicates}")
    return extended


@dataclass
class FileResult:
    source_file: str
    records: int
    inserted: int


@dataclass
class LoadSummary:
    files: list[FileResult] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def records(self) -> int:
        return sum(f.records for f in self.files)

    @property
    def inserted(self) -> int:
        return sum(f.inserted for f in self.files)


def read_records(path: Path) -> list[dict[str, Any]]:
    records = json.loads(path.read_text())
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ExportError(f"{path.name}: expected a JSON array of objects")
    return records


def load_file(conn: psycopg.Connection, profile: str, path: Path) -> FileResult:
    """Load one export file. Positions already in raw.export_record are skipped, never rewritten."""
    records = read_records(path)
    source_file = path.name
    row = conn.execute(
        "select count(*) from raw.export_record where profile_slug = %s and source_file = %s",
        (profile, source_file),
    ).fetchone()
    if row and row[0] == len(records):
        return FileResult(source_file, len(records), 0)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("drop table if exists pg_temp._export_load")
        cur.execute(
            "create temp table _export_load "
            "(profile_slug text, source_file text, record_index int, payload jsonb)"
        )
        with cur.copy(
            "copy _export_load (profile_slug, source_file, record_index, payload) from stdin"
        ) as copy_in:
            for index, record in enumerate(records):
                clean = scrub_record(record)
                copy_in.write_row((profile, source_file, index, json.dumps(clean)))
        cur.execute(
            "insert into raw.export_record (profile_slug, source_file, record_index, payload) "
            "select profile_slug, source_file, record_index, payload from _export_load "
            "on conflict (profile_slug, source_file, record_index) do nothing"
        )
        inserted = cur.rowcount
        cur.execute("drop table pg_temp._export_load")
    return FileResult(source_file, len(records), inserted)


def load_profile(conn: psycopg.Connection, profile: str, profile_dir: Path) -> LoadSummary:
    started = time.monotonic()
    summary = LoadSummary()
    for path in discover_files(profile_dir):
        summary.files.append(load_file(conn, profile, path))
    summary.seconds = time.monotonic() - started
    return summary
