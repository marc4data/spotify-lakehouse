# spot-main-R-017 — Build report: the poller

**Session:** `main` · **Date:** 2026-09-14 · **Status:** built, installed, **seen firing**. No contract amended.

`spot refresh` polls recently-played once per API-enabled profile, persists the response like the probe, and records
an overflow gap whenever the window starts after the newest play already captured. The launchd agent
`local.spot.refresh` is installed from `main`. It was **loaded, fired by launchd, and logged a real poll with exit 0**
(§4). All three required tests exist, and the gap guard was broken on purpose and went red (§5). R-004 C2 / R-027 is
closed and proven (§6).

---

## 1. Cowork's decisions, as implemented

| Decision | Implemented as |
|---|---|
| Every 30 minutes | `StartInterval` 1800; `launchctl print` reports `run interval = 1800 seconds` |
| No backfill; no `--follow-next` in the poller | One `GET /me/player/recently-played?limit=50` per profile, per poll. The poller never follows `next` |
| Every profile in the registry "with `has_api_access`" | **Registry profiles that have a stored refresh token.** The registry has no such column; see §7 F1 |
| Install once, from `main` | `make launchd-install` refuses unless `.session` is `main`; the plist is rendered from `launchd/local.spot.refresh.plist.template` into `~/Library/LaunchAgents/` |
| Existing `spot_refresh` lock; lock held → exit 0 quietly | `db.try_refresh_lock` (non-blocking). When held, the poll prints one line, records nothing and exits 0 (§5.2, §7 F5) |

---

## 2. What was built

