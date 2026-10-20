#!/usr/bin/env bash
# scripts/sync_from_github.sh
# Updates this server checkout from the GitHub fork through the SSH RemoteForward tunnel
# (ps:127.0.0.1:17897 -> Windows Clash Verge 127.0.0.1:7897). Preferred over
# sync_from_bundle.sh once the tunnel is verified; bundles stay the fallback.
# The proxy is passed per command (git -c http.proxy=...), never stored in any git config,
# and TLS verification is never touched.
# Checks: user/path/env (check_env.sh), remote `origin` is exactly the fork URL, clean working
# tree, the tunnel listener on 127.0.0.1:17897 belongs to one of our own sshd processes,
# GitHub answers through the proxy, git ls-remote works. Then fetches heads into
# refs/remotes/origin/*, fetches tag baseline-v1 without ever overwriting it, and moves local
# branches by fast-forward only (never resets). `upstream` and baseline-v1 must be 9a91cfa.
# If the tunnel or proxy is down it stops; it never retries or falls back to direct GitHub.
# DRY_RUN=1: run every check (including ls-remote), change nothing.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/check_env.sh
# shellcheck source=scripts/env.sh
source scripts/env.sh

PROXY=http://127.0.0.1:17897
PROXY_PORT=17897
FORK=https://github.com/jackwubupt-ops/mini-sglang_dev.git
BASELINE=9a91cfafe754aa85daee49998176275667eb58f2
BRANCHES=(upstream tooling instr)
DRY_RUN=${DRY_RUN:-0}
die() { echo "STOP: $*" >&2; exit 1; }
pgit() { git -c "http.proxy=$PROXY" "$@"; }

url=$(git config --get remote.origin.url) \
  || die "no remote 'origin'. If this checkout came from a bundle, add it (non-destructive): git remote add origin $FORK"
[ "$url" = "$FORK" ] || die "origin is '$url', expected $FORK; inspect it, do not change it blindly"
[ -z "$(git status --porcelain)" ] || die "uncommitted changes in $(pwd); inspect them, commit nothing on ps"

echo "== 1. tunnel listener on 127.0.0.1:$PROXY_PORT"
scripts/check_gh_tunnel.sh || die "tunnel or proxy unavailable; reconnect VS Code Remote-SSH (RemoteForward) and check Clash Verge on Windows, or use the bundle fallback"

echo "== 2. git ls-remote through the tunnel"
heads=$(pgit ls-remote "$FORK" "${BRANCHES[@]/#/refs/heads/}" refs/tags/baseline-v1) \
  || die "git ls-remote through $PROXY failed; not retrying"
echo "$heads"
[ "$(awk '$2 == "refs/heads/upstream" {print $1}' <<< "$heads")" = "$BASELINE" ] || die "fork's upstream is not 9a91cfa"

if [ "$DRY_RUN" = 1 ]; then echo "DRY-RUN: checks passed; would fetch and fast-forward ${BRANCHES[*]}"; exit 0; fi

echo "== 3. fetch (objects fsck'd; baseline-v1 never overwritten)"
pgit -c transfer.fsckObjects=true fetch --no-tags origin '+refs/heads/*:refs/remotes/origin/*' \
  'refs/tags/baseline-v1:refs/tags/baseline-v1' || die "fetch failed (or baseline-v1 differs locally); nothing was fast-forwarded"
[ "$(git rev-parse 'baseline-v1^{commit}')" = "$BASELINE" ] || die "tag baseline-v1 is not 9a91cfa"

echo "== 4. fast-forward local branches"
current=$(git branch --show-current)
for b in "${BRANCHES[@]}"; do
  git show-ref --verify --quiet "refs/remotes/origin/$b" || { echo "skip $b (not on the fork)"; continue; }
  if ! git show-ref --verify --quiet "refs/heads/$b"; then
    git branch --track "$b" "origin/$b" && echo "created $b"
  elif [ "$b" = "$current" ]; then
    git merge --ff-only "origin/$b" || die "$b cannot fast-forward; nothing changed on it"
  else
    git fetch . "refs/remotes/origin/$b:refs/heads/$b" || die "$b cannot fast-forward; nothing changed on it"
  fi
done
[ "$(git rev-parse upstream)" = "$BASELINE" ] || die "local upstream is not 9a91cfa"

echo "== result (compare with the SHAs printed on the Mac)"
git for-each-ref --format='%(objectname) %(refname:short)' "${BRANCHES[@]/#/refs/heads/}" refs/tags/baseline-v1
echo "current branch: $current"
