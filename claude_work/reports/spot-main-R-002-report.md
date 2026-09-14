# spot-main-R-002 — Build report

**Session:** `main` · **Date:** 2026-09-13 · **Commit:** `3a499cf` (scaffold) + this report
**Status for Cowork:** everything except the credential-dependent acceptance is built and proven. The live
OAuth login and the first real API call are **blocked on Marc** (see §6). Mark ❓, not ✅.

---

## 1. Acceptance scorecard

| Acceptance item | Result | Evidence |
|---|---|---|
| `make bootstrap` from clean state with `~/.config/spot/` absent prints the exact missing-credential message | ✅ | §3.1, §3.2 |
| `make bootstrap && spot auth marc && spot probe --profile marc` writes JSON + a row | ⛔ **Blocked** — no Spotify credentials exist on this machine; `spot auth` needs Marc's browser login | §6 |
| Worktree `spotify-wta` bootstraps and uses the same credentials with no additional setup | ✅ for everything credentials don't gate (same `.env`, same container, same `data/raw`, own schemas). `spot probe` there fails with the **same** shared-credential message as `main`, which shows it reads the shared config. A successful probe there waits on §6 | §4 |
| Staged fake 32-hex string rejected by the hook | ✅ rejected by **two** hooks: `spot-no-spotify-secrets` and `gitleaks` | §2.1 |
| Notebook with populated output stripped on commit | ✅ `nbstripout` | §2.2 |
| `uv run pytest`, `uv run ruff check .`, `uv run dbt parse` pass | ✅ 64 passed · ruff clean · parse 0 errors (see §5 note on how dbt is invoked) | §2.3 |

---

## 2. Proven guards (CLAUDE.md §7)

### 2.1 32-hex string → commit rejected

Staged `scratch_guard/leak_test.env.txt` containing `SPOTIFY_CLIENT_SECRET=<openssl rand -hex 16>`.
The value is redacted below. The hook output itself never prints the matched value.

```
Detect hardcoded secrets........................................................Failed
- hook id: gitleaks
Finding:     SPOTIFY_CLIENT_SECRET=REDACTED
RuleID:      generic-api-key
File:        scratch_guard/leak_test.env.txt
WRN leaks found: 1

spot — reject 32-hex strings and personal email addresses.......................Failed
- hook id: spot-no-spotify-secrets
- exit code: 1

scratch_guard/leak_test.env.txt:1: 32-hex string (Spotify client ID / secret shape)

Commit rejected by spot-no-spotify-secrets. Credentials and account identifiers belong in ~/.config/spot/, never in the repo (CLAUDE.md §5).
git commit exit=1
HEAD still 3a499cf
```

### 2.2 Notebook outputs → stripped on commit

```
before commit  -> outputs: 1 | execution_count: 7
nbstripout......................................................................Failed
- hook id: nbstripout
- files were modified by this hook
git commit exit=1
after hook     -> outputs: 0 | execution_count: None
index after re-add: 1 empty outputs array(s), 0 output text line(s)
```

The proof notebook was unstaged and deleted afterwards. Nothing was committed.

### 2.3 Staged breaks → named tests red → reverted

| Break | Test that went red | Reverted |
|---|---|---|
| Emptied `DISCARDED_FIELDS` in [raw_store.py](../../spotify_lakehouse/raw_store.py), so `/me` email would be persisted | `tests/test_raw_store.py::test_scrub_removes_email_from_me_without_mutating_input` — 1 failed, 4 passed | ✅ file restored, `git status` clean |
| `alter table raw.api_response disable trigger api_response_no_update_delete` | `tests/test_migrate.py::test_raw_api_response_is_append_only` — `Failed: DID NOT RAISE RaiseException` | ✅ trigger re-enabled (`tgenabled = O` for both triggers); `raw.api_response` has 0 rows, since the test rolls back |

After the reverts: `64 passed`, `ruff check` clean, `ruff format --check` clean, `dbt parse` 0 errors.

---

## 3. Bootstrap contract (worktree-protocol §5)

### 3.1 First run in `main`, `~/.config/spot/` absent

Bootstrap created `~/.config/spot/` (mode 700) and `.env` (mode 600, with a random Postgres password), then
kept going. It started Postgres, ran migrations, linked `data/raw`, and installed hooks. It exits non-zero
with:

```
[credentials-missing]
    /Users/marcalexander/.config/spot/.env is missing required key(s): SPOTIFY_CLIENT_ID SPOTIFY_CLIENT_SECRET
    Fix: 1. developer.spotify.com/dashboard -> your app -> Settings: copy Client ID and Client Secret
         2. edit /Users/marcalexander/.config/spot/.env and replace each replace-me value (it is chmod 600, outside the repo)
         3. confirm the dashboard Redirect URI is exactly http://127.0.0.1:3000
```

### 3.2 Each named failure, from a fresh `git clone` in a scratch directory

