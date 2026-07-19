#!/usr/bin/env bash
# Extract the OCI base image embedded in the official UniFi OS Server
# installer and load it into Docker as uos-base:local.
#
# The installer is an ELF binary with a standard ZIP archive appended;
# image.tar inside it is the full UOS rootfs image. A plain unzip is all
# the "extraction" there is — deliberately dumb, so installer-format drift
# fails loudly here rather than silently downstream.
#
# Extraction approach derived from toquanghieu/unifi-os-server-docker
# (MIT), simplified from binwalk to unzip after inspecting the 5.1.21
# installer format.
#
# Usage: extract-uos.sh [amd64|arm64]   (defaults to host architecture)
set -euo pipefail

cd "$(dirname "$0")"
. ./pins.env

arch=${1:-$(uname -m)}
case "$arch" in
  amd64|x86_64) url=$UOS_URL_AMD64; sha=$UOS_SHA256_AMD64 ;;
  arm64|aarch64) url=$UOS_URL_ARM64; sha=$UOS_SHA256_ARM64 ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "==> downloading UOS ${UOS_VERSION} (${arch}) installer"
curl -fSL -o "$work/installer" "$url"
echo "${sha}  $work/installer" | sha256sum -c -

echo "==> extracting embedded image.tar"
unzip -o -q "$work/installer" image.tar -d "$work" || true  # exit 1 = benign prepended-ELF warning
[ -s "$work/image.tar" ] || { echo "image.tar not found in installer — format drift?" >&2; exit 1; }
chmod 644 "$work/image.tar"  # the zip stores it mode 000; docker load needs to read it

echo "==> loading into docker"
loaded=$(docker load -i "$work/image.tar" | sed -n 's/^Loaded image: //p')
[ -n "$loaded" ] || { echo "docker load produced no image" >&2; exit 1; }
docker tag "$loaded" uos-base:local
echo "==> uos-base:local <- ${loaded} (UOS ${UOS_VERSION}, ${arch})"
