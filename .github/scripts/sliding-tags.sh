#!/usr/bin/env bash
# sliding-tags.sh <image> <version> <base-digest> <sim-digest> [tag-prefix] [seeded-digest]
# Point the top-level sliding tags (latest + sim + seeded, when a seeded
# digest is given) at the just-pushed digests, but only when <version> is
# the highest stable version among the product's git tags. Needs full
# history. Per-major (X) and per-minor (X.Y) sliding tags are not published.
# tag-prefix selects the product's tag namespace: "v" (default, network)
# or "unifi-os-v".
set -euo pipefail
image=$1
version=$2
base_digest=$3
sim_digest=$4
tag_prefix=${5:-v}
seeded_digest=${6:-}

stable_versions=$(git tag -l "${tag_prefix}[0-9]*" | grep -v -- '-rc' | sed "s/^${tag_prefix}//")

highest_matching() {
  printf '%s\n' "$stable_versions" | grep -E "$1" | sort -V | tail -1
}

slide() {
  echo "sliding :$1 -> $2"
  docker buildx imagetools create --tag "${image}:$1" "${image}@$2"
}

if [ "$(highest_matching '.')" = "$version" ]; then
  slide latest "$base_digest"
  slide sim "$sim_digest"
  [ -n "$seeded_digest" ] && slide seeded "$seeded_digest"
fi
echo "sliding tags done"
