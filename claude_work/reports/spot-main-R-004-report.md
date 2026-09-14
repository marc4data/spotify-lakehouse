# spot-main-R-004 — Build report: the star schema, API path (rebuilt, genres removed)

**Session:** `main` · **Date:** 2026-09-14 · **Supersedes** the stopped round's report (preserved at `c8f0a65`).

**Status for Cowork:** built. Migration `0002` applied under the §1 exception with matching checksums, 45 artist
responses filed, 19 models built (4 staging, 6 intermediate, 9 marts), **`make dbt-build` PASS=114 / WARN=0 / ERROR=0**, both
required breaks go red on the named tests, and everything restores clean. No contract was amended. One security
defect was found and fixed along the way (§9, Marc has a one-line action), and six smaller findings are in §10.

---

## 1. Cowork's decisions, as implemented

| Report § / decision | Implemented as | Where |
|---|---|---|
| Genres absent → **Option A**, `dim_artist` SCD1 + `genre_source` | `dim_artist` Type 1, `genre_source` NULL on all 45; no `dim_genre`, `dim_genre_bucket`, `br_artist_genre` | `dbt/models/marts/dim_artist.sql` |
| Raw mismatch → **one-time backfill per §1 exception** | Migration `0002`, in-transaction sha256 guard, checksums in §2 | `migrations/0002_raw_api_response_feed_and_profile_slug.sql` |
| Re-file the "49" artist responses | **45** filed. The stopped round's 49 calls were 45 distinct artists + 1 duplicate shape check + 1 refused batch + top artists + followed artists (§10 C1) | `spot extract-artists`, `spotify_lakehouse/artists.py` |
| Red test → **both** | Unit test with fixed fixture, and live break on `fct_play_event_grain` (§6) | `int_play_events__deduped.yml`, `dbt/tests/fct_play_event_grain.sql` |
| Profile registry **not in the repo** | `~/.config/spot/profiles.csv` (600) → `spot sync-profiles` → `spot_meta.profile_registry` (migration `0003`); repo ships `profiles.csv.example` only; bootstrap creates and syncs; `scripts/dbt.sh` syncs before `build`/`run`/`test`/`seed` | `spotify_lakehouse/profiles.py` |
| `other` > 15% test → **ship disabled** | `{{ config(enabled=false) }}` with the R-024 note and the outline of the query | `dbt/tests/genre_other_share_under_15_pct.sql` |
| "~100 play events" → **50 plays** | Every count below uses 50 | — |
| `spotify-wta` → assume single checkout | Confirmed: `git worktree list` shows only `main`. Its schemas `stg_wta` / `mart_wta` remain (§10 C5) | — |

---

## 2. Migration `0002` — the §1 exception, with proof

**Checksum expression** (identical before and after, over every pre-existing column except the renamed one):
`sha256( string_agg(id | payload | source_file | ingested_at, '\n' order by id) )`

| | Rows | sha256 |
|---|---|---|
| **Before** — taken read-only before `0002` existed | 4 | `77ff518bab73d561425d75ad27bcf1ccc69096142ddee67d90f976c089196c83` |
| **After** — same expression, after `0002` committed | 4 | `77ff518bab73d561425d75ad27bcf1ccc69096142ddee67d90f976c089196c83` |

**Identical.** Independently, the migration itself computes a before/after checksum over the same columns **plus
`profile_slug`** inside its own transaction and raises — rolling the whole migration back — if they differ or if any
pre-existing row has no mappable `feed`. It committed, so that guard passed too.

What `0002` does, in order, in one transaction: rename `profile_key` → `profile_slug` (metadata only) · add `feed
text` · disable `api_response_no_update_delete` · set `feed` from the R-002 `source_file` layout (`api/me/…` → `me`,
`api/me_player_recently-played/…` → `recently_played`) · re-enable the trigger · verify checksum and zero unmapped ·
`feed NOT NULL` · `CHECK (feed ~ '^[a-z][a-z0-9_]*$')` · index `(feed, ingested_at)`.

