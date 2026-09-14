#!/usr/bin/env bash
# make bootstrap — contract: docs/worktree-protocol.md §5.
# Idempotent and safe in any state. Succeeds, or names every failure and how to fix it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONFIG_DIR="${SPOT_CONFIG_DIR:-$HOME/.config/spot}"
ENV_FILE="$CONFIG_DIR/.env"
DATA_HOME="${SPOT_DATA_HOME:-$HOME/spot-data}"
DBT_PROFILES="${DBT_PROFILES_DIR:-$HOME/.dbt}/profiles.yml"
SPOTIFY_KEYS=(SPOTIFY_CLIENT_ID SPOTIFY_CLIENT_SECRET SPOTIFY_REDIRECT_URI)
PG_KEYS=(SPOT_PG_USER SPOT_PG_PASSWORD)

FAILURES=()
step() { printf '\n==> %s\n' "$1"; }
ok() { printf '    ok: %s\n' "$1"; }
# record NAME LINE... : remember a failure and keep going where later steps don't depend on it.
record() {
  local name="$1"; shift
  local msg="[$name]"
  for line in "$@"; do msg+=$'\n    '"$line"; done
  FAILURES+=("$msg")
  printf '    FAILED [%s]\n' "$name"
}
# stop NAME LINE... : a failure nothing after it can survive.
stop() { record "$@"; finish; }
finish() {
  if ((${#FAILURES[@]})); then
    printf '\nBOOTSTRAP INCOMPLETE — %d problem(s):\n' "${#FAILURES[@]}" >&2
    for f in "${FAILURES[@]}"; do printf '\n%s\n' "$f" >&2; done
    printf '\nFix the above, then re-run: make bootstrap\n' >&2
    exit 1
  fi
  printf '\nBootstrap complete for session "%s".\n' "$SESSION"
  exit 0
}
env_value() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d "\"'" || true; }

# --- 1. Session identity -----------------------------------------------------------------------
SESSION="(unknown)"
step "Session identity (.session)"
if [[ ! -f .session ]]; then
  stop session-missing \
    "No .session file in $ROOT." \
    "Fix: echo main > .session   # primary checkout" \
    "     echo wta > .session    # worktree spotify-wta (wtb, wtc... for others)"
fi
SESSION="$(tr -d '[:space:]' < .session)"
if [[ ! "$SESSION" =~ ^(main|wt[a-z])$ ]]; then
  stop session-unrecognized \
    ".session contains '$SESSION', which is not a recognized session token." \
    "Fix: overwrite it with main (primary checkout) or wta/wtb/... (worktree): echo wta > .session"
fi
if [[ "$SESSION" == "main" ]] && git rev-parse --git-dir >/dev/null 2>&1 \
  && [[ "$(git rev-parse --absolute-git-dir)" != "$(cd "$(git rev-parse --git-common-dir)" && pwd)" ]]; then
  stop session-unrecognized \
    ".session says 'main' but this checkout is a linked worktree." \
    "A worktree that thinks it is main would build into analytics. Fix: echo wta > .session"
fi
ok "session = $SESSION"

# --- 2. Lockfile ---------------------------------------------------------------------------------
step "uv.lock matches pyproject.toml"
command -v uv >/dev/null || stop uv-missing \
  "uv is not installed." "Fix: curl -LsSf https://astral.sh/uv/install.sh | sh"
if ! uv lock --check >/dev/null 2>&1; then
  stop lockfile-mismatch \
    "uv.lock does not match pyproject.toml (a dependency changed without re-locking)." \
    "Fix: run 'uv lock', then commit uv.lock together with the pyproject.toml change."
fi
uv sync --locked --quiet
ok "virtualenv synced from uv.lock"

# --- 3. Credentials ----------------------------------------------------------------------------
step "Credentials ($ENV_FILE)"
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
  pg_password="$(openssl rand -base64 36 | tr -dc 'A-Za-z0-9')"
  (umask 077 && sed "s/^SPOT_PG_PASSWORD=.*/SPOT_PG_PASSWORD=${pg_password}/" .env.example > "$ENV_FILE")
  printf '    created %s from .env.example (chmod 600, random Postgres password)\n' "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
[[ -f "$CONFIG_DIR/tokens.json" ]] && chmod 600 "$CONFIG_DIR/tokens.json"

missing_spotify=()
for key in "${SPOTIFY_KEYS[@]}"; do
  value="$(env_value "$key")"
  [[ -z "$value" || "$value" == *replace-me* ]] && missing_spotify+=("$key")
done
missing_pg=()
for key in "${PG_KEYS[@]}"; do
  value="$(env_value "$key")"
  [[ -z "$value" || "$value" == *replace-me* ]] && missing_pg+=("$key")
done
if ((${#missing_spotify[@]})); then
  record credentials-missing \
    "$ENV_FILE is missing required key(s): ${missing_spotify[*]}" \
    "Fix: 1. developer.spotify.com/dashboard -> your app -> Settings: copy Client ID and Client Secret" \
    "     2. edit $ENV_FILE and replace each replace-me value (it is chmod 600, outside the repo)" \
    "     3. confirm the dashboard Redirect URI is exactly http://127.0.0.1:3000"
else
  ok "Spotify keys present"
fi
if ((${#missing_pg[@]})); then
  record credentials-missing \
    "$ENV_FILE is missing Postgres key(s): ${missing_pg[*]}" \
    "Fix: set SPOT_PG_USER=spot and SPOT_PG_PASSWORD=<any strong value> in $ENV_FILE"
else
  ok "Postgres keys present"
fi

# --- 4. Docker / Postgres ----------------------------------------------------------------------
step "Postgres container (spot-postgres, port 5433)"
DB_UP=0
if ! docker info >/dev/null 2>&1; then
  record docker-not-running \
    "Docker is not running (or not installed)." \
    "Fix: open Docker Desktop and wait until it reports 'Engine running', then re-run make bootstrap."
elif ((${#missing_pg[@]})); then
  record container-unhealthy "Skipped starting Postgres: its credentials are missing (see above)."
elif ! docker compose --env-file "$ENV_FILE" up -d --wait --wait-timeout 90 postgres >/dev/null 2>&1; then
  record container-unhealthy \
    "spot-postgres did not reach 'healthy' within 90 seconds." \
    "Diagnose: docker compose --env-file $ENV_FILE logs postgres" \
    "Port clash? lsof -iTCP:5433 -sTCP:LISTEN   |   Stale container? docker rm -f spot-postgres"
else
  ok "spot-postgres healthy"
  DB_UP=1
fi

# --- 5. dbt profile ----------------------------------------------------------------------------
step "dbt profile ($DBT_PROFILES)"
if [[ -f "$DBT_PROFILES" ]] && grep -qE '^spot:' "$DBT_PROFILES"; then
  ok "'spot' profile present"
else
  record dbt-profile-missing \
    "$DBT_PROFILES has no 'spot' profile." \
    "Fix: make dbt-profile   # appends profiles.yml.example; backs up the existing file first"
fi

# --- 6. Shared raw data link -------------------------------------------------------------------
step "data/raw -> $DATA_HOME/raw"
mkdir -p "$DATA_HOME/raw"
chmod 700 "$DATA_HOME"
mkdir -p data
if [[ -L data/raw ]]; then
  if [[ "$(readlink data/raw)" == "$DATA_HOME/raw" ]]; then
    ok "symlink in place"
  else
    record data-raw-link "data/raw points at $(readlink data/raw), not $DATA_HOME/raw." \
      "Fix: rm data/raw && make bootstrap"
  fi
elif [[ -e data/raw ]]; then
  record data-raw-link "data/raw is a real directory; it must be a symlink so worktrees share one copy." \
    "Fix: mv data/raw/* $DATA_HOME/raw/ && rmdir data/raw && make bootstrap"
else
  ln -s "$DATA_HOME/raw" data/raw
  ok "symlink created"
fi

# --- 7. Migrations and session schemas ---------------------------------------------------------
step "raw migrations + stg_${SESSION} / mart_${SESSION}"
if ((DB_UP)); then
  if ! uv run --quiet spot migrate | sed 's/^/    /'; then
    record migrate-failed "spot migrate failed (output above)." "Fix: address the error, then make migrate"
  fi
else
  printf '    skipped: database not available\n'
fi

# --- 8. Pre-commit hooks -----------------------------------------------------------------------
step "pre-commit hooks"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  hook="$(git rev-parse --git-common-dir)/hooks/pre-commit"
  installed_python="$(grep -E '^INSTALL_PYTHON=' "$hook" 2>/dev/null | cut -d= -f2- || true)"
  # Hooks are shared by all worktrees. Keep an existing working install rather than re-pointing it
  # at this worktree's venv, which would break every other checkout when this worktree is removed.
  if [[ -n "$installed_python" && -x "$installed_python" ]]; then
    ok "already installed (shared across worktrees)"
  else
    uv run --quiet pre-commit install --install-hooks >/dev/null
    ok "installed"
  fi
else
  record not-a-git-repo "$ROOT is not a git checkout; hooks cannot be installed." \
    "Fix: clone the repo (or git init) and re-run make bootstrap"
fi

finish
