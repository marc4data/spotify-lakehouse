#!/usr/bin/env bash
# make worktree NAME=<token> / make worktree-remove NAME=<token>  (spot-main-R-033)
#
# add:    git worktree add ../spotify-<token> -b feat/<token> from main, write .session, run make bootstrap
#         inside it, then print what was provisioned. Nothing else is configured by hand.
# remove: refuse if the worktree holds real uncommitted or untracked work, remove it, delete the branch if
#         it is merged into main, and drop stg_<token> / mart_<token>.
set -euo pipefail

ACTION="${1:-}"
NAME="${2:-}"
# Declared in the round contract (~/projects/ai_orchestrator_claude/spotify-pm/round-contract.md).
PM_DIR="$HOME/projects/ai_orchestrator_claude/spotify-pm"

die() {
  printf 'worktree: %s\n' "$*" >&2
  exit 1
}

MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
WT_PATH="$(dirname "$MAIN_ROOT")/$(basename "$MAIN_ROOT")-$NAME"
BRANCH="feat/$NAME"

validate_name() {
  [[ -n "$NAME" ]] || die "NAME is required, e.g. make worktree NAME=wtc"
  [[ "$NAME" =~ ^(main|wt[a-z])$ ]] ||
    die "'$NAME' is not a session token. Use wt plus one lowercase letter: wta, wtb, … wtz."
  [[ "$NAME" != "main" ]] ||
    die "'main' is the primary checkout ($MAIN_ROOT), not a worktree. Use wta, wtb, … wtz."
}

is_registered_worktree() {
  git -C "$MAIN_ROOT" worktree list --porcelain | grep -qxF "worktree $WT_PATH"
}

case "$ACTION" in
  add)
    validate_name
    [[ ! -e "$WT_PATH" ]] || die "$WT_PATH already exists. Remove it first: make worktree-remove NAME=$NAME"
    ! is_registered_worktree || die "a worktree is already registered at $WT_PATH"
    ! git -C "$MAIN_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH" ||
      die "branch $BRANCH already exists. Delete it or choose another name."

    git -C "$MAIN_ROOT" worktree add "$WT_PATH" -b "$BRANCH" main
    printf '%s\n' "$NAME" >"$WT_PATH/.session"
    if ! (cd "$WT_PATH" && make bootstrap); then
      die "make bootstrap failed in $WT_PATH (output above). The worktree was kept: fix the named problem and re-run 'make bootstrap' there, or remove it with 'make worktree-remove NAME=$NAME'."
    fi

    echo
    echo "==> Provisioned $WT_PATH"
    (
      cd "$WT_PATH"
      printf '    session      %s (from .session)\n' "$(tr -d '[:space:]' <.session)"
      printf '    branch       %s\n' "$(git rev-parse --abbrev-ref HEAD)"
      printf '    venv         %s\n' "$(uv run --quiet python -c 'import sys; print(sys.executable)')"
      uv run --quiet python - <<'PY'
from spotify_lakehouse.config import mart_schema, stg_schema
from spotify_lakehouse.db import connect

with connect() as conn:
    present = {
        row[0]
        for row in conn.execute(
            "select nspname from pg_namespace where nspname = any(%s)",
            ([stg_schema(), mart_schema()],),
        )
    }
for schema in (stg_schema(), mart_schema()):
    print(f"    schema       {schema}: {'present' if schema in present else 'MISSING'}")
PY
      printf '    data/raw     -> %s\n' "$(readlink data/raw)"
      hook="$(git rev-parse --path-format=absolute --git-common-dir)/hooks/pre-commit"
      printf '    hooks        %s\n' "$([[ -x "$hook" ]] && echo "installed (shared): $hook" || echo MISSING)"
      if [[ -d "$PM_DIR/prompts" ]]; then
        printf '    PM folder    %s (%s prompts, read by absolute path)\n' \
          "$PM_DIR" "$(find "$PM_DIR/prompts" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')"
      else
        printf '    PM folder    %s NOT FOUND\n' "$PM_DIR"
      fi
    )
    echo
    echo "Open it in a new VS Code window: code -n $WT_PATH"
    ;;

  remove)
    validate_name
    is_registered_worktree || die "no worktree is registered at $WT_PATH"
    [[ "$(git rev-parse --show-toplevel)" != "$WT_PATH" ]] ||
      die "run this from another checkout (e.g. $MAIN_ROOT), not from inside $WT_PATH"
    changes="$(git -C "$WT_PATH" status --porcelain)"
    [[ -z "$changes" ]] || die "$WT_PATH has uncommitted or untracked work — commit, stash or delete it first:
$changes"

    # Plain remove, never --force: git refuses untracked files but not gitignored ones (measured R-033).
    git -C "$MAIN_ROOT" worktree remove "$WT_PATH"
    echo "removed worktree $WT_PATH"
    if git -C "$MAIN_ROOT" branch -d "$BRANCH" >/dev/null 2>&1; then
      echo "deleted branch $BRANCH (merged into main)"
    else
      echo "kept branch $BRANCH: it is not merged into main (delete it deliberately with git branch -D)"
    fi
    (cd "$MAIN_ROOT" && uv run --quiet spot drop-session-schemas "$NAME")
    ;;

  *)
    die "usage: scripts/worktree.sh add|remove <wta…wtz>"
    ;;
esac
