# spot-main-R-008 — Build report: the EDA notebook, and the first thing Marc can open

**Session:** `main` · **Date:** 2026-09-14 · **Status:** built, rendered, verified. No contract amended.

## The deliverable

**`~/projects/ai_orchestrator_claude/spotify/reports/01_api_inventory.html`** (294 KB, self-contained, gitignored).
Open it from Finder, or from the `spotify` (main) VS Code terminal with `open reports/01_api_inventory.html`.
Regenerate with `make report-01`.

It covers all 14 endpoint sections in `docs/notebook-specs.md` §1–§5: **3 read from the warehouse, 11 called live
(17 API calls, all HTTP 200), 0 could not be sampled**. Six removed endpoints were deliberately not called (§6.1).
§6 "What We Cannot Get" and §7 "Coverage Summary" are built from this run's measurements.

---

## 1. What was built

| What | Location |
|---|---|
| Notebook entry point | `spotify_lakehouse/notebook.py`: `setup(profile)` resolves `.session`, reads `~/.config/spot/`, opens Postgres (autocommit), checks the profile is registered, configures pandas / matplotlib / plotly, returns `NotebookContext`. `ctx.frame()` / `ctx.rows()` / `ctx.scalar()` take `{stg}` / `{mart}` placeholders, quoted as identifiers. `ctx.api()` yields a paced client (1.5 s) **while holding the `spot_refresh` lock**, so the poller skips instead of competing. Settings and connection are excluded from `repr` |
| Inventory helpers | `spotify_lakehouse/inventory.py`: 14 `EndpointSpec`s; `collect()` (warehouse) and `collect_live()` (the rest); **one emitter, `emit_endpoint`, for every section**; `schema_frame` (derived from the response: type, presence, null rate, documented yes/no, **redacted example**); `sample_frame` (**redacted**); §4.4, §4.5, §6, §7 emitters |
| Notebook | `notebooks/01_api_inventory.ipynb`, 26 cells. Every code cell is preceded by a markdown cell; no `print()`; committed with **0 outputs** (nbstripout Passed) |
| Rendering | `make report-01`: execute in place → nb2report → `reports/01_api_inventory.html` → strip outputs again |
| Dependencies | `matplotlib` 3.11.2 and `plotly` 7.0.0 added to the dev group. **nb2report is not in `pyproject.toml` or `uv.lock`** (§4 F1) |
| Docs | `README.md`: "Notebooks and reports" section, including why nb2report is attached per run |
| Tests | `tests/test_inventory.py` (redaction of samples **and** schema examples, nested summaries, documented/conditional/undocumented fields, `/items`→`/tracks` fallback, unsafe ids never placed in a path, errors captured, coverage counts) · `tests/test_notebook.py` (setup against the database, no credential in `repr`, unregistered profile refused with the fix) |

**Where each section's data came from:**

| Source | Sections |
|---|---|
| Warehouse (`raw.api_response`, no API call) | 1.1 `/me` (latest response) · 4.1 recently-played (latest response, 50 items) · 5.1 `/artists/{id}` (all 45 stored responses) |
| Live, once, under the lock | 2.1 `/me/tracks` · 2.2 `/me/albums` · 2.3 `/me/following` · 3.1 `/me/playlists` · 3.2 `/playlists/{id}/items` · 4.2 top artists ×3 · 4.3 top tracks ×3 · 5.2 `/albums/{id}` · 5.3 `/tracks/{id}` · 5.4 `/me/shows` → `/shows/{id}` → `/episodes/{id}` · 5.5 `/search` |
| Not called, by policy | the six removed endpoints (§6.1) |

**URLs and ids from responses are never trusted** (CLAUDE.md §5). No `next` or `href` is followed. Ids taken from
responses (a playlist, a show, an episode) must match `[A-Za-z0-9]{1,64}` before they're placed in a path; a test proves
an id like `../../me/player` is never requested.

---

## 2. Verification of the rendered report

| Check | Result |
|---|---|
| Structure | 7 H2 · 19 H3 (14 endpoints, §4.4, §4.5, §6.1–6.3) · 56 H4 (4 per endpoint) · TOC depth 3 |
| Figure | 1 embedded PNG **with its caption**: "Plays per calendar date, counted by UTC date and by the profile's home-timezone date" |
| Live calls | 17 requests, **all 200** |
| **PII** | `/me` `id`, `display_name`, `account_id`, `uri`: **0 occurrences each** in the HTML; 619 redaction markers |
| Committed notebook | 0 outputs, 0 execution counts |
| `analytics` | untouched; dbt ran only through `make dbt-build` against `stg_main`/`mart_main` |

---

## 3. Measurements the report surfaced

### 3.1 Field drift: measured across every object captured in this run

