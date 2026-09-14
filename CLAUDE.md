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
- The request register (`claude_work/spot_request_register.md`) and R-numbers.
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

1. Marc makes a request in **Cowork**. Cowork assigns an R-number immediately and writes it to the
   register — before any round starts.
2. Cowork writes a prompt file to `claude_work/prompts/spot-<session>-R-###.md`.
3. Marc pastes the prompt id into the Claude Code session named in the filename.
4. Claude Code executes, and ends by writing a build report to
   `claude_work/reports/spot-<session>-R-###-report.md`.
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

- **The Web API has no history.** `GET /me/player/recently-played` returns at most 50 items and
  cannot page backwards into the past. `/me/top/*` returns Spotify's own ranked rollups, not a
  time series.
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

**Artist `genres` still returns but Spotify now marks the field deprecated.** Every genre value is
therefore snapshotted into `dim_artist` with the date observed, so the analysis survives the field
being removed. A fallback genre source (MusicBrainz) is specified in the contracts as a phase-2
task, not a phase-1 one.

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
- **A guard was proven to fail.** Every round that adds a test or constraint stages a break, records
  which named test went red, and reverts. "The tests pass" is not evidence; "test_play_event_grain
  failed with N duplicate keys when I removed the dedupe" is.
- A build report exists at `claude_work/reports/spot-<session>-R-###-report.md` naming: files
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
