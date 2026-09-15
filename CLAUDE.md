# CLAUDE.md — spotify-lakehouse

**Project abbreviation: `spot`. Owner: Marc Alexander. Repository: `marc4data/spotify-lakehouse` (PUBLIC).**

A local Postgres warehouse of Spotify listening data for Marc and his family, modeled to Kimball
dimensional standards, surfaced through Jupyter notebooks and Tableau Public.

---

## 0. Read this first

| | |
|---|---|
| **The repo is public.** | Every commit is world-readable the moment it is pushed. There is no "I'll clean it up later." See §5. |
| **The data is intimate.** | Play-by-play listening history for four people, two of whom did not build this. Treat `raw/` and any notebook output as personal data. |
| **Two brains, one wall.** | Cowork manages. Claude Code builds. Neither does the other's job. See §1. |
| **Contracts are files, not conversation.** | If it is in `docs/data-contracts.md`, it is binding. If it is not, Claude Code decides. See §2. |

---

## 1. Separation of duties — church and state

This project is run by two different Claude surfaces with a hard wall between them.

### Cowork is the project manager

Cowork **owns**:

- The dimensional model — table names, grain statements, keys, conformed dimensions, slowly-changing
  policy. This is a statement about *what the data means*, which is a business decision.
- Business definitions — what counts as a "listen," how genre buckets roll up, how a playlist
  "overlap" is defined.
- The request register (location: the round contract, §2) and R-numbers.
- Prompt authoring, sequencing, acceptance criteria, round review.
- Verification that a build report matches the repository.

Cowork **does not**: write application code, run the ETL, execute dbt, or touch the database.
Cowork reads the repo to verify claims. It does not author the thing it is verifying.

### Claude Code is the engineer

Claude Code **owns** everything behind the contract: module layout, file organization, library
choices, error handling, retry and backoff, caching, logging, test structure, SQL style inside a
model, CLI ergonomics, migrations, CI configuration.

Claude Code **does not** change a contract. If a contract is wrong — and it will sometimes be wrong,
because it was written before the data was seen — Claude Code **stops, states the problem, and
proposes the amendment.** It does not route around it and it does not silently improve it. A model
that cannot be built as specified is a finding, and a finding is worth more than a workaround.

### Marc

Decides what gets built next, whether an output is *right* about the subject, and anything that
spends money or is irreversible. He is not asked about branches, worktrees, test strategy,
R-numbers, or repo structure.

---

## 2. How work flows

### Round contract — declared in the PM folder, not here

🚨 **The round contract lives at `~/projects/ai_orchestrator_claude/spotify-pm/round-contract.md`.** It is
the single declaration of this project's five round facts (abbr, sessions, register, id shape, repo root),
where prompts and reports live, and this project's round traps. **Read it there. This file does not restate
it** — a second copy is a copy that drifts (R-033, answering R-032 F1: Cowork is connected to the PM folder,
so the contract has to be readable from the PM folder).

🚨 **The command file's paths do not bind this project.** `.claude/commands/project-round-close.md` is
generated from notso-prompt's skill and still says `claude_work/reports/…` and "No such id in
`claude_work/`". **Ignore those literals: the round contract's paths override them** until notso-prompt
regenerates the file from a skill that reads paths from the contract (R-032 F2). The file is not
hand-edited here, and a pre-commit hook rejects any file under `claude_work/` in this repo.

🚨 **This project does not edit the round skill. Marc's rule, 2026-09-14.** The **notso-prompt** project
owns `~/notso_prompt/skill/SKILL.md` as the single source of truth. Any change this project wants to the
shared workflow — a new trap, a corrected rule, a wording fix — is **proposed to notso-prompt**, never
applied here by editing the account skill directly. Unsynchronised edits from two surfaces silently
overwrite each other; a save replaces the whole file, so the loser never learns it lost.

What this project may still change on its own: `.claude/commands/project-round-close.md`, but only by
**regenerating** it from that source — never by hand-editing the shared rules inside it. Its provenance
header names the source and the sha256 it was generated from. **Check currency in one command, from this
repo:**

```
shasum -a 256 ~/notso_prompt/skill/SKILL.md
```

Match against the header means current. Mismatch means ask notso-prompt for the delta and regenerate.
Compare hashes, never phrases — a grep for a phrase that wraps across a line reports ABSENT for text that
is present, and answers a different question than the one asked (notso-prompt, 2026-09-14).