**After state, verified from the catalog:** columns `id, payload, source_file, profile_slug, ingested_at, feed`, all
`NOT NULL`; constraints `api_response_pkey`, `api_response_feed_format`; triggers `api_response_no_update_delete` and
`api_response_no_truncate` both enabled (`O`); rows 5–8 carry `me, recently_played, me, recently_played`.

**How it was applied — say it plainly:** `0002` and `0003` were first applied by the **pytest integration fixture**
(`tests/test_migrate.py::conn` calls `migrate.apply`), not by `make migrate`. A ruff failure stopped the command chain
before `spot migrate`, and `pytest` ran after it. The in-transaction guard ran regardless, and the external before/after
checksums above bracket it. It is a process finding, not a data one — §10 C2.

---

## 3. What was built

| Layer | Model | Rows | Notes |
|---|---|---|---|
| raw | `raw.api_response` | 49 | 2 `me` · 2 `recently_played` · **45 `artist`** (new) |
| staging | `stg_spotify__me` | 2 | `account_id`, `email` never selected |
| | `stg_spotify__recently_played` | 100 | item grain; not deduplicated by design |
| | `stg_spotify__artist` | 45 | `has_genres_key` per observation |
| | `stg_spot_meta__profile_registry` | 1 | from `spot_meta`, not `raw` |
| intermediate | `int_play_events__unioned` | 100 | API branch only; export (R-003) is one more union branch |
| | `int_play_events__deduped` | **50** | §2 natural key, export > api, earliest capture wins |
| | `int_tracks__latest`, `int_albums__latest`, `int_content_artists`, `int_artists__latest` | 43 / 38 / 58 / 45 | SCD1 sources |
| | **`int_allocation_reconciliation`** | 4 | one row per (profile, step); §7 |
| marts | `fct_play_event` | **50** | all §2 columns; `ms_played` NULL ×50; stable md5-derived bigint key |
| | `dim_profile` | 1 + unknown | registry ⟕ latest `/me` |
| | `dim_content` | 43 + `-1`, `-2` | `content_match_key` (§5 tier 3) included |
| | `dim_track_detail` | 43 | ISRC 43/43 (§5 tier 2) |
| | `dim_album` | 38 + unknown | `release_date` text; precision `day`=35, `year`=3 |
| | `dim_artist` | 45 + unknown | SCD1, `genre_source` NULL |
| | `br_content_artist` | 58 | exactly one primary per track (verified) |
| | `dim_date` | 7,283 + unknown | 2008-10-07 → build date + 2 years |
| | `dim_time_of_day` | 1,440 + unknown | key `hour*100+minute`, §3 dayparts |

Targets: `stg_main` / `mart_main` only. **`analytics` does not exist.** dbt ran exclusively through `make dbt-*` /
`scripts/dbt.sh`.

**The fact table is ready for the export without a migration:** `source_system`, nullable `ms_played`,
`is_ms_played_imputed`, every export-only column, and the export-beats-api precedence all exist now and are exercised by
the unit test's export row (§6.1).

---

## 4. Tests — 114 nodes, all green

| Kind | What | Count |
|---|---|---|
| **Required 1 — grain** | `dbt/tests/fct_play_event_grain.sql` | 1 |
| **Required 2 — no orphans** | `dbt/tests/star_no_orphan_keys.sql` (8 FKs in one query, incl. `-1`/`-2`) **plus** `relationships` on every fact/bridge/subtype FK | 1 + 8 |
| **Required 3 — reconciliation** | `allocation_reconciles.sql` (each step = next step + its residual, counts and ms) · `allocation_reconciliation_matches_fact.sql` (step 1 = fact rows per profile) | 2 |
| Unit test | `dedupe_collapses_same_minute_and_prefers_export` | 1 |
| Registry | `every_played_profile_is_registered.sql` — a played profile with no timezone fails loudly instead of landing on `-1` | 1 |
| Generic | unique / not_null / accepted_values / composite uniqueness (dependency-free macro) | remainder |
| Disabled | `genre_other_share_under_15_pct` (R-024) | 0 run |

Python: **92 passed** — new `test_profiles.py` (registry validation: slug, role, IANA zone, duplicates, header, missing
file, header-only file), `test_artists.py`, `feed_for_endpoint` and safe file keys in `test_raw_store.py`, and DB tests
for `feed NOT NULL`, the `feed` format `CHECK`, and the registry role `CHECK`, each in a rolled-back transaction.

