#!/usr/bin/env bash
# make-seed.sh <base-image-ref> [output-tgz]
# Produce seed.tgz for the seeded variant: boot the given base image with
# a scratch volume, complete the first-run wizard via seed-wizard.sh,
# clean-stop, and snapshot /unifi.
set -euo pipefail

cd "$(dirname "$0")"
base_image=$1
out=${2:-seed.tgz}
name="seedgen-$$"
vol="seedvol-$$"
port=48443

cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$vol" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker volume create "$vol" >/dev/null
docker run -d --name "$name" -p "$port:8443" -v "$vol:/unifi" "$base_image" >/dev/null

echo "==> waiting for controller to become healthy"
../../.github/scripts/wait-healthy.sh "$name" 720

./seed-wizard.sh "https://localhost:$port" admin unifi-containers-seeded

echo "==> clean stop + snapshot"
docker stop -t 120 "$name" >/dev/null
docker rm "$name" >/dev/null
docker run --rm -v "$vol:/unifi" alpine tar -czf - -C /unifi . > "$out"
echo "==> wrote $(pwd)/$out ($(du -h "$out" | cut -f1))"