**Project-specific round traps** are declared in the round contract, with the five facts (R-033).

### The flow

1. Marc makes a request in **Cowork**. Cowork assigns an R-number immediately and writes it to the
   register — before any round starts.
2. Cowork writes a prompt file, `spot-<session>-R-###.md`, into the PM folder's `prompts/` (path: the
   round contract).
3. Marc pastes the **handoff cell** into the Claude Code session the id names. The cell is the slash
   command and the round id, nothing else:

   ```
   /project-round-close spot-main-R-004
   ```

   🚨 **This is the standard, and it is the only form Cowork hands over.** Not a bare id, not a raw file
   path, not "read this file and execute it" — that form is retired and must never be offered again, not
   as a fallback, not when a round is blocked, not when the skill looks unavailable. If a window rejects
   the command, the fix is to say so and stop, not to route around the standard.

   ⚠️ **Verified 2026-09-14: BOTH bare `/project-round-close` and namespaced
   `/anthropic-skills:project-round-close` return `Unknown command` in Claude Code**, and a window
   restart does not change it. Account and plugin skills from claude.ai exist only in the Cowork cloud
   container; they are **not installed on this Mac**, so there was never a local skill for either form to
   address. `/cfdb-round` resolves in the same window because it reaches Code by a different route.

   Cowork asserted the namespaced form as "the standard" on the strength of how Cowork itself addresses
   the skill, and never tested the Code side. That is precisely the failure the round rules exist to
   prevent: **a measurement is reproducible; a diagnosis is an inference from one.** Four rounds were
   lost to it.

   **What actually reaches Claude Code** is a repo-local command file,
   `.claude/commands/project-round-close.md`, committed with the project. It carries only the execute
   half of the workflow; Cowork keeps the close format from the account skill, so there is no duplicated
   content to drift. A user-level install at `~/.claude/skills/` would cover every project at once, but
   the device bridge cannot write to `~/.claude` — that one is Marc's to run by hand.

   **The working command form is recorded here only after it has been observed working in Claude Code,
   never inferred from how Cowork addresses it.**

   🚨 **The cell carries the FULL id — `spot-<session>-R-###`, never the short `R-###`.** The session
   segment is what tells Marc which window to paste into, and it is what lets the command refuse a round
   that belongs to a different session. A short id makes both of those impossible and forces a glob that
   can match two things. The short form is legal *inside* the skill's resolver; it is not legal in a
   handoff cell.

   4. **When the round finishes, the Code session ends its report with the same cell.** Marc pastes it
   into Cowork, which then reviews the report and closes the round. The command is surface-dependent by
   design, so one cell works in both directions — Code executes it, Cowork reviews it. **A report that
   does not end with its return cell is not finished**, because it leaves Marc to compose the handoff
   himself.

   A bare `R-004` is ambiguous
   across Marc's machine — several of his projects call their units of work "rounds", keep a
   `claude_work/` directory with prompts and a register, and one has its own skill that triggers on round
   ids (R-021). The slash command names the skill explicitly, so there is nothing to guess. A raw path
   works but skips every check the skill performs: committed-or-not, session match, duplicate ids.

   **If a session replies without having opened the round, that is a failed handoff — start a fresh
   session rather than rephrasing at it** (R-021).

   ~~A prompt file is not handed off until it is committed and pushed.~~ **Retired by R-032.** The rule
   existed because prompts lived inside this repo, where a worktree sees only committed state — three
   consecutive rounds were lost to it (R-022). Prompts now live in the PM folder, outside every checkout,
   and are read by absolute path, committed or not.

   **Small rounds skip the file entirely.** If the instruction fits in a paste, put it in the paste —
   self-contained, no path, no dependency on repo state. The file-based prompt exists for rounds too
   large to paste, not as a ceremony. A one-command change handed over as a file reference is pure
   overhead, and it is overhead that has now failed more often than it has worked.
4. Claude Code executes, and ends by writing a build report, `spot-<session>-R-###-report.md`, into the
   PM folder's `reports/` (path: the round contract).
5. Cowork reads the **repository**, not the report, to move the register row.

**R-number format: `spot-<session>-R-###`** — e.g. `spot-main-R-007`, `spot-wta-R-042`. The session
name is inside the id, so parallel sessions cannot collide. **Sessions never mint their own numbers.**
A session that needs one asks Cowork.