---

## 5. The four measurements, re-taken against built models

| Measurement | Result | Source |
|---|---|---|
| **Minute-collapse rate** | **0 genuine collapses.** 100 candidate rows → 50 natural keys; all 50 keys had 2 rows, and **all 50 pairs share the exact same `ended_at_utc`** (the two probe windows were identical). Keys with more than one *distinct* timestamp: **0**. Distinct plays lost to minute truncation: **0**. | `int_play_events__unioned` |
| **Content resolution** | **0 of 43** real `dim_content` rows have `is_resolved = false`. All `track`. | `dim_content` |
| **Artists with zero genres** | **45 of 45** — `genre_source` NULL on every real row; 45 stored responses, **0** with a `genres` key; 0 artists referenced but not fetched. | `dim_artist`, `stg_spotify__artist` |
| **Multi-artist share** | **Per play: 13 of 50 = 26.0%** (1×37, 2×10, 3×1, 4×2) — confirms the contract. **Per distinct track: 10 of 43 = 23.3%** (1×33, 2×7, 3×1, 4×2). | `br_content_artist` ⋈ `fct_play_event` |

Other facts from the built star, each a contract check:

| Check | Result |
|---|---|
| Unknown members used by `fct_play_event` | `profile_key=-1`: 0 · `content_key<0`: 0 · `date_key=-1`: 0 · `time_of_day_key=-1`: 0 |
| `ms_played` NULL on API rows (§2) | 50 of 50 · `is_ms_played_imputed = true`: 0 |
| **Local date ≠ UTC date** (§2 `date_key` rule) | **48 of 50 plays** fall on a different calendar date in `America/Los_Angeles` than in UTC. All 50 land on one local date; dayparts evening 47, night 1, afternoon 2. The "not from UTC" rule is not an edge case here — it moves 96% of this sample. |

---

## 6. Red tests — both breaks, named tests, output

### 6.1 Break A — remove the minute truncation from the dedupe key

```diff
<             partition by profile_slug, content_uri, date_trunc('minute', ended_at_utc)
>             partition by profile_slug, content_uri, ended_at_utc
```

`scripts/dbt.sh test --select int_play_events__deduped`:

```
1 of 2 PASS dbt_utils_free_unique_combination_int_play_events__deduped_profile_slug__content_uri__date_trunc_minute_ended_at_utc_
2 of 2 FAIL 1 int_play_events__deduped::dedupe_collapses_same_minute_and_prefers_export  [FAIL 1]
[ERROR]: in unit_test dedupe_collapses_same_minute_and_prefers_export (models/intermediate/int_play_events__deduped.yml)
actual differs from expected:
Done. PASS=1 WARN=0 ERROR=1 SKIP=0 NO-OP=0 REUSED=0 TOTAL=2
```

**Red: `dedupe_collapses_same_minute_and_prefers_export`.** The fixture holds two distinct plays of one track 30 s apart
in one minute, an exact re-capture, a same-minute different track, a next-minute play, and an api/export pair in one
minute where the api row was ingested first.

**The same break against live data, unit tests excluded** (`build --select int_play_events__deduped+
--exclude-resource-type unit_test`):

```
11 of 24 PASS fct_play_event_grain ............................................. [PASS in 0.04s]
Done. PASS=24 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=24
```

**Green.** This is the measurement that justifies the unit test: on this data the grain test *cannot* see the minute
truncation removed, because no two distinct plays share a minute (§5). Without the fixture, that part of §2 would be
unguarded.

### 6.2 Break B — remove the dedupe filter entirely

```diff
< where natural_key_rank = 1
```

First attempt, `build --select int_play_events__deduped+`: the build stopped one layer early, at the intermediate
uniqueness test — `FAIL 50 dbt_utils_free_unique_combination_int_play_events__deduped_… Got 50 results` — and skipped the
22 downstream nodes, so `fct_play_event_grain` never ran. To show the *named* test failing, models were run without tests
(`run --select int_play_events__deduped+`, `fct_play_event` = **100 rows**), then the fact's tests:

