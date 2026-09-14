# spot-main-R-018 — Build report: does `recently-played` page backwards?

**Session:** `main` · **Date:** 2026-09-14 · **Status:** measured, reported, stopped. `CLAUDE.md` §4 not touched.

---

## 1. The answer, as a measurement

**Page 2 came back empty.**

| Page | Request | Items | Oldest `played_at` | Newest `played_at` | Not seen on an earlier page | `next` at end |
|---|---|---|---|---|---|---|
| 1 | `GET /me/player/recently-played?limit=50` | **50** | 2026-09-14T00:43:58.287Z | 2026-09-14T19:25:08.803Z | 50 | non-null |
| 2 | page 1's `next` → `before` = **2026-09-14T00:43:58.287Z** | **0** | — | — | 0 | **null** |
| 3 | not requested | — | — | — | — | — |

Page 1's `next` carried `before=<cursor>`, and that cursor decoded to **exactly page 1's oldest `played_at`**. Page 2's payload
kept the envelope (`href, items, limit, next, cursors`) with `items = []`, `next = null` and `cursors = null`. With
`next` null there was nothing further to follow, so the run stopped after page 2. The endpoint was called twice.

### Plays known to exist behind the cursor

This is what separates "Spotify returned nothing older" from "there was nothing older to return":

- `raw` already holds the recently-played window captured by R-002 (raw ids 6 and 8), spanning **2026-09-13T23:55:33.038Z →
  2026-09-14T05:10:17.555Z**.
- **14 distinct R-002 plays are older than page 1's oldest** (2026-09-13T23:55:33.038Z → 2026-09-14T00:41:22.570Z).
  Spotify returned all 14 in R-002, about 19 hours before this run.
- Page 2, asked for plays before 00:43:58.287Z, returned **none of them**.
- 36 of page 1's 50 items were already in raw from R-002: the 50-item window had moved forward by 14 plays since then.

**Stated as measured, and no further:** on 2026-09-14 at 13:01 PT, for profile `marc`, following page 1's `next` once
returned 0 items and `next = null`. This happened although at least 14 earlier plays were demonstrably returned by the same
endpoint the day before. This test reached **one page** past the first. It shows no backward access on this date for this
account through `next`. It does not establish how Spotify behaves for other accounts, other `before` values chosen
independently of `next`, or on other days.

---

## 2. Implications for Cowork — stated as questions, not rewrites

`CLAUDE.md` §4 is Cowork's. The measurement bears on:

| Where | Currently | What the measurement says |
|---|---|---|
| `CLAUDE.md` §4 provisional warning | "`spot-main-R-018` follows `next` once to settle it" | Followed once: **0 items, `next` null**, with 14 earlier plays known to exist |
| R-017 (poller), blocked on R-018 | "if `next` pages backwards, the poller's job changes … to backfill" | The backfill branch has no evidence behind it. Catch-before-overflow remains the job; the 50-item window moved 14 plays in ~19 h for this account |
| `data-contracts.md` §6 open item R-018 | "one API call, gates R-017" | Settled by this measurement if Cowork accepts one page as sufficient |

---

## 3. What was built

| What | Location | Notes |
|---|---|---|
| `spot probe --follow-next [N]` | `spotify_lakehouse/cli.py` (`_follow_next`, `_page_line`, `_follow_next_count`) | Follows `next` up to N times **after** page 1 (see §5 C1). Bare flag = 1, max 5, absent = 0 (probe unchanged). Keeps the `spot_refresh` advisory lock; 2 s pause between pages on top of client pacing and `Retry-After`. Every page persisted as `feed=recently_played`, file key `pageN`. Prints per page: items, min/max `played_at`, count not seen on earlier pages, `next` state, the requested `before` decoded to UTC, the raw id |
| `next` link validator | `spotify_lakehouse/paging.py` (`next_page_params`) | **The client attaches the bearer token to every request, so a `next` URL from a response is never requested as given.** It must be `https://api.spotify.com/v1/me/player/recently-played` with no port, no userinfo, only `before` (numeric, required) and `limit` (numeric), with no repeats. It is reduced to params and re-issued through the normal client. `UnsafeNextLink` exits 2 with a one-line message |
| Tests | `tests/test_paging.py` (15), `tests/test_cli.py` (7) | Valid link; foreign, look-alike, `http`, non-default-port and userinfo origins refused; wrong path, traversal, missing/non-numeric cursor, extra or repeated params refused; flag absent / bare / 3 / 5 / 0 / 6 / -1 / non-numeric |