### Register status key

| | |
|---|---|
| ✅ | Landed — Marc has seen it working |
| 🔨 | In flight |
| 📋 | Queued |
| ⏸️ | Parked — waiting on something external |
| 💭 | Deferred by agreement |
| ❓ | Reported built, unverified |
| ⚠️ | Dropped — Cowork's failure, recorded as one |

---

## 3. Cowork, not Chat

**Marc's stated rule: using plain Claude Chat for this project is a mistake.** Chat has neither the
project context nor the file access, and answers from it will be confidently wrong about the schema,
the contracts, and what has already been built.

The tell, for Marc: **a real project reply always ends with the Request Register table, Action Items,
and a clock line.** If a reply about this project has none of those, it did not come from the project
manager and should not be acted on.

Any Claude surface that finds itself answering a `spotify-lakehouse` question without access to this
file should say so plainly and tell Marc to move the question to the Cowork project.

---

## 4. Architecture

```
Spotify Extended Streaming History (.json, GDPR export)  ─┐
                                                          ├─▶  data/raw/  ─▶  Postgres `raw`
Spotify Web API (extractors, launchd every 30 min)       ─┘        (immutable JSON, gitignored)
                                                                            │
                                                                    dbt-postgres
                                                                            │
                                                     staging ─▶ intermediate ─▶ marts (Kimball star)
                                                                            │
                                              ┌─────────────────────────────┼──────────────────┐
                                        notebooks/                    published/          Tableau Public
                                      (via db module)            (.csv / .xlsx extracts)
```

### Stack — fixed, not Claude Code's to change

| Layer | Choice |
|---|---|
| Python env & deps | **uv** (`uv sync`, `uv run`). Lockfile committed. Python 3.12. |
| Database | **Postgres 16 in Docker**, `docker-compose.yml` in repo, host port **5433** (avoids collision with any system Postgres) |
| Transformation | **dbt-postgres**. Layers: `staging` → `intermediate` → `marts`. |
| Notebooks | Jupyter, run via `uv run jupyter lab` |
| Report rendering | **nb2report** (`~/projects/ai_orchestrator_claude/nb2report`) |
| Presentation | Tableau Public, fed by flat extracts from `published/` |
| Scheduling | **launchd** user agent, every 30 minutes |

### Two loading paths, one fact table

This is the single most important architectural fact and the reason the model looks the way it does.

- **The Web API has no history.** `GET /me/player/recently-played` returns at most 50 items per call.
  `/me/top/*` returns Spotify's own ranked rollups, not a time series.
  > [!NOTE]
  > **Settled by measurement, `spot-main-R-018`, 2026-09-14 13:01 PT, profile `marc`.** Page 1's `next`
  > carried `before=` decoding to exactly page 1's oldest `played_at`. Following it returned **0 items and
  > `next = null`** — while `raw` held **14 distinct plays older than that cursor**, returned by the same
  > endpoint ~19 hours earlier. **Older plays existed and were not returned**, which is what makes the
  > empty page evidence rather than an absence of data.
  >
  > **Scope, and no further:** one page past the first, one account, one date, cursor taken from `next`.
  > It says nothing about `before` values chosen independently, other accounts, or other days. The earlier
  > wording ("cannot page backwards") is now supported — but it is recorded as a scoped measurement, not
  > restored as a flat claim, because asserting it flat from the API reference is what cost four rounds.
- ⚠️ **An overflow gap is evidence of possible loss, not proof of it** (R-017 F3). The poller records a gap
  when the 50-item window starts after the newest play already captured. But if exactly 50 plays happened
  in the interval, the window's oldest *is* the first play after the mark and nothing was lost — two
  timestamps cannot tell those apart. `items_returned` is stored so the gap can be judged: **a gap with
  fewer than 50 items cannot be an overflow.** Any surface that shows gaps says this, or it reports
  phantom losses as fact.
- **`recently-played` does not return podcast episodes at all.** Spotify's reference states this
  explicitly. The music-vs-podcast time split is therefore **impossible from the API** and comes
  only from the export.
- **The Extended Streaming History export** (GDPR, requested per-account at
  `https://www.spotify.com/account/privacy/`, ~weeks to arrive) carries every play since account
  creation, music and podcast, with `ms_played`, `reason_start`/`reason_end`, `skipped`, `shuffle`,
  `offline`, and platform.

