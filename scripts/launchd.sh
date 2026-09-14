#!/usr/bin/env bash
# make launchd-install / make launchd-uninstall (spot-main-R-017).
# One user agent, installed from the main checkout only (docs/worktree-protocol.md §3). The rendered
# plist and the logs live outside the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="local.spot.refresh"
TEMPLATE="$ROOT/launchd/$LABEL.plist.template"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="${SPOT_CONFIG_DIR:-$HOME/.config/spot}/logs"
DOMAIN="gui/$(id -u)"

case "${1:-}" in
  install)
    session="$(tr -d '[:space:]' <"$ROOT/.session" 2>/dev/null || true)"
    if [[ "$session" != "main" ]]; then
      echo "launchd-install refused: .session is '${session:-missing}'." >&2
      echo "The poller is installed once, from the main checkout (docs/worktree-protocol.md §3)." >&2
      exit 1
    fi
    uv_path="$(command -v uv)" || {
      echo "launchd-install: uv not found on PATH" >&2
      exit 1
    }
    mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"
    chmod 700 "$LOG_DIR"
    sed -e "s|__REPO__|$ROOT|g" -e "s|__UV__|$uv_path|g" -e "s|__LOG_DIR__|$LOG_DIR|g" \
      "$TEMPLATE" >"$PLIST.tmp"
    plutil -lint "$PLIST.tmp" >/dev/null
    mv "$PLIST.tmp" "$PLIST"
    chmod 644 "$PLIST"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && sleep 1 || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed $PLIST and loaded $DOMAIN/$LABEL."
    echo "It fires now (RunAtLoad), then every 30 minutes. Log: $LOG_DIR/refresh.log"
    echo "Check it with: uv run spot refresh --status"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Unloaded $DOMAIN/$LABEL and removed $PLIST (logs kept in $LOG_DIR)."
    ;;
  *)
    echo "usage: scripts/launchd.sh install|uninstall" >&2
    exit 2
    ;;
esac
