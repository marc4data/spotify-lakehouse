#!/usr/bin/env bash
# Run dbt with SPOT_SESSION from .session and Postgres credentials from ~/.config/spot/.env.
# Usage: scripts/dbt.sh parse | build | ...
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SPOT_CONFIG_DIR:-$HOME/.config/spot}/.env"

if [[ ! -f "$ROOT/.session" ]]; then
  echo "dbt: .session is missing in $ROOT. Fix: echo main > .session (or wta...), then make bootstrap" >&2
  exit 1
fi
SPOT_SESSION="$(tr -d '[:space:]' < "$ROOT/.session")"
export SPOT_SESSION

# Read only the Postgres keys dbt needs, as plain text. Never `source` the credentials file: it is
# data, not shell, and a value with a stray space would be executed (and echoed) as a command.
# Spotify credentials are deliberately not exported to dbt at all.
env_get() {
  [[ -f "$ENV_FILE" ]] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n 1 \
    | sed -e 's/[[:space:]]*$//' -e 's/^["'\'']//' -e 's/["'\'']$//'
}
for key in SPOT_PG_USER SPOT_PG_PASSWORD SPOT_PG_HOST SPOT_PG_PORT; do
  value="$(env_get "$key")"
  if [[ -n "$value" ]]; then
    export "$key=$value"
  fi
done

# dim_profile reads spot_meta.profile_registry; refresh it from ~/.config/spot/profiles.csv before any
# command that builds or tests models, so the registry is never stale relative to the file.
case "${1:-}" in
  build | run | test | seed)
    (cd "$ROOT" && uv run --quiet spot sync-profiles)
    ;;
esac

cd "$ROOT/dbt"
exec uv run dbt "$@"