| Condition | Name printed | Fix printed |
|---|---|---|
| No `.session` | `[session-missing]` | `echo main > .session` / `echo wta > .session` |
| `.session` = `prod` | `[session-unrecognized]` | overwrite with `main` or `wta/wtb/...` |
| `.session` = `main` inside a linked worktree | `[session-unrecognized]` | extra guard: a worktree claiming `main` would build into `analytics` |
| `pyproject.toml` edited without re-locking | `[lockfile-mismatch]` | `uv lock`, commit together |
| `SPOT_CONFIG_DIR` empty | `[credentials-missing]` | as above (dir 700 / file 600 verified) |
| Docker unreachable (`DOCKER_HOST` pointed at a dead socket) | `[docker-not-running]` | open Docker Desktop, re-run |
| Container not healthy in 90 s | `[container-unhealthy]` | `docker compose logs`, port-5433 check (code path, not staged) |
| `profiles.yml` without `spot:` | `[dbt-profile-missing]` | `make dbt-profile` |

Failures that later steps don't depend on are collected and all reported together. Session and
lockfile failures stop the run immediately.

---

## 4. Worktree demonstration

```
$ git worktree add ../spotify-wta -b feat/probe && echo wta > ../spotify-wta/.session && cd ../spotify-wta && make bootstrap
    ok: session = wta
    ok: virtualenv synced from uv.lock
    FAILED [credentials-missing]          <- same shared ~/.config/spot/.env as main
    ok: Postgres keys present
    ok: spot-postgres healthy             <- same container, not a second one
    ok: 'spot' profile present
    ok: symlink created                   <- data/raw -> ~/spot-data/raw
    migrations applied: none pending      <- raw is shared; main already applied 0001
    session schemas present: stg_wta, mart_wta
    ok: already installed (shared across worktrees)

$ uv run python -c "...config..."
session wta | stg_wta mart_wta | repo_root spotify-wta | db 127.0.0.1 5433 spot | config_dir /Users/marcalexander/.config/spot

$ uv run spot probe --profile marc
spot probe: /Users/marcalexander/.config/spot/.env is missing required key(s): SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET. ...

$ docker ps  ->  spot-postgres  Up (healthy)  127.0.0.1:5433->5432/tcp      (exactly one)
$ schemas    ->  mart_main mart_wta public raw spot_meta stg_main stg_wta
$ git worktree list
/Users/marcalexander/projects/ai_orchestrator_claude/spotify      3a499cf [main]
/Users/marcalexander/projects/ai_orchestrator_claude/spotify-wta  3a499cf [feat/probe]
```

The worktree reached parity with `main` using `.session` and `make bootstrap` alone. The one remaining
failure is the same one `main` has. Once Marc fills the shared `.env`, both clear together.

---

## 5. Files created

| Area | Files |
|---|---|
| Project | `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `README.md`, `LICENSE` (MIT), `Makefile` |
| Secrets scaffolding | `.env.example`, `profiles.yml.example`, `.pre-commit-config.yaml`, `scripts/hooks/check_secrets.py` |
| Bootstrap / ops | `scripts/bootstrap.sh`, `scripts/dbt.sh`, `scripts/install_dbt_profile.sh`, `docker-compose.yml` |
| Package | `spotify_lakehouse/{config,auth,api,db,migrate,raw_store,redact,shape,cli}.py` |
| Database | `migrations/0001_raw_api_response.sql` (table + append-only triggers on update/delete/truncate) |
| dbt | `dbt/dbt_project.yml`, `dbt/macros/generate_schema_name.sql`, empty `models/{staging,intermediate,marts}/` |
| CI | `.github/workflows/ci.yml` — ruff, ruff format, pytest, custom secret scan over tracked files, dbt parse with the template profile, gitleaks-action |
| Tests | `tests/test_{config,auth,api,raw_store,redact,check_secrets,migrate}.py` |
| Placeholders | `notebooks/`, `seeds/`, `dbt/tests/` (`.gitkeep`) |

**Changed outside the repo:** `~/.config/spot/.env` (created, 600) · `~/spot-data/raw/` (created, 700) ·
`~/.dbt/profiles.yml` (`spot` profile appended; backup at `profiles.yml.bak.20260913183838`) · Docker container
`spot-postgres` and volume `spot_pgdata` · worktree `../spotify-wta` on branch `feat/probe`.

**Engineering decisions worth knowing** (Claude Code's call, listed for visibility):

- **dbt invocation.** The profile reads `SPOT_SESSION` and the Postgres credentials from the environment, and
  nothing sets those for a bare `uv run dbt`. The supported form is `make dbt-parse` / `scripts/dbt.sh <cmd>`,
  which reads `.session` and `~/.config/spot/.env`. There is deliberately no `SPOT_SESSION` default.
- **gitleaks** uses the upstream Go-source hook, which pre-commit provisions itself. No system install, and it
  works inside worktrees, where a Docker bind mount cannot see the shared `.git`.
- **Pre-commit hooks are shared by all worktrees.** Bootstrap keeps an existing working install rather than
  re-pointing it at the current worktree's venv, so removing a worktree doesn't break `main`'s hooks.
- **`spot probe` takes the `spot_refresh` advisory lock**, the same lock `spot refresh` will use.
- **`spot auth` forces Spotify's account chooser** (`show_dialog=true`). It also refuses to bind one Spotify
  account to two profile slugs, which catches "Marie logged in while the browser was still signed in as Marc".
- **The email hook** blocks personal-mail domains by default. Family domains can be added through
  `SPOT_BLOCKED_EMAIL_DOMAINS` in `~/.config/spot/.env`, so the list itself never enters the public repo.
- Session tokens are validated as `main` or `wt` + one lowercase letter.

---

## 6. Blocked on Marc — to finish acceptance

1. Paste the Client ID and Client Secret into `~/.config/spot/.env`. Confirm the dashboard Redirect URI is exactly
   `http://127.0.0.1:3000`.