| Object (count examined) | Documented field **absent from every object** | Undocumented field returned |
|---|---|---|
| artist (65) | `genres`, `popularity`, `followers` | — |
| track (76) | `popularity`, `available_markets`, `preview_url` | — |
| album (6) | `popularity`, `available_markets`; `label` too on the full `/albums/{id}` response | — (`genres` **key present on 6/6**; contents not measured) |
| playlist (5) | **`tracks`** | **`items`**, `primary_color` |
| playlist item (5) | **`track`** | **`item`**, `video_thumbnail`, `primary_color` |
| show (1) | `available_markets`, **`publisher`** | — |
| episode (1) | `audio_preview_url` | — |
| user `/me` (1) | `email` (conditional: the scope is never requested) | `account_id` |

"Documented" means transcribed from the Spotify reference for this round. The notebook labels it as documentation, not
measurement. Absences are measured.

### 3.2 The prompt's two findings, re-measured

| Finding | Prompt said | Measured at 3:19 PM |
|---|---|---|
| Local date ≠ UTC date | "96% (48 of 50)" | **49 of 78 plays (63%)**, all `America/Los_Angeles`. The notebook computes this at run time |
| The API window is the whole API history | `fct_play_event` 64 rows vs `api_coverage_start` | **78 plays**, 2026-09-13 23:55:33 → 2026-09-14 22:10:28 UTC. Contract `api_coverage_start` (first ok poll) = **2026-09-14 20:42:46 UTC**; `dim_profile.api_coverage_start` as built = **05:52:28 UTC** (old definition). **64 of 78 plays predate the first ok poll** |

### 3.3 Other §6 measurements

- `ms_played`: **0 of 78** fact rows carry it.
- Recently-played item types across all 10 stored responses: tracks only.
- The six removed endpoints are refused locally by the client before any request.
- R-017 F4, closed by observation: `spot_meta.poll_run` held **5 `ok` polls, the latest at 21:43:08 UTC**, at 3:02 PM. The
  agent is firing on its 30-minute interval, not only at load.

---

## 4. Findings: where the prompt, the spec or a tool was wrong