So: **the export is the spine, the API is the enrichment plus the ongoing incremental feed.** Both
land in one fact table with a `source_system` column and a documented dedupe rule (§2 of
`docs/data-contracts.md`).

### Dead API surface — do not attempt

Cut off for apps registered after 2024-11-27, which includes this one:
`/audio-features`, `/audio-analysis`, `/recommendations`, `/artists/{id}/related-artists`,
`/browse/featured-playlists`, `/browse/categories/{id}/playlists`, 30-second preview URLs.

**Batch lookups, measured closed to this app (R-037 F1, recorded by R-039).** Two batch endpoints have been
tried and both returned **403 `Forbidden`**, each against a working single-id control:

| Endpoint | Measured | Single-id control |
|---|---|---|
| `GET /artists?ids=` | 403, spot-main-R-004, 2026-09-14 | `GET /artists/{id}` 200 |
| `GET /tracks?ids=` | 403, spot-main-R-037, 2026-09-14 (httpx and curl; 5 ids, 1 id, `market=from_token`) | `GET /tracks/{id}` 200 |

That is two endpoints, for this app, on that date, not a claim about every batch endpoint. Until another
is measured, the cost model is **one call per id at ~1 call/s**.

> [!CAUTION]
> **Artist `genres` is gone, not deprecated. Measured 2026-09-14 (spot-main-R-004 §1):** the `genres`
> key is **absent from 55 of 55 artist objects** across `GET /artists/{id}` (45 artists, every one in
> `raw`), `/me/top/artists` and `/me/following`. `popularity` and `followers` are absent too. This
> document previously said genres "still returns but is marked deprecated" — that was read off the API
> reference, which still documents the field, and stated as though it were a measurement of live
> behaviour. **Documentation is not a measurement.** The same error class cost four rounds on R-023.
>
> Consequence: Spotify supplies **no genre data to this app at all**. Genres come from **MusicBrainz,
> joined by ISRC** (`spot-main-R-024`: `dim_genre`, `br_artist_genre`, `dim_artist` SCD Type 2). The
> bucket taxonomy that maps them to radar spokes is R-015.

### The 5-user ceiling

Development-mode apps allow **5 authenticated users total** and require the **app owner** to hold
Premium. Extended quota mode requires a registered business with 250k+ MAU and is not available here.
Four family profiles use four slots. **Friends are export-only** — their history can be ingested
without ever touching the app.

> [!NOTE]
> Unverified: whether the four *added* users each need Premium, or only the owner. Spotify's docs
> specify the owner. `spot-main-R-016` verifies this empirically. Do not assume either way in code —
> handle the failure path.

---

## 5. Secrets — the rule that has no exceptions

**No Spotify credential, token, or account identifier ever enters the git tree.** Not in a config
file, not in a notebook output, not in a test fixture, not in a commit message, not temporarily.

### Where things live

| Item | Location | Notes |
|---|---|---|
| Client ID, Client Secret | `~/.config/spot/.env` | **Outside the repo.** No worktree can stage it. `chmod 600`. |
| Per-profile refresh tokens | `~/.config/spot/tokens.json` | Outside the repo. `chmod 600`. One object per profile key. |
| Postgres credentials | `~/.config/spot/.env` | Local-only, but same treatment. |
| dbt profile | `~/.dbt/profiles.yml` | Outside the repo. Repo ships `profiles.yml.example`. |
| Raw API/export JSON | `data/raw/` | **Gitignored, whole directory.** |
| Notebook outputs | stripped on commit | See below. |

The repo ships `.env.example` and `profiles.yml.example` with placeholder values and a
`make bootstrap` target that creates `~/.config/spot/` and copies the templates.

### Enforcement — all three are required

1. **`.gitignore`** covering `.env*` (except `.env.example`), `data/`, `published/`, `*.hyper`,
   `tokens*.json`, `reports/*.html`, `.session`.
2. **`gitleaks` as a pre-commit hook**, plus a custom hook that rejects any staged file containing a
   32-hex-character string (the Spotify client ID/secret shape) or an `@`-bearing string matching the
   family's email domains.
3. **`nbstripout` as a pre-commit hook.** Notebooks are committed **with outputs stripped.**

### Never re-issue a URL the response chose

