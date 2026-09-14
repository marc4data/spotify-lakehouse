#!/usr/bin/env bash
# Scan the repository's FULL git history with a pinned, checksum-verified gitleaks (spot-main-R-020).
#
# Called by .github/workflows/ci.yml. Runs identically on a laptop through Docker:
#   docker run --rm --platform linux/amd64 -v "$PWD:/repo" -w /repo buildpack-deps:24.04-scm \
#     bash -c 'git config --global --add safe.directory /repo && scripts/ci/gitleaks_history.sh'
#
# Output policy: Actions logs on a public repo are public. No --verbose, and --redact, so the log carries
# the finding count and never a matched value.
set -euo pipefail

# Keep equal to the gitleaks `rev` in .pre-commit-config.yaml so the hook and CI apply the same rules.
GITLEAKS_VERSION="8.30.0"
GITLEAKS_LINUX_X64_SHA256="79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e"
REPO="${1:-.}"
# Rules come from this checkout's .gitleaks.toml (defaults + spot-hex32), whichever repo is scanned.
CONFIG="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.gitleaks.toml"

if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG — without it gitleaks falls back to default rules, which miss Spotify IDs." >&2
  exit 2
fi
if [[ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]]; then
  echo "gitleaks_history.sh expects Linux x86_64 (got $(uname -s)-$(uname -m))." >&2
  exit 2
fi
if [[ "$(git -C "$REPO" rev-parse --is-shallow-repository)" == "true" ]]; then
  echo "Refusing to scan a shallow clone: history would be incomplete. Use fetch-depth: 0." >&2
  exit 2
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
tarball="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
curl -fsSL -o "$workdir/$tarball" \
  "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${tarball}"
echo "${GITLEAKS_LINUX_X64_SHA256}  $workdir/$tarball" | sha256sum --check --strict
tar -xzf "$workdir/$tarball" -C "$workdir" gitleaks
"$workdir/gitleaks" version

echo "commits reachable from any ref: $(git -C "$REPO" rev-list --all --count)"
# Scope: every commit reachable from every ref, never a range. --log-opts REPLACES gitleaks' default git
# arguments rather than adding to them, so this is v8.30.0's own default (read from --log-level=debug)
# written out in full. That pins the scope even if a future version changes its default.
"$workdir/gitleaks" git "$REPO" \
  --config "$CONFIG" \
  --log-opts="--full-history --all --diff-filter=tuxdb" \
  --redact \
  --no-banner \
  --no-color \
  --exit-code 1