| # | Finding | Evidence | Proposal |
|---|---|---|---|
| **F1** | **"Install nb2report as a dev dependency via local path" breaks CI.** A path source in `pyproject.toml` / `uv.lock` makes `uv lock --check` and `uv sync --locked` fail wherever `../nb2report` doesn't exist, GitHub's runner included. | Measured in two scratch copies of the repo without the sibling folder, with nb2report in the dev group **and** in a separate `report` group (synced with `--no-group report`). All four commands exited **2**: `Failed to generate package metadata for nb2report==0.1.0 @ editable+../nb2report … Distribution not found`. | **Implemented instead:** `make report-01` runs `uv run --with-editable ../nb2report nb2report …`, which uses the local checkout (not a git URL, so remote visibility doesn't matter) and leaves the lock untouched (`uv lock --check` exit 0). Documented in the README. The DoD's literal `uv run nb2report …` works only if nb2report is installed into the venv by hand, and `uv sync --locked` would remove it. |
| **F2** | **nb2report cannot produce warning or critical callouts, so its "Issues Only" filter never shows anything.** §0 and the prompt rely on `> [!WARNING]` / `> [!CAUTION]` driving that filter. | Rendered through nb2report's own renderer (commit `3626962`, mistune 3.3.4), five syntaxes all classify as **info**: `[!WARNING]` multi-line and single-line, `[!CAUTION]`, `**Warning:**`, `**WARNING**`. The report has **26 callouts, all info**. Cause: `_CalloutAwareRenderer.block_quote()` receives the **already-rendered** HTML (`<p>[!WARNING]…`), and `callout_severity()` anchors its regex at `^\[!`. `callout_severity('[!WARNING] x')` = warning; `callout_severity('<p>[!WARNING]\nx</p>')` = info. A raw-HTML `callout-warning` block keeps the styling but `render_section` never counts it (`has_callouts = False`). | **A defect in nb2report, a separate repo, and not fixed here.** One-line fix location: strip a leading `<p>` in `block_quote` before calling `callout_severity`. The notebook keeps the spec's `> [!WARNING]` syntax, so it classifies correctly as soon as nb2report is fixed; no re-authoring needed. Until then the `[!WARNING]` marker text is visible inside each callout. |
| **F3** | **Playlist objects no longer carry `tracks`; they carry `items`. Playlist items no longer carry `track`; they carry `item`.** | §3.1: 5 of 5 playlists and 5 of 5 items. `/playlists/{id}/items` itself returned 200, so the older `/tracks` fallback never ran. | **R-006 (playlist overlap) must extract `items` / `item`.** Worth a line in its prompt before it is written. |
| **F4** | **Show objects no longer carry `publisher`.** data-contracts §3 defines `dim_content.primary_creator_name` as "show publisher for episodes" and `dim_show` as carrying `publisher`. | 1 show examined (a sample of one, from `/me/shows`). | Measure on more shows before amending; flag for whichever round builds `dim_show`. |
| **F5** | **`dim_profile` no longer matches data-contracts §3** after `e3c62dd`. `api_coverage_start` is built from the first recently-played ingestion; the contract now says first ok `poll_run`. `has_api_access` is contracted as "stored refresh token **and** an ok poll", but **dbt cannot see the token** (it lives in `~/.config/spot/tokens.json`, outside the database). | §3.2: 05:52:28 vs 20:42:46 UTC. The notebook shows both values and explains the difference in a WARNING callout. | A small dbt round: `api_coverage_start` from `spot_meta.poll_run`; redefine `has_api_access` as something dbt can observe (e.g. an ok poll within N days), or have the poller record token presence in `spot_meta`. |
| **F6** | **The spec's hierarchy has no place for the prompt's two "sections of their own".** Added as **H3 4.4** (local vs UTC date, with the figure) and **H3 4.5** (the API window) under §4 Listening. | Additive only: no §1 heading was removed, renamed or reordered. | Confirm, or say where Cowork wants them; the move is a notebook edit. |
| **F7** | The prompt's numbers were a day old: "96% (48 of 50)", "64 plays", "`ms_played` NULL on all 64". | §3.2: 49 of 78 (63%), 78 plays, 0 of 78 with `ms_played`. | None. The notebook computes every such figure at run time rather than quoting it. |
| **F8** | Caught before reporting: `Capture.live_calls` counted parts that were never requested (no playlist id, no show id), which would have over-reported API calls (17 vs 15 in the mocked test). A caption placed in a markdown cell that also held a heading was silently ignored by nb2report. | Test `test_live_errors_are_captured_not_raised_and_counted_in_coverage` failed on the first; the first render had 0 captions. | Both fixed: `Part.requested` is set only when HTTP is sent; the caption has its own cell. |

---

## 5. Red test: a redaction nobody has watched fail is not a redaction

**Break:** `redact_profile` returns its input unredacted.

```diff
>     return df.copy()  # BREAK (R-008 red test)
```

```
FAILED tests/test_redact.py::test_redact_profile_masks_identifiers_keeps_analytics_fields
FAILED tests/test_inventory.py::test_schema_examples_are_redacted_like_samples
FAILED tests/test_inventory.py::test_sample_frame_redacts_owner_fields_and_summarises_nested_values
3 failed, 10 passed
```

**Red: all three redaction tests, including both new ones.** The schema examples and the samples go through the same
`redact_profile` call, so one break turns both red: there is no second, unguarded redaction path. The break was staged
**after** the final render, so the report was never produced with redaction disabled. Reverted, byte-identical by `cmp`;
full suite **138 passed**.

---

## 6. Files changed

`Makefile` (`report-01`) · `README.md` · `pyproject.toml` · `uv.lock` (matplotlib, plotly) ·
`notebooks/01_api_inventory.ipynb` (new, stripped) · `spotify_lakehouse/inventory.py` (new) ·
`spotify_lakehouse/notebook.py` (new) · `tests/test_inventory.py` (new) · `tests/test_notebook.py` (new) · this report.

**Not committed:** `reports/01_api_inventory.html` (gitignored `/reports/`). Also left for Cowork: its uncommitted
`claude_work/spot_request_register.md`, `claude_work/prompts/spot-main-R-032.md` and `claude_work/_delete_probe`.
Contracts, `CLAUDE.md` and `docs/notebook-specs.md`: untouched.

## 7. Definition of done (`CLAUDE.md` §7 + prompt)

| Item | State |
|---|---|
| `uv run pytest` | ✅ 138 passed, 0 skipped |
| `uv run dbt build` | ✅ PASS=115 WARN=0 ERROR=0 (no model changes) |
| `ruff check` / `ruff format --check` | ✅ |
| Pre-commit, gitleaks included | ✅ every hook Passed, nbstripout included, one argument per file via `xargs -0`; again at commit |
| **Guard proven to fail** | ✅ `redact_profile` break → 3 named tests red (§5) |
| nb2report render "runs clean" | ✅ with the §4 F1 invocation (`make report-01`); the literal `uv run nb2report` form is F1 |
| Report states its own coverage | ✅ 3 warehouse · 11 live (17 calls) · 0 not sampled · 6 not called, in the report intro and §7 |
| Rendered HTML path given to Marc | ✅ top of this report |

---

```
/project-round-close spot-main-R-008
```

**Start 2026-09-14 3:00 PM / End 3:22 PM : 22:40**