```
5 of 18 FAIL 50 fct_play_event_grain ........................................... [FAIL 50 in 0.04s]
18 of 18 FAIL 50 unique_fct_play_event_play_event_key .......................... [FAIL 50 in 0.02s]
  Got 50 results, configured to fail if != 0
  Got 50 results, configured to fail if != 0
Done. PASS=16 WARN=0 ERROR=2 SKIP=0 NO-OP=0 REUSED=0 TOTAL=18
```

**Red: `fct_play_event_grain` with 50 duplicate keys**, and `unique_fct_play_event_play_event_key` with 50. First three
violating keys, keys only: `(1, 1, 2026-09-13 23:55 UTC) ×2`, `(1, 2, 23:59) ×2`, `(1, 3, 2026-09-14 00:00) ×2`.
`star_no_orphan_keys`, `allocation_reconciles` and `allocation_reconciliation_matches_fact` stayed green — correctly:
duplicated rows still resolve their keys, and both sides of the reconciliation doubled together.

### 6.3 Restore

Both reverts verified byte-identical against a backup (`cmp`). Full `make dbt-build` after each: **PASS=114, WARN=0,
ERROR=0**; `fct_play_event` back to 50 rows.

---

## 7. `int_allocation_reconciliation` — as built

| profile | step | play_count | ms_played | pct_rows_with_duration | residual | residual plays |
|---|---|---|---|---|---|---|
| marc | 1 total_listening | 50 | NULL | 0.00 | — | 0 |
| marc | 2 music | 50 | NULL | 0.00 | not_music (episodes and unknown content) | 0 |
| marc | 3 artist_resolved | 50 | NULL | 0.00 | primary_artist_unresolved | 0 |
| marc | 4 genre_allocated | **0** | NULL | — | **unclassified (no genre source until spot-main-R-024)** | **50** |

The chain ends at artist-resolved, and 100% of artist-resolved music is named `unclassified` — the visible answer the
prompt asked for. `ms_played` is NULL at every step because every row is API-sourced; `pct_rows_with_duration = 0.00`
says so rather than a zero that looks like a measurement.

---

## 8. Files changed

| Area | Files |
|---|---|
| Migrations | `migrations/0002_raw_api_response_feed_and_profile_slug.sql` (new) · `migrations/0003_spot_meta_profile_registry.sql` (new) |
| Python | `spotify_lakehouse/raw_store.py` (`feed`, `profile_slug`, `feed_for_endpoint`, per-feed dirs, safe file key) · `spotify_lakehouse/cli.py` (`extract-artists`, `sync-profiles`; probe writes `feed`) · `spotify_lakehouse/artists.py` (new) · `spotify_lakehouse/profiles.py` (new) |
| Tests | `tests/test_migrate.py` · `tests/test_raw_store.py` · `tests/test_artists.py` (new) · `tests/test_profiles.py` (new) |
| dbt | `models/staging/` 4 models + `_sources.yml` · `models/intermediate/` 6 models · `models/marts/` 9 models — each `.sql` with its own `.yml` · `tests/` 6 singular tests (1 disabled) · `macros/test_dbt_utils_free_unique_combination.sql` |
| Ops | `scripts/dbt.sh` (no longer sources `.env` — §9; syncs registry before build) · `scripts/bootstrap.sh` (registry step) · `Makefile` (`dbt-build`, `dbt-test`) · `profiles.csv.example` (new) |
| Outside the repo | `~/.config/spot/profiles.csv` (600, one row) · 45 files under `~/spot-data/raw/api/artist/marc/` |

**Contracts touched:** none. **Nothing under `data/` is tracked.**

---

## 9. Security defect found and fixed: `scripts/dbt.sh` executed the credentials file