**`next`, `href` and every other link Spotify returns is untrusted input.** The API client attaches the
bearer token to whatever it requests, so following a response-supplied URL as given would send Marc's
token wherever that URL points — a look-alike host, a downgraded `http`, an off-port listener, a
userinfo-stuffed origin.

**Contract (R-018 C2):** a link from a response is never requested as-is. It is parsed, its origin and
path checked against an allowlist, its query reduced to the parameters that endpoint is known to take,
and then re-issued through the normal client. `spotify_lakehouse/paging.py` does this for
recently-played. **Playlists, the library and top items all page by `next` too** — each one extends that
module rather than trusting the URL.

### The notebook trap

`GET /me` returns the account **email address**, display name, and country. The EDA notebook
samples every accessible endpoint, so it *will* pull real PII into cell outputs, and nb2report
embeds those outputs in an HTML file.

**Contract:** a `spotify_lakehouse.redact` module provides `redact_profile(df)` and is applied to
every display of profile, user, or follower data in every notebook. Rendered HTML reports go to
`reports/`, which is gitignored. Notebooks are committed stripped. **Neither the notebook nor the
report is ever the thing that leaks — the stripping is belt, the gitignore is braces, and both are
required.**

---

## 6. Parallel VS Code sessions

Marc runs multiple VS Code windows against this project simultaneously. Full protocol in
`docs/worktree-protocol.md`. The essentials:

- **Main checkout** is `main`. Worktrees are `wta`, `wtb`, … created as
  `git worktree add ../spotify-wta -b feat/<topic>`.
- Every checkout contains an **untracked `.session` file** holding one lowercase token: `main`,
  `wta`, `wtb`. `spotify_lakehouse.config` reads it. Everything session-scoped derives from it.
- **All sessions share one Postgres container and one `raw` schema.** dbt writes to
  `stg_<session>` / `mart_<session>`. Only `main` may build into `analytics`.
- **All sessions share `~/.config/spot/`** — one set of credentials, one token store. A new worktree
  needs `uv sync` and nothing else to be fully operational.
- **`spot refresh` takes a Postgres advisory lock.** Two sessions cannot poll Spotify concurrently
  and burn the shared rate-limit quota.

---

## 7. Definition of done, per round

A round is not complete until all of these are true:

- `uv run pytest` passes.
- `uv run dbt build` succeeds against `stg_<session>`/`mart_<session>` with all tests green.
- `uv run ruff check .` and `uv run ruff format --check .` pass.
- Pre-commit hooks pass on all staged files, gitleaks included.
- ⚠️ **A scanner that reports "no files to check" is a FAILED check, not a pass.** Read the file count
  every scanner reports and confirm it matches what you staged. zsh does not word-split an unquoted
  `$VAR`, so a whole path list reaches a scanner as one nonexistent filename and every hook cheerfully
  skips. Pass paths with `xargs -0` or one argument each (C6, R-004).
- **A guard was proven to fail.** Every round that adds a test or constraint stages a break, records
  which named test went red, and reverts. "The tests pass" is not evidence; "test_play_event_grain
  failed with N duplicate keys when I removed the dedupe" is.
- **A staged break is always followed by a full `make dbt-build` before the round reports** (R-037 F4,
  recorded by R-039). A selective `dbt build -s <view>` drops every view downstream of it until the next full
  build: dbt-postgres swaps a view by renaming the old one and dropping it with `cascade`. Measured at R-037:
  after `-s int_play_events__deduped`, `int_allocation_reconciliation` did not exist. Recoverable in a session
  schema; in `analytics` it would break a reader mid-query.
- A build report exists in the PM folder's `reports/` (path: the round contract) naming: files
  changed, contracts touched, the red-test evidence above, and anything the contract got wrong.

---

## 8. Conventions

- **SQL:** lowercase keywords, trailing commas, CTEs over subqueries, one model per file, `_key` for
  surrogate keys, `_id` for natural keys from the source, `_uri` for Spotify URIs.
- **Timestamps:** everything stored UTC as `timestamptz`. Local-time columns are derived in marts
  from `dim_profile.home_timezone`, never assumed.
- **Money/duration:** durations are integer milliseconds end to end. Convert at the presentation
  layer only.
- **Python:** type hints on public functions, `pathlib` not `os.path`, `httpx` for HTTP,
  `structlog` for logging, no bare `except`.
- **Commits:** conventional commits, R-number in the body — `Refs: spot-wta-R-042`.