**Rows written:** raw ids **67** (`me`), **68** (page 1), **69** (page 2, empty). Files under
`~/spot-data/raw/api/{me,recently_played}/marc/…_probe-main-0cc8912f*.json`. Nothing under `data/` is tracked.

**Downstream:** `make dbt-build` with the new rows: **PASS=114 WARN=0 ERROR=0**. `fct_play_event` went from 50 to **64**:
page 1 added 14 plays not already present, and the dedupe collapsed the 36 overlapping ones. The empty page 2 contributes
no rows, as expected.

---

## 4. Red test

**Break:** remove the host check from `next_page_params`.

```diff
<     if parts.scheme != _ALLOWED_SCHEME or parts.hostname != _ALLOWED_HOST or parts.port is not None:
>     if parts.scheme != _ALLOWED_SCHEME or parts.port is not None:
```

`uv run pytest tests/test_paging.py`:

```
FAILED tests/test_paging.py::test_foreign_or_downgraded_origin_is_refused[https://evil.example/v1/me/player/recently-played?before=1&limit=50]
FAILED tests/test_paging.py::test_foreign_or_downgraded_origin_is_refused[https://api.spotify.com.evil.example/v1/me/player/recently-played?before=1]
2 failed, 13 passed
```

**Red: `test_foreign_or_downgraded_origin_is_refused`**, both foreign-host cases. Those are exactly the two cases where
a response-supplied `next` would have sent Marc's bearer token to another host. The `http`, port and userinfo cases
stayed green because separate checks still cover them. Reverted, byte-identical by `cmp`; full suite **114 passed**.

---

## 5. Findings — including where the prompt was ambiguous

| # | Finding | Proposal |
|---|---|---|
| **C1** | "`--follow-next N` (default 1)" and "run it with `N=3`" leave open whether N counts pages or follows. Implemented: **N = follows after page 1** (N=3 → up to 4 pages), **bare flag = 1**, **absent = 0**, so a plain `spot probe`, and R-017's poller if it reuses the probe, never makes an extra call it did not ask for. A literal "default 1" on every probe would silently double recently-played calls. The run is unaffected by the reading: page 2 ended it. | Confirm, or say which reading Cowork meant. |
| **C2** | `next` is not trustworthy input: it is a URL chosen by the response, and following it naively sends the token wherever it points. The prompt did not ask for validation. | Keep it for any future cursor-following (playlists, library, top items all page by `next`). `paging.py` covers only recently-played today. |
| **C3** | `/me` on this run returned no `email` and did return `account_id`, consistent with R-002 and the F7/F8 amendments. | None. |
| **C4** | The `_follow_next` loop itself has no unit test with a mocked client; its behavior is evidenced by the live run (§1) and the database cross-check, which agree page by page. The validator and the flag are unit-tested. | Add a `MockTransport` loop test when R-017 reuses it. |

---

## 6. Definition of done (`CLAUDE.md` §7)

| Item | State |
|---|---|
| `uv run pytest` | ✅ 114 passed |
| `uv run dbt build` against `stg_main`/`mart_main` | ✅ PASS=114 WARN=0 ERROR=0 (with this run's rows) |
| `ruff check` / `ruff format --check` | ✅ |
| Pre-commit on staged files, gitleaks included | ✅ at commit |
| **A guard proven to fail** | ✅ `test_foreign_or_downgraded_origin_is_refused` (§4) |
| Build report | this file |
| Contracts / `CLAUDE.md` touched | none |
| API calls | 3: `/me`, page 1, page 2, all under the advisory lock |

---

**Start 2026-09-14 12:58 PM / End 1:03 PM : 04:47**
