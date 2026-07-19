#!/bin/sh
# Woodpecker cron driver: run one updater lane; on a bump, push a branch
# and open an auto-merging PR against the right base branch.
# Lanes: stable | rc  (network product)   uos  (unifi-os product)
# Hostname-free: all endpoints come from Woodpecker's CI_* variables.
# Requires: git, curl, jq, python3; secret FORGEJO_TOKEN.
set -eu

lane=$1
api="${CI_FORGE_URL}/api/v1"
repo="${CI_REPO}"
auth="Authorization: token ${FORGEJO_TOKEN}"

git config --global --add safe.directory "$(pwd)"
git config user.name  "unifi-containers updater"
git config user.email "noreply@loreland.org"

case "$lane" in
  stable|rc)
    out=$(python3 scripts/unifi-network-updater.py --channel "$lane" --write)
    prefix="network"
    files="network/Dockerfile README.md"
    ;;
  uos)
    out=$(python3 scripts/unifi-os-updater.py --write)
    prefix="unifi-os"
    files="unifi-os/pins.env README.md"
    ;;
  *) echo "unknown lane: $lane" >&2; exit 1 ;;
esac
echo "$out"
case "$out" in
  up-to-date*) exit 0 ;;
esac
version=$(printf '%s\n' "$out" | tail -1)   # e.g. 10.4.57, 10.5.62-rc, 5.2.0
semver=${version%-rc}

base=main
if [ "$lane" = "rc" ]; then
  base=rc
  # Skip if the rc lane already carries this version.
  current_rc=$(curl -fsS -H "$auth" "${api}/repos/${repo}/raw/rc/network/Dockerfile" 2>/dev/null \
    | sed -n 's#^ARG PKGURL=.*/unifi/\([0-9.]*\)/.*#\1#p' || true)
  if [ "${current_rc:-}" = "$semver" ]; then
    echo "rc lane already at ${semver}; nothing to do"
    exit 0
  fi
  # The rc branch is disposable: recreate it from current main for each RC.
  curl -fsS -X DELETE -H "$auth" "${api}/repos/${repo}/branches/rc" >/dev/null 2>&1 || true
  curl -fsS -X POST -H "$auth" -H 'Content-Type: application/json' \
    -d '{"new_branch_name":"rc","old_ref_name":"main"}' \
    "${api}/repos/${repo}/branches" >/dev/null
fi

branch="bump/${prefix}-${version}"
git checkout -b "$branch"

# Network bumps also refresh the sim-key drift tripwire from the new deb.
if [ "$prefix" = "network" ]; then
  ./scripts/enumerate-sim-keys.sh "$semver" && git add docs/sim-keys/ || \
    echo "warning: sim-key enumeration failed; bump proceeds without it" >&2
fi

# shellcheck disable=SC2086
git add $files
git commit -m "${prefix}: bump to ${version}"

push_url=$(printf '%s' "$CI_REPO_CLONE_URL" | sed "s#https://#https://oauth2:${FORGEJO_TOKEN}@#")
git push -f "$push_url" "HEAD:refs/heads/${branch}"

pr=$(curl -sS -X POST -H "$auth" -H 'Content-Type: application/json' \
  -d "{\"base\":\"${base}\",\"head\":\"${branch}\",\"title\":\"${prefix}: bump to ${version}\",\"body\":\"Automated update (${lane} lane).\"}" \
  "${api}/repos/${repo}/pulls" | jq -r '.number // empty')
if [ -z "$pr" ]; then
  # PR may already exist from an earlier run; find it.
  pr=$(curl -fsS -H "$auth" "${api}/repos/${repo}/pulls?state=open" \
    | jq -r ".[] | select(.head.ref==\"${branch}\") | .number" | head -1)
fi
[ -n "$pr" ] || { echo "failed to create or find bump PR" >&2; exit 1; }

# Auto-merge on green. rebase (fast-forward) keeps the bump commit message
# intact on the base branch, which is what scripts/mint-tag.sh keys off.
curl -fsS -X POST -H "$auth" -H 'Content-Type: application/json' \
  -d '{"Do":"rebase","merge_when_checks_succeed":true,"delete_branch_after_merge":true}' \
  "${api}/repos/${repo}/pulls/${pr}/merge" >/dev/null
echo "opened auto-merging PR #${pr} (${lane} ${version})"
