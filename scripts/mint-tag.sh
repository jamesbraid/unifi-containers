#!/bin/sh
# Mint the release git tag when a version-bump commit lands on main or rc.
# Runs on every push; exits quietly when HEAD is not a bump commit.
set -eu

git config --global --add safe.directory "$(pwd)"
msg=$(git log -1 --format=%s)
case "$msg" in
  "network: bump to "*) version=${msg#network: bump to } ;;
  *) echo "not a bump commit; nothing to do"; exit 0 ;;
esac

# Lane guard: stable tags only from main, rc tags only from rc.
case "$version" in
  *-rc) [ "${CI_COMMIT_BRANCH}" = rc ]   || { echo "rc bump outside rc branch" >&2; exit 1; } ;;
  *)    [ "${CI_COMMIT_BRANCH}" = main ] || { echo "stable bump outside main" >&2; exit 1; } ;;
esac

tag="v${version}"
if git ls-remote --tags origin "refs/tags/${tag}" | grep -q .; then
  echo "${tag} already exists; nothing to do"
  exit 0
fi
git tag -a "${tag}" -m "UniFi Network Application ${version}"
push_url=$(printf '%s' "$CI_REPO_CLONE_URL" | sed "s#https://#https://oauth2:${FORGEJO_TOKEN}@#")
git push "$push_url" "refs/tags/${tag}"
echo "minted ${tag}"
