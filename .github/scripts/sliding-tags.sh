#!/usr/bin/env bash
# sliding-tags.sh <image> <version> <base-digest> <sim-digest>
# Point sliding image tags (X.Y, X, latest + their -sim twins) at the
# just-pushed digests, but only where <version> is the highest stable
# version at that level among all git tags. Needs full history + tags.
set -euo pipefail
image=$1
version=$2
base_digest=$3
sim_digest=$4
minor=${version%.*}
major=${version%%.*}

stable_versions=$(git tag -l 'v[0-9]*' | grep -v -- '-rc' | sed 's/^v//')

highest_matching() {
  printf '%s\n' "$stable_versions" | grep -E "$1" | sort -V | tail -1
}

slide() {
  echo "sliding :$1 -> $2"
  docker buildx imagetools create --tag "${image}:$1" "${image}@$2"
}

if [ "$(highest_matching "^${minor//./\\.}\.")" = "$version" ]; then
  slide "$minor" "$base_digest"
  slide "${minor}-sim" "$sim_digest"
fi
if [ "$(highest_matching "^${major}\.")" = "$version" ]; then
  slide "$major" "$base_digest"
  slide "${major}-sim" "$sim_digest"
fi
if [ "$(highest_matching '.')" = "$version" ]; then
  slide latest "$base_digest"
  slide sim "$sim_digest"
fi
echo "sliding tags done"
