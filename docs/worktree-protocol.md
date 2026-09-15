# Worktree Protocol — parallel VS Code sessions

**Requirement:** Marc runs several VS Code windows against this project at once. Any new window must
become fully operational — credentials, database, dbt, notebooks — with one command and no manual
configuration. Nothing session-specific may live in a tracked file.

---

## 1. Session identity

Every checkout carries an **untracked `.session` file** containing exactly one lowercase token:

```
main    # ~/projects/ai_orchestrator_claude/spotify        (primary checkout)
wta     # ~/projects/ai_orchestrator_claude/spotify-wta
wtb     # ~/projects/ai_orchestrator_claude/spotify-wtb
```

`.session` is in `.gitignore`. `spotify_lakehouse.config.session()` reads it and **raises** if it is
missing — no defaulting to `main`, because a worktree that silently thinks it is `main` will build
into `analytics` and overwrite the promoted marts.

Creating a worktree, from `main` (R-033):

```bash
make worktree NAME=wta   # ../spotify-wta on branch feat/wta, writes .session, runs make bootstrap
```

It refuses a name that is not `wt` plus one letter, an existing path, branch or worktree, and prints what it
provisioned (session, branch, venv, schemas, data links, hooks). `make bootstrap` must complete in seconds
and be safe to re-run. It does: `uv sync`, verify `~/.config/spot/` exists and is populated, verify the
Postgres container is up (start it if not), link `data/raw` and `data/exports`, create `stg_wta` /
`mart_wta` if absent, install pre-commit hooks.

---

## 2. What is shared and what is not

| Resource | Scope | Location |
|---|---|---|
| Credentials, tokens | **Shared** | `~/.config/spot/` — outside every worktree |
| dbt profile | **Shared** | `~/.dbt/profiles.yml`, target schema from `SPOT_SESSION`. **`make dbt-*` / `scripts/dbt.sh` is the supported entry point** — it reads `.session` and `~/.config/spot/.env` and exports the variable. Bare `uv run dbt` has no `SPOT_SESSION` and there is deliberately no default (F5). |
| Postgres container | **Shared** | one container, port 5433, database `spot` |
| `raw` schema | **Shared, append-only** | written only by the extractor |
| `stg_<session>`, `mart_<session>` | **Per session** | dbt target |
| `analytics` schema | **`main` only** | promotion target |
| `data/raw/` JSON files | **Shared** | symlinked into each worktree from `~/spot-data/raw` |
| `data/exports/` export files | **Shared** | symlinked from `~/spot-data/exports`, one folder per profile (R-003) |
| Python venv | Per worktree | `.venv/`, created by `uv sync` |
| `published/` extracts | Per worktree | gitignored |

**`data/raw/` is a symlink, not a copy.** Three worktrees each holding a full copy of a decade of
export JSON for four people is wasteful and, worse, lets them drift. `make bootstrap` creates
`data/raw -> ~/spot-data/raw`.

---

## 3. The rules that keep sessions from breaking each other

**Only the extractor writes `raw`, and it holds a lock.** `spot refresh` acquires the Postgres
advisory lock `pg_advisory_lock(hashtext('spot_refresh'))` before its first API call and releases it
at the end. A second session attempting a refresh **blocks with a clear message** rather than
double-polling. Rate-limit quota is pooled per developer account across all client IDs — two sessions
polling in parallel will 429 each other.

**No session builds into `analytics` except `main`.** dbt's target schema comes from `SPOT_SESSION`;
the `analytics` target is a separate, explicitly-named dbt target that only exists in the profile's
`prod` entry, and `make promote` refuses to run when `.session` is not `main`.

**No session runs the launchd agent.** The scheduled poller is installed once, from `main`, and runs
against `~/projects/ai_orchestrator_claude/spotify` regardless of what worktrees exist. It uses the
same advisory lock.

**Migrations are ordered, not timestamped-and-hoped.** `raw` DDL lives in numbered migration files
applied by `make migrate`, which is idempotent and safe to run from any session. A session that adds
a raw table adds a migration; it does not `CREATE TABLE` from a script.

**Notebooks connect through the module, never a hardcoded string.** `from spotify_lakehouse.db import
connect; con = connect()` resolves the session, the schema, and the credentials. A notebook with a
connection string in it is a bug and, in a public repo, a leak.

---

## 4. Merging back

- Branch per worktree, PR into `main`. Squash merge.
- **dbt model conflicts are the real risk**, not Python conflicts. Two sessions adding models to the
  same mart will both edit `schema.yml`. Contract: **one `schema.yml` per model file**
  (`models/marts/fct_play_event.yml` alongside `fct_play_event.sql`), never a directory-wide file.
  This makes the conflict structurally impossible.
- After merge, every other worktree runs `git fetch && git rebase origin/main && make bootstrap`.
- When done, from `main`: `make worktree-remove NAME=wta`. It refuses a worktree holding uncommitted or
  untracked work, deletes `feat/wta` only if merged, and drops `stg_wta` / `mart_wta` (R-033).

---

## 5. Bootstrap contract

`make bootstrap` is the single entry point and must be safe to run in any state. It succeeds or it
prints exactly what is missing and how to fix it. Specifically it must detect and report, by name:

- `.session` missing or containing an unrecognized token
- `~/.config/spot/.env` missing or missing a required key
- Docker not running, or the container not healthy
- `~/.dbt/profiles.yml` missing the `spot` profile
- A `uv.lock` that does not match `pyproject.toml`

Each failure message says what to do, not just what is wrong.
