#!/bin/sh
# Mint the release git tag when a version-bump commit lands on main or rc.
# Runs on every push; exits quietly when HEAD is not a bump commit.
# Bump commit -> tag mapping:
#   "network: bump to 10.4.57"      -> v10.4.57      (main only; -rc on rc)
#   "unifi-os: bump to 5.1.21"      -> unifi-os-v5.1.21 (main only)
set -eu

git config --global --add safe.directory "$(pwd)"
msg=$(git log -1 --format=%s)
case "$msg" in
  "network: bump to "*)
    version=${msg#network: bump to }
    tag="v${version}"
    case "$version" in
      *-rc) [ "${CI_COMMIT_BRANCH}" = rc ]   || { echo "rc bump outside rc branch" >&2; exit 1; } ;;
      *)    [ "${CI_COMMIT_BRANCH}" = main ] || { echo "stable bump outside main" >&2; exit 1; } ;;
    esac
    ;;
  "unifi-os: bump to "*)
    version=${msg#unifi-os: bump to }
    tag="unifi-os-v${version}"
    [ "${CI_COMMIT_BRANCH}" = main ] || { echo "unifi-os bump outside main" >&2; exit 1; }
    ;;
  *) echo "not a bump commit; nothing to do"; exit 0 ;;
esac

if git ls-remote --tags origin "refs/tags/${tag}" | grep -q .; then
  echo "${tag} already exists; nothing to do"
  exit 0
fi
git tag -a "${tag}" -m "${tag#*v} (${tag})"
push_url=$(printf '%s' "${CI_REPO_CLONE_URL}" | sed "s#https://#https://oauth2:${FORGEJO_TOKEN}@#")
git push "$push_url" "refs/tags/${tag}"
echo "minted ${tag}"
