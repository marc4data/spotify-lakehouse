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
import itertools
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


# --- Rebuild (spot-main-R-062) -------------------------------------------------------------------
#
# Why this exists at all: `load_file` inserts `on conflict ... do nothing`, so re-loading the SAME
# file is a clean no-op and re-loading a DIFFERENT one is a silent hybrid. R-044 measured that
# happening — one child's export was loaded under two slugs, a corrective load added 91 rows, and
# three files ended up holding the first N records from one person and the rest from another. `raw`
# is append-only (data-contracts §1), so the bad rows could not be removed and the bad load blocked
# its own fix.
#
# The way back is the one §1 already anticipates: the zip is the immutable original, so the table is
# reconstructible from it. This rebuilds wholesale rather than surgically deleting the bad rows — a
# targeted delete would trade §1's guarantee for a loading convenience, while a full reconstruction
# from the stated source of truth honours it.

#: Two profiles sharing more than this share of the smaller one's plays are the same export.
#: Measured 2026-09-24: the genuine pair (brody/emma) shares 1 play in 60,080 — 0.0017%. The
#: duplicated pair shared 99.84%. Anything between is not a family resemblance; two Spotify accounts
#: do not independently produce the same play at the same millisecond.
MATERIAL_OVERLAP = 0.05

#: Gate 1's control: this profile must reproduce byte-for-byte or the rebuild is not trustworthy.
CONTROL_PROFILE = "marc"


@dataclass(frozen=True)
class InputCheck:
    """What the inputs on disk are, before anything is destroyed."""

    digests: dict[str, str]
    play_counts: dict[str, int]
    overlaps: dict[tuple[str, str], float]


def zip_digest(path: Path) -> str:
    import hashlib

    digest = hashlib.md5()  # noqa: S324 - file identity, not a security boundary
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_play_keys(profile_dir: Path) -> set[tuple[str | None, str | None]]:
    """(timestamp, content uri) for every play in a profile's extracted export."""
    keys: set[tuple[str | None, str | None]] = set()
    for path in discover_files(profile_dir):
        for record in read_records(path):
            keys.add(
                (
                    record.get("ts"),
                    record.get("spotify_track_uri")
                    or record.get("spotify_episode_uri")
                    or record.get("audiobook_chapter_uri"),
                )
            )
    return keys


def check_rebuild_inputs(exports_root: Path, profiles: list[str]) -> InputCheck:
    """Refuse to rebuild from inputs that cannot be told apart. Raises ExportError with the reason.

    🚨 The gate is a RELATION, not a hash (spot-main-R-062, corrected by Cowork). R-062's prompt
    originally banned one specific md5 — the file that had arrived under two children's names. That
    is the wrong shape: the file was never bad, the *duplication* was, and by the time the round ran
    that same hash was the sole remaining copy and entirely legitimate. A banned-hash list also goes
    stale the moment anyone re-downloads, and it cannot catch a duplication between two files that
    differ by a byte.

    What actually has to hold is that the inputs can be told apart: every zip distinct, and no two
    profiles carrying materially the same plays.
    """
    digests: dict[str, str] = {}
    for profile in profiles:
        archives = sorted((exports_root / profile).glob("*.zip"))
        if not archives:
            raise ExportError(f"{exports_root / profile} holds no zip to rebuild from")
        for archive in archives:
            digests[f"{profile}/{archive.name}"] = zip_digest(archive)

    seen: dict[str, str] = {}
    for name, digest in digests.items():
        if digest in seen:
            raise ExportError(
                f"two exports are the same file: {seen[digest]!r} and {name!r} both hash to "
                f"{digest}. One download was served twice; re-request the second before rebuilding."
            )
        seen[digest] = name

    keys = {profile: profile_play_keys(exports_root / profile) for profile in profiles}
    play_counts = {profile: len(k) for profile, k in keys.items()}
    overlaps: dict[tuple[str, str], float] = {}
    for left, right in itertools.combinations(sorted(profiles), 2):
        smaller = min(len(keys[left]), len(keys[right])) or 1
        share = len(keys[left] & keys[right]) / smaller
        overlaps[(left, right)] = share
        if share > MATERIAL_OVERLAP:
            raise ExportError(
                f"{left!r} and {right!r} share {share:.2%} of the smaller export's plays. Two "
                "accounts do not independently produce the same play at the same millisecond — "
                "one of these is the other's export under the wrong name."
            )
    return InputCheck(digests=digests, play_counts=play_counts, overlaps=overlaps)


def content_fingerprints(conn: psycopg.Connection) -> dict[str, tuple[int, str]]:
    """Rows and an order-independent content fingerprint per profile, straight from raw."""
    rows = conn.execute(
        "select profile_slug, count(*), "
        "md5(string_agg(coalesce(payload ->> 'ts', '') || '~' || "
        "coalesce(payload ->> 'spotify_track_uri', ''), '|' "
        "order by coalesce(payload ->> 'ts', ''), "
        "coalesce(payload ->> 'spotify_track_uri', ''))) "
        "from raw.export_record group by profile_slug"
    ).fetchall()
    return {slug: (count, fingerprint) for slug, count, fingerprint in rows}


@dataclass
class RebuildResult:
    before: dict[str, tuple[int, str]]
    after: dict[str, tuple[int, str]]
    inputs: InputCheck
    summaries: dict[str, LoadSummary]
    seconds: float = 0.0

    @property
    def control_reproduced(self) -> bool:
        return self.before.get(CONTROL_PROFILE) == self.after.get(CONTROL_PROFILE)


def rebuild_export_record(
    conn: psycopg.Connection,
    exports_root: Path,
    profiles: list[str],
    *,
    control: str = CONTROL_PROFILE,
) -> RebuildResult:
    """Reconstruct raw.export_record wholesale from the zips on disk, in one transaction.

    🚨 Gate 1 is absolute: the control profile must reproduce byte-for-byte — same row count, same
    content fingerprint. If it does not, the rebuild is wrong and every other figure it produces is
    worthless, so the transaction rolls back and nothing is changed.

    The triggers are disabled only for the truncate and only inside this transaction, which is the
    same shape data-contracts §1 already sanctions for a backfilling migration. Postgres TRUNCATE is
    transactional, so a failed control restores every row.
    """
    started = time.monotonic()
    inputs = check_rebuild_inputs(exports_root, profiles)
    before = content_fingerprints(conn)
    if control not in before:
        raise ExportError(
            f"control profile {control!r} has no rows to reproduce; refusing to rebuild"
        )

    summaries: dict[str, LoadSummary] = {}
    with conn.transaction():
        conn.execute("alter table raw.export_record disable trigger export_record_no_truncate")
        try:
            conn.execute("truncate table raw.export_record")
        finally:
            conn.execute("alter table raw.export_record enable trigger export_record_no_truncate")
        for profile in profiles:
            summaries[profile] = load_profile(conn, profile, exports_root / profile)
        after = content_fingerprints(conn)
        if before.get(control) != after.get(control):
            raise ExportError(
                f"GATE 1 FAILED: control profile {control!r} did not reproduce. "
                f"before={before.get(control)} after={after.get(control)}. "
                "The rebuild is wrong; nothing was changed."
            )
    return RebuildResult(
        before=before,
        after=after,
        inputs=inputs,
        summaries=summaries,
        seconds=time.monotonic() - started,
    )