2. `make bootstrap` (should now print `Bootstrap complete for session "main"`).
3. `uv run spot auth marc` → log in in the browser.
4. `uv run spot probe --profile marc --shape` in `main`, then the same in `../spotify-wta`.

(`spot` is installed into the project venv, so the commands are `uv run spot ...`, not bare `spot ...`.)

Step 4's `--shape` output prints key paths and types only, never values. It fills §7's "observed" column.

**GitHub:** `marc4data/spotify-lakehouse` does not exist yet (`gh repo view` → not found). Nothing was pushed, so
CI has not run on GitHub. Creating the public repo is Marc's call.

---

## 7. `/me` and `recently-played` — observed vs documented

**Observed: not yet.** No call reached Spotify this round (see §6). Documented shape, from Spotify's reference:

| Endpoint | Documented top-level fields |
|---|---|
| `GET /me` | `country`, `display_name`, `email`, `explicit_content{filter_enabled,filter_locked}`, `external_urls`, `followers{href,total}`, `href`, `id`, `images[]`, `product`, `type`, `uri` |
| `GET /me/player/recently-played` | `href`, `limit`, `next`, `cursors{after,before}`, `total`, `items[]{track, played_at, context}`; `context` may be null |

`spot probe --shape` records the real shape. Some `/me` fields (`email`, `country`, `product`) depend on scopes
and possibly on dev-mode policy, and the probe summary reports each as present or not returned. That also
bears on R-016 (`product` shows Premium status).

---

## 8. Contract findings — not amended; Cowork to decide

| # | Where | Finding | Proposed amendment |
|---|---|---|---|
| F1 | Prompt §8 vs data-contracts §3 | The prompt says to write "the raw JSON" of `/me` to `data/raw/` and `raw.api_response`. §3 says "**email is never stored.** The extractor reads it from `/me` … and discards it." The two conflict. **Built to the contract:** `email` is removed before anything is persisted, and the probe reports only `email: present -> discarded`. | Confirm, and add a line to §1 that `raw` payloads are as received **minus contract-discarded fields**, so "raw" and "immutable" aren't read as "unscrubbed". |
| F2 | data-contracts §1 | §1 says **"One table per source feed"**, but the prompt names a single `raw.api_response` for both endpoints. The contract's five-column shape has no feed column, so the feed can be recovered only from the `source_file` path (`api/me/marc/…` vs `api/me_player_recently-played/marc/…`). Built as the prompt specified. Staging will have to parse a path to split feeds, which is fragile. | Either one raw table per endpoint (`raw.api_me`, `raw.api_recently_played`, …), or add `feed text not null` to the §1 raw shape. |
| F3 | data-contracts §1 vs §3 | `raw.*.profile_key` is `text` (holds the slug, `marc`), while `dim_profile.profile_key` is an int surrogate. One name, two meanings, and the staging join will be confusing. | Rename the raw column `profile_slug`. |
| F4 | Prompt acceptance #1 | "`make bootstrap` **succeeds** … with `~/.config/spot/` absent, **printing the missing-credential message**" contradicts itself: a missing credential can't be both a success and a reported failure. Built as: every step that doesn't need Spotify completes, then the script exits non-zero naming `credentials-missing`. | Reword to "completes all non-credential steps and fails naming the missing keys." |
| F5 | worktree-protocol §2 | "target schema from `SPOT_SESSION` env" doesn't say who sets the variable. Solved with `scripts/dbt.sh`; bare `uv run dbt` requires it exported. | Name `make dbt-*` / `scripts/dbt.sh` as the supported entry point. |
| F6 | `docs/spotify-data-access-guide.md` | "Last verified … **September 14, 2026**" is one day in the future relative to this round (2026-09-13). | Correct the date. |

---

## 9. Definition-of-done check (CLAUDE.md §7)

| Item | State |
|---|---|
| `uv run pytest` | ✅ 64 passed (DB integration tests ran against live `spot-postgres`) |
| `uv run dbt build` | n/a — no models this round by instruction; `dbt parse` ✅ |
| `ruff check` / `ruff format --check` | ✅ |
| Pre-commit on staged files, gitleaks included | ✅ all hooks passed on `3a499cf` |
| A guard proven to fail | ✅ §2 (four guards) |
| Build report | this file |