| What | Location |
|---|---|
| Poller schema | `migrations/0004_spot_meta_poller.sql`: `spot_meta.poll_run` (one row per profile per poll, `ok` or `error`, window bounds, previous high-water mark, raw id; `CHECK` ok ⇔ raw id present) and `spot_meta.overflow_gap` (`gap_start` = previous high-water mark, `gap_end` = window's oldest play, `items_returned`, `CHECK gap_end > gap_start`) |
| Poll logic | `spotify_lakehouse/poller.py`: `window_of`, **`detect_gap`**, `high_water_mark`, `record_poll`, `record_poll_error`, `poll_profile`, `profile_status`, `launchd_state` |
| CLI | `spot refresh` and `spot refresh --status` in `spotify_lakehouse/cli.py`. Per-profile failures are recorded as `status='error'` and exit 1; the other profiles still poll |
| Supporting | `db.try_refresh_lock` · `auth.stored_profiles` · `migrate.pending` (read-only) |
| Agent | `launchd/local.spot.refresh.plist.template` (`RunAtLoad`, `StartInterval` 1800, `ProcessType` Background) · `scripts/refresh_agent.sh` (launchd entry point: rotates `refresh.log` at 1 MB keeping 5, writes firing/exit lines, runs `uv run --frozen spot refresh`) · `scripts/launchd.sh` (install/uninstall) · `Makefile`: `launchd-install`, `launchd-uninstall`, `refresh-status` |
| Tests | `tests/test_poller.py` · `tests/test_poller_db.py` · `tests/test_follow_next.py` (R-018 C4) · `tests/conftest.py` `db` fixture · `tests/test_migrate.py` rewritten on it · dbt unit test `identical_window_polled_twice_adds_no_plays` |

**The high-water mark** is the newest `played_at` in **any** recently-played capture for the profile (probe or poll),
read before the poll inserts its own row. The very first poll therefore compared against R-018's probe rather than
starting blind (§7 F2).

**Outside the repo:** `~/Library/LaunchAgents/local.spot.refresh.plist` (644, `plutil -lint` OK) · `~/.config/spot/logs/`
(700) with `refresh.log` (600).

---

## 3. Baseline, taken before any poll

| | Value |
|---|---|
| max `raw.api_response.id` | 69 |
| `marc` high-water mark | 2026-09-14T19:25:08.803Z (R-018 probe, page 1) |
| `spot_meta.poll_run` / `overflow_gap` rows | 0 / 0 |
| `mart_main.fct_play_event` rows | 64 |
| registry | `marc` |

---

## 4. Live verification

### 4.1 Two manual polls: required test 3, end to end

```
2026-09-14T20:42:45Z spot refresh refresh-main-5896a997 marc: items=50 window 2026-09-14T00:43:58.287Z .. 2026-09-14T19:25:08.803Z high-water-before=2026-09-14T19:25:08.803Z raw_id=87 poll_id=5: no gap
  make dbt-build → Done. PASS=115 WARN=0 ERROR=0 · fct_play_event = 64 · raw rows = 53
2026-09-14T20:42:52Z spot refresh refresh-main-449bc093 marc: items=50 window 2026-09-14T00:43:58.287Z .. 2026-09-14T19:25:08.803Z high-water-before=2026-09-14T19:25:08.803Z raw_id=88 poll_id=6: no gap
  make dbt-build → Done. PASS=115 WARN=0 ERROR=0 · fct_play_event = 64 · raw rows = 54   (delta fct 0, raw +1)
  plays in poll 2 not in poll 1: 0
```

**Two consecutive polls over the same unchanged window added one raw row each and zero `fct_play_event` rows.**
Scope: the window did not change between the polls (0 new plays), so this measures the unchanged-window case the prompt
specified. The changed-window case, where a re-captured play must still not duplicate, is covered by the dbt unit test
`identical_window_polled_twice_adds_no_plays` and R-004's `dedupe_collapses_same_minute_and_prefers_export`.

### 4.2 The agent: installed, loaded, fired, logged

`make launchd-install` at 1:42:59 PM:

```
Installed ~/Library/LaunchAgents/local.spot.refresh.plist and loaded gui/501/local.spot.refresh.
It fires now (RunAtLoad), then every 30 minutes. Log: ~/.config/spot/logs/refresh.log
```

`~/.config/spot/logs/refresh.log`, written by the launchd firing, not by a manual command:

```
2026-09-14T20:43:00Z agent firing (pid 66540)
2026-09-14T20:43:01Z spot refresh refresh-main-8007683f marc: items=50 window 2026-09-14T00:43:58.287Z .. 2026-09-14T19:25:08.803Z high-water-before=2026-09-14T19:25:08.803Z raw_id=100 poll_id=11: no gap
2026-09-14T20:43:02Z agent exit=0
```

`launchd.err.log` and `launchd.out.log`: empty. `launchctl print gui/501/local.spot.refresh`:

```
path = ~/Library/LaunchAgents/local.spot.refresh.plist
state = not running
program = /bin/bash
runs = 1
last exit code = 0
run interval = 1800 seconds
```

`spot_meta.poll_run` holds the agent's own row: **id 11, run `refresh-main-8007683f`, `ok`, 50 items**, finished
2026-09-14T20:43:02.577Z. `spot refresh --status` immediately after:

```
spot refresh --status  as of 2026-09-14T20:43:04.619Z
launchd agent local.spot.refresh: loaded (state not running, runs 1, last exit code 0)
  marc: token yes; last successful poll 2026-09-14T20:43:02.577Z (0 min ago); last poll status ok; polls in 24 h 3 (errors 0); high-water mark 2026-09-14T19:25:08.803Z; overflow gaps recorded 0
```

**What was verified and what was not:**

| Verified by observation | Not yet observed |
|---|---|
| Plist rendered and lint-clean; agent loaded in `gui/501` | The first **interval** firing (due ~2:13 PM). Only the load-time (`RunAtLoad`) firing has been seen |
| A launchd-initiated firing ran the real poll: log lines, `poll_run` row 11, exit 0, `runs = 1` | Behaviour across Mac sleep, Docker Desktop stopped, or no network. Untested (§7 F4) |
| `--status` answers "is it running, has it missed anything" in one command | A real overflow gap. None has occurred (§7 F3) |

### 4.3 Log rotation, exercised without Spotify

`scripts/refresh_agent.sh` was run 7 times into a scratch directory with `SPOT_LOG_MAX_BYTES=1` and
`SPOT_UV=/usr/bin/true` (no API call):

```
refresh.log  refresh.log.1  refresh.log.2  refresh.log.3  refresh.log.4  refresh.log.5      (no .6)
dir mode drwx------  log mode -rw-------
```

---

## 5. Required tests, and the red test

### 5.1 Required test 1: overflow detection (the guard)

| Test | Asserts |
|---|---|
| `test_poller.py::test_gap_recorded_when_window_starts_after_the_high_water_mark` | previous mark 07:00, window 08:00–09:00 → `Gap(07:00, 08:00, 2)` |
| `test_poller.py::test_no_gap_when_window_overlaps_the_high_water_mark` | mark 08:30 inside window → no gap |
| `test_poller.py::test_no_gap_when_oldest_play_is_the_high_water_mark_itself` | equal timestamps overlap → no gap |
| `test_poller.py::test_no_gap_without_an_earlier_capture_or_with_an_empty_window` | nothing to compare → no gap |
| `test_poller_db.py::test_high_water_mark_and_gap_rows` | against Postgres, rolled back: mark read from raw, `poll_run` + `overflow_gap` rows written with the right bounds, `--status` counts it |
| `test_poller_db.py::test_overlapping_poll_records_no_gap_row` | overlap → `poll_run` row, no `overflow_gap` row |

**Break:** `detect_gap` never reports a gap.

```diff
<     if window.oldest > previous_high_water_mark:
>     if False:  # BREAK (R-017 red test)
```

```
FAILED tests/test_poller.py::test_gap_recorded_when_window_starts_after_the_high_water_mark
FAILED tests/test_poller_db.py::test_high_water_mark_and_gap_rows - Assertion...
2 failed, 8 passed
```

**Red: `test_gap_recorded_when_window_starts_after_the_high_water_mark`** and `test_high_water_mark_and_gap_rows`. The
overlap tests stayed green, correctly. Reverted, byte-identical by `cmp`; full suite **127 passed**.

### 5.2 Required test 2: lock contention exits 0

`test_poller_db.py::test_refresh_exits_zero_and_records_nothing_while_the_lock_is_held`: a second connection holds
`pg_advisory_lock(hashtext('spot_refresh'))`; `cli.main(["refresh"])` returns **0**, `poll_run` count is unchanged, and
the output says `lock is held elsewhere; skipped`. The lock is taken before settings, profiles or any client, so a
contended poll cannot reach Spotify.

### 5.3 Required test 3: idempotence

End to end live (§4.1: fct delta 0, raw +1), plus dbt unit test `identical_window_polled_twice_adds_no_plays`
(6 captured items across two polls → 3 plays, each keeping the first poll's lineage).

Also: `test_status_makes_no_api_call_and_exits_zero`. `--status` fails the test if it constructs an API client.

---

## 6. "Also in this round"

| Item | Result |
|---|---|
| **R-018 C4:** `MockTransport` test of `_follow_next` | `tests/test_follow_next.py`: follows `next` until null, persisting exactly `run1_page2.json` and `run1_page3.json`; stops at the requested count; **a foreign `next` link raises `UnsafeNextLink` before any request is made** (0 requests recorded) |
| **R-004 C2 / R-027:** the test fixture must stop applying migrations | The `db` fixture calls `migrate.pending()` (read-only) and **skips** on anything unapplied. **Proven before `make migrate`:** all 9 DB tests skipped with `migrations pending (0004_spot_meta_poller.sql): run make migrate`, and afterwards `0004` was still absent (`schema_migrations` version 4: 0 rows; `spot_meta.poll_run` did not exist). `make migrate` then applied it and the same tests ran. New `test_pending_lists_unapplied_without_applying_them` checks that `pending()` writes nothing. **R-027 can close** |
| **Drop `stg_wta`, `mart_wta`** | Verified first: 0 relations, 0 functions or types, 0 dependent objects elsewhere. Dropped. Remaining schemas: `mart_main, public, raw, spot_meta, stg_main`; `fct_play_event` unaffected (64) |

---

## 7. Findings: where the prompt was ambiguous, and what is not yet proven

| # | Finding | Proposal |
|---|---|---|
| **F1** | "Every profile in `spot_meta.profile_registry` with `has_api_access`." **The registry has no `has_api_access` column.** It exists only on the `dim_profile` mart, derived (R-004 C4) as "a `/me` response exists", and the extractor should not read a dbt mart. Implemented: registry profiles **with a stored refresh token**, the fact API access rests on. Registry profiles without one are logged and skipped. | Accept. And since `data-contracts.md` §3 says to redefine `has_api_access` and `api_coverage_start` "when the poller lands": propose `has_api_access` = stored token **and** at least one `ok` `poll_run`; `api_coverage_start` = first `ok` `poll_run.finished_at` per profile. `dim_profile` is unchanged pending Cowork's call. |
| **F2** | "The newest `played_at` it saw last time" is implemented as the newest across **all** recently-played captures, not only previous polls. So the first poll compared against the R-018 probe (19:25:08.803Z) instead of recording nothing. | Confirm. |
| **F3** | **An overflow gap is evidence of possible loss, not proof of it.** If exactly 50 new plays happened since the mark, the window's oldest can be the first play after the mark with nothing lost, and two timestamps cannot distinguish that. `items_returned` is recorded so the gap can be judged: a gap with fewer than 50 items cannot be an overflow. **No real gap has occurred yet**; detection has been exercised only by tests and the break. | None. Worth one sentence in R-009's notebook when gaps are shown. |
| **F4** | Not yet observed: the first interval firing (due about 2:13 PM), and behaviour when the Mac sleeps, Docker Desktop is stopped, or the network is down. If Postgres is unreachable the wrapper logs the error and exits non-zero, but **nothing reaches `poll_run`**, because the database is what's missing. `--status` flags a profile `STALE` once its last successful poll is over 60 minutes old; that is the detector for this case. | A follow-up check (tomorrow's `spot refresh --status` showing ~48 polls in 24 h) would convert "installed" into "running" at the interval, not just at load. |
| **F5** | "Exits 0 quietly": implemented as exit 0 with **one log line** (`… lock is held elsewhere; skipped, nothing recorded`) rather than silence, so a skipped firing is visible in `refresh.log` without ever being an error. | Confirm, or say if Cowork wants true silence. |
| **F6** | Engineering slips caught before reporting: a `_follow_next` test indexed the output one line too far (a test bug, fixed; the loop was correct), and an assertion `written == [...] or all(...)` was trivially true because file names carry a timestamp prefix. It now asserts the exact names. | None. |

---

## 8. Files changed

`Makefile` · `dbt/models/intermediate/int_play_events__deduped.yml` · `launchd/local.spot.refresh.plist.template` (new) ·
`migrations/0004_spot_meta_poller.sql` (new) · `scripts/launchd.sh` (new) · `scripts/refresh_agent.sh` (new) ·
`spotify_lakehouse/auth.py` · `spotify_lakehouse/cli.py` · `spotify_lakehouse/db.py` · `spotify_lakehouse/migrate.py` ·
`spotify_lakehouse/poller.py` (new) · `tests/conftest.py` · `tests/test_follow_next.py` (new) · `tests/test_migrate.py` ·
`tests/test_poller.py` (new) · `tests/test_poller_db.py` (new). **16 files.** Contracts and `CLAUDE.md`: untouched.
Nothing under `data/` is tracked.

## 9. Definition of done (`CLAUDE.md` §7)

| Item | State |
|---|---|
| `uv run pytest` | ✅ 127 passed, 0 skipped |
| `uv run dbt build` (`stg_main`/`mart_main`) | ✅ PASS=115 WARN=0 ERROR=0 (114 + the new unit test) |
| `ruff check` / `ruff format --check` | ✅ |
| Pre-commit on the round's files, gitleaks included | ✅ all hooks Passed over **16** files (count checked, passed one per argument via `xargs -0`); again at commit |
| CI-equivalent `dbt parse` (template profile, no DB) | ✅ exit 0, no warnings |
| **A guard proven to fail** | ✅ `test_gap_recorded_when_window_starts_after_the_high_water_mark` (§5.1) |
| **Agent verified running** | ✅ load, a launchd firing, its log lines, `poll_run` row 11, `last exit code = 0` (§4.2). Interval firing: pending (F4) |
| API calls this round | 3 polls (2 manual, 1 by the agent): one `recently-played` call each |

---

```
/project-round-close spot-main-R-017
```

**Start 2026-09-14 1:32 PM / End 1:45 PM : 12:40**
