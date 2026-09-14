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

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

cd "$ROOT/dbt"
exec uv run dbt "$@"
