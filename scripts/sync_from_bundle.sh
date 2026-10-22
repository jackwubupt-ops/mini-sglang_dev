#!/usr/bin/env bash
# scripts/sync_from_bundle.sh BUNDLE
# Updates this server checkout from a git bundle copied in from the Windows machine
# Fallback for when the SSH tunnel (scripts/sync_from_github.sh) is unavailable. Never fetches from the network.
#   BUNDLE must be /home/xwc/vllm/transfer/mini-sglang_dev-<stamp>.bundle, owned by xwc, with
#   BUNDLE.sha256 (sha256sum format) and BUNDLE.refs (git for-each-ref "<sha> <ref>" lines)
#   written next to it on Windows.
# Checks: sha256, refs file == bundle heads, git bundle verify, object fsck on fetch, clean
# working tree, `upstream` and tag `baseline-v1` both at 9a91cfa. Then fetches heads into
# refs/remotes/bundle/* and moves local branches by fast-forward only (never resets, never
# moves a tag). New branches are created at the bundle's commit. DRY_RUN=1: verify only.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/check_env.sh
# shellcheck source=scripts/env.sh
source scripts/env.sh

BASELINE=9a91cfafe754aa85daee49998176275667eb58f2
TRANSFER=$MINISGL_ROOT/transfer
DRY_RUN=${DRY_RUN:-0}
die() { echo "REFUSED: $*" >&2; exit 1; }

[ $# -eq 1 ] || { echo "usage: $0 $TRANSFER/mini-sglang_dev-<stamp>.bundle" >&2; exit 2; }
BUNDLE=$1
case "$BUNDLE" in "$TRANSFER"/mini-sglang_dev-*.bundle) ;; *) die "bundle must be $TRANSFER/mini-sglang_dev-<stamp>.bundle";; esac
[ -f "$BUNDLE" ] && [ ! -L "$BUNDLE" ] || die "$BUNDLE is not a regular file"
[ -O "$BUNDLE" ] || die "$BUNDLE is not owned by $(id -un)"
for f in "$BUNDLE.sha256" "$BUNDLE.refs"; do [ -f "$f" ] || die "missing $f (copy it from Windows with the bundle)"; done
[ -z "$(git status --porcelain)" ] || die "uncommitted changes in $(pwd); commit nothing here, inspect them first"

echo "== 1. sha256"
(cd "$TRANSFER" && sha256sum -c "$(basename "$BUNDLE").sha256") || die "sha256 mismatch: copy the bundle again"

echo "== 2. heads in the bundle match the refs recorded on Windows"
diff <(git bundle list-heads "$BUNDLE" | tr -d '\r' | sort) <(tr -d '\r' < "$BUNDLE.refs" | grep -v '^$' | sort) \
  || die "bundle heads differ from $BUNDLE.refs"
git bundle list-heads "$BUNDLE"

echo "== 3. git bundle verify"
git bundle verify "$BUNDLE" > /dev/null || die "git bundle verify failed"

head_sha() { git bundle list-heads "$BUNDLE" | awk -v r="$1" '$2 == r {print $1}'; }
[ "$(head_sha refs/heads/upstream)" = "$BASELINE" ] || die "bundle's upstream is not 9a91cfa"
tag_obj=$(head_sha refs/tags/baseline-v1)
[ -n "$tag_obj" ] || die "bundle has no tag baseline-v1"

if [ "$DRY_RUN" = 1 ]; then echo "DRY-RUN: verified; would fetch and fast-forward"; exit 0; fi

echo "== 4. fetch (objects fsck'd; tags never overwritten)"
git -c transfer.fsckObjects=true fetch --no-tags "$BUNDLE" \
  '+refs/heads/*:refs/remotes/bundle/*' 'refs/tags/*:refs/tags/*'
[ "$(git rev-parse 'baseline-v1^{commit}')" = "$BASELINE" ] || die "tag baseline-v1 is not 9a91cfa"

echo "== 5. fast-forward local branches"
current=$(git branch --show-current)
for ref in $(git bundle list-heads "$BUNDLE" | awk '$2 ~ /^refs\/heads\// {print $2}'); do
  b=${ref#refs/heads/}
  if ! git show-ref --verify --quiet "refs/heads/$b"; then
    git branch "$b" "refs/remotes/bundle/$b" && echo "created $b"
  elif [ "$b" = "$current" ]; then
    git merge --ff-only "refs/remotes/bundle/$b" || die "$b cannot fast-forward; nothing changed on it"
  else
    git fetch . "refs/remotes/bundle/$b:refs/heads/$b" || die "$b cannot fast-forward; nothing changed on it"
  fi
done
[ "$(git rev-parse upstream)" = "$BASELINE" ] || die "local upstream is not 9a91cfa"

echo "== result (compare with the SHAs printed on the Mac)"
git for-each-ref --format='%(objectname) %(refname:short)' refs/heads refs/tags
echo "current branch: $current"
