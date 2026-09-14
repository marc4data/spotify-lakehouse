#!/usr/bin/env bash
# launchd entry point for `spot refresh` (spot-main-R-017).
# launchd does not rotate logs, so this does: refresh.log rolls at SPOT_LOG_MAX_BYTES, keeping 5.
# Every firing appends a start line, the poll's own output, and an exit line.
set -uo pipefail
umask 077

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${SPOT_LOG_DIR:-$HOME/.config/spot/logs}"
LOG="$LOG_DIR/refresh.log"
MAX_BYTES="${SPOT_LOG_MAX_BYTES:-1048576}"
KEEP=5
UV="${SPOT_UV:-uv}"

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"

if [[ -f "$LOG" ]] && (($(stat -f %z "$LOG") >= MAX_BYTES)); then
  for ((i = KEEP - 1; i >= 1; i--)); do
    if [[ -f "$LOG.$i" ]]; then mv "$LOG.$i" "$LOG.$((i + 1))"; fi
  done
  mv "$LOG" "$LOG.1"
fi

rc=0
{
  printf '%s agent firing (pid %s)\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$"
  "$UV" run --project "$REPO" --frozen --quiet spot refresh || rc=$?
  printf '%s agent exit=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rc"
} >>"$LOG" 2>&1
exit "$rc"