`scripts/dbt.sh` (R-002) loaded Postgres credentials with `set -a; source ~/.config/spot/.env`. That treats the
credentials file as **shell code**. Line 17 of Marc's file, `SPOTIFY_CLIENT_ID`, has whitespace after the `=`, so bash
ran the value as a command and **printed the Client ID into this session's terminal output** (`line 17: <value>: command
not found`). `set -e` stopped the script at that line, so the Client Secret on line 18 was not reached. Nothing reached a
file, a commit, CI or the repo. Python's dotenv trims the whitespace, which is why every `spot` command worked and the
defect stayed hidden.

**Fix, in this round:** `dbt.sh` now reads only `SPOT_PG_USER`, `SPOT_PG_PASSWORD`, `SPOT_PG_HOST`, `SPOT_PG_PORT` as
plain text with `sed` — nothing in the file is ever executed — and **Spotify credentials are no longer exported to dbt
at all**. A key-names-only scan of the file found line 17 as the only line with stray whitespace.

**Marc's action:** remove the space after `SPOTIFY_CLIENT_ID=` in `~/.config/spot/.env`. A Client ID identifies the
app and is not the secret, but it now appears in one session transcript; regenerating the Client Secret is not needed
because it was not printed.

---

## 10. Findings — including where the prompt was wrong

| # | Finding | Proposal |
|---|---|---|
| **C1** | The prompt says "re-file the **49** artist responses". The stopped round made 49 calls, but only **45** were distinct artist responses; the rest were a duplicate shape check, a 403 batch, `/me/top/artists` and `/me/following`. | None needed — 45 filed. Noted so the register does not carry 49. |
| **C2** | The pytest DB fixture applies migrations to the **shared** database. That is how `0002` first landed (§2). It is idempotent and the guard held, but a test run should not be the thing that changes shared schema. | Next round that touches tests: have the fixture assert migrations are current and **skip** otherwise, leaving `make migrate` as the only writer. |
| **C3** | The unknown members use values outside their column's contract domain: `content_type = 'unknown'` (contract: `track` \| `episode`), `profile_slug = '(unknown)'`, `daypart = 'unknown'`. Kimball-standard, and tested as accepted values, but not what §3 enumerates. | Confirm, or name the values Cowork wants for unknown members. |
| **C4** | `dim_profile.has_api_access` is derived as "a `/me` response exists", and `api_coverage_start` as "first `recently_played` ingestion". §3 says "occupies one of the 5 dev-mode slots" and "first poller row" — close, but the poller (R-017) does not exist yet. | Confirm, or redefine when R-017 lands. |
| **C5** | The `spotify-wta` worktree has been removed (`git worktree list` shows only `main`), but its schemas **`stg_wta` and `mart_wta` still exist** in the shared database. They are empty leftovers from R-002's bootstrap and nothing reads them. (An earlier check in this session, before the removal, still listed the worktree.) | Drop them when convenient: `drop schema stg_wta cascade; drop schema mart_wta cascade;` — Marc's call, since it is a destructive statement on the shared database. |
| **C6** | **Claude Code's own error, corrected.** A first pre-commit dry run reported the custom hook as "(no files to check)". The cause was not pre-commit: zsh does not word-split an unquoted `$VAR`, so all 63 paths reached every scanner as **one** nonexistent filename. Re-run with each path as its own argument (`xargs -0`): `check_secrets.py` exit 0; pinned gitleaks 8.30.0 with `.gitleaks.toml` over all 63 files, `no leaks found`; `pre-commit run --files` exit 0 with every hook Passed — including on untracked files. | None for the repo. For sessions: never pass a file list through an unquoted variable in zsh; a scanner that "checked no files" is a failed check, not a pass. |

---

## 11. Definition of done (`CLAUDE.md` §7)

| Item | State |
|---|---|
| `uv run pytest` | ✅ 92 passed (DB tests ran against live Postgres) |
| `uv run dbt build` against `stg_main`/`mart_main` | ✅ PASS=114 WARN=0 ERROR=0, via `make dbt-build`; zero deprecation warnings |
| `ruff check` / `ruff format --check` | ✅ |
| Pre-commit on staged files, gitleaks included | ✅ dry run over all 63 round files, every hook Passed (§10 C6); and again at commit |
| **A guard proven to fail** | ✅ `dedupe_collapses_same_minute_and_prefers_export` (§6.1) · `fct_play_event_grain` FAIL 50 (§6.2) |
| Build report | this file |
| CI-equivalent `dbt parse` (template profile, `SPOT_SESSION=ci`, no DB) | ✅ no errors or warnings |

---

**Start 2026-09-14 12:18 PM / End 12:40 PM : 21:35**
