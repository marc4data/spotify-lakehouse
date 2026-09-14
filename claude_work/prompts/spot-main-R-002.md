# spot-main-R-002 — Scaffold, auth, and one verified API call

**Session: `main`. Read `CLAUDE.md` and `docs/data-contracts.md` before starting.**

## Objective

Stand up the repository so that a second VS Code worktree can be created and be fully operational
with `make bootstrap`, and prove the Spotify connection works end to end by writing one real API
response to `data/raw/` and one row to Postgres.

**This round does no modeling.** No dbt models beyond a parse-clean empty project. No star schema.
The point is that the plumbing is real before anything is built on it.

## Scope

1. **Repo skeleton** — `pyproject.toml` (uv, Python 3.12), package `spotify_lakehouse/`, `tests/`,
   `dbt/`, `notebooks/`, `docs/` (exists), `claude_work/` (exists), `seeds/`, `.gitignore`,
   `README.md`, `LICENSE` (MIT).

2. **`make bootstrap`** — meeting the contract in `docs/worktree-protocol.md` §5. Named,
   actionable failure messages for each of the five listed conditions.

3. **Docker Postgres 16** — `docker-compose.yml`, host port 5433, database `spot`, healthcheck.
   `make db-up` / `make db-down`.

4. **Config module** — `spotify_lakehouse.config`. Reads `.session` (raises if missing or
   unrecognized), loads `~/.config/spot/.env`, exposes session-scoped schema names. **No default to
   `main`.**

5. **Secrets scaffolding** — `.env.example`, `profiles.yml.example`, `~/.config/spot/` creation in
   bootstrap with `chmod 600`. Pre-commit config with `gitleaks`, `nbstripout`, `ruff`, and the
   custom 32-hex-string hook from `CLAUDE.md` §5.

6. **OAuth Authorization Code flow** — `spot auth <profile-slug>`. Local redirect at
   `http://127.0.0.1:3000`, scopes from `CLAUDE.md`/the access guide, refresh token written to
   `~/.config/spot/tokens.json` keyed by profile slug, `chmod 600`. Automatic refresh on expiry.
   Run it for `marc` only this round.

7. **`raw` schema + first migration** — `make migrate`, idempotent, numbered files. One table
   `raw.api_response` per the contract shape in `docs/data-contracts.md` §1.

8. **One verified call** — `spot probe --profile marc` calls `GET /me` and `GET /me/player/recently-played`,
   writes the raw JSON to `data/raw/`, inserts into `raw.api_response`, and prints a redacted summary.
   **Never print or log the email from `/me`.**

9. **Empty dbt project** — `dbt/`, `dbt_project.yml`, target schema from `SPOT_SESSION`,
   `profiles.yml.example`. `uv run dbt parse` must succeed. One `.yml` per model file convention
   documented in the project README.

10. **CI** — GitHub Actions: ruff, pytest, gitleaks, `dbt parse`. No credentials, no live API calls.

## Acceptance

- `make bootstrap` succeeds from a clean clone with `~/.config/spot/` absent, printing the exact
  missing-credential message.
- After credentials are placed, `make bootstrap && spot auth marc && spot probe --profile marc`
  writes JSON to `data/raw/` and a row to `raw.api_response`.
- `git worktree add ../spotify-wta -b feat/probe && echo wta > ../spotify-wta/.session &&
  cd ../spotify-wta && make bootstrap` succeeds and `spot probe` works there **using the same
  credentials without any additional setup.** Demonstrate this; it is the whole point of the round.
- A staged commit containing a fake 32-hex string is **rejected by the hook.** Report the hook name
  and the rejection output — the guard must be proven to fire, per `CLAUDE.md` §7.
- A notebook with a populated output cell is stripped by `nbstripout` on commit. Prove it.
- `uv run pytest`, `uv run ruff check .`, `uv run dbt parse` all pass.

## Report

Write `claude_work/reports/spot-main-R-002-report.md`: files created, the two proven-guard
transcripts, the worktree demonstration, the shape of the `/me` and `recently-played` responses as
actually observed versus documented, and anything in the contracts that turned out wrong.

## Constraints

- **Do not amend a contract.** If `docs/data-contracts.md` conflicts with reality, stop and report.
- **Do not commit anything under `data/`, `reports/`, or `published/`.**
- **Do not attempt** `/audio-features`, `/audio-analysis`, `/recommendations`, or
  `/artists/{id}/related-artists` — they are dead for this app.
- Rate limits are a 30-second rolling window with an unpublished dev-mode ceiling. Sleep between
  calls; honor `Retry-After`.
