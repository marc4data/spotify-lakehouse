#!/usr/bin/env bash
# Append the `spot` profile to ~/.dbt/profiles.yml if absent. Backs up the existing file first.
# The block contains no secrets: every credential is an env_var() lookup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILES="${DBT_PROFILES_DIR:-$HOME/.dbt}/profiles.yml"

mkdir -p "$(dirname "$PROFILES")"
if [[ -f "$PROFILES" ]] && grep -qE '^spot:' "$PROFILES"; then
  echo "'spot' profile already present in $PROFILES — nothing to do."
  exit 0
fi
if [[ -f "$PROFILES" ]]; then
  backup="$PROFILES.bak.$(date +%Y%m%d%H%M%S)"
  cp -p "$PROFILES" "$backup"
  echo "Backed up $PROFILES -> $backup"
  printf '\n' >> "$PROFILES"
fi
grep -vE '^#' "$ROOT/profiles.yml.example" >> "$PROFILES"
echo "Appended 'spot' profile to $PROFILES"
