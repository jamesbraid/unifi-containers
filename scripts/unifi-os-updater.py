#!/usr/bin/env python3
"""Discover UniFi OS Server releases and update the pinned build.

Queries the Ubiquiti firmware API for both platforms (amd64 + arm64),
requires their versions to match, and rewrites unifi-os/pins.env plus the
current-versions row in README.md. The API provides sha256 checksums
directly, so no artifact download is needed.

stdlib only.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API = "https://fw-update.ubnt.com/api/firmware-latest"
USER_AGENT = "Mozilla/5.0 (unifi-containers updater)"
PINS = "unifi-os/pins.env"
README = "README.md"

README_ROW_RE = re.compile(
    r"^\| `ghcr\.io/jamesbraid/unifi-os-server` \| \S+ \| \[Release notes\]\([^)]*\) \|$",
    re.MULTILINE,
)


@dataclass
class Release:
    version: str  # bare X.Y.Z (v-prefix stripped)
    url: str
    sha256: str


def api_url(platform: str) -> str:
    return (f"{API}?filter=eq~~product~~unifi-os-server"
            f"&filter=eq~~platform~~{platform}&filter=eq~~channel~~release")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_release(payload: bytes) -> Release:
    fw = json.loads(payload)["_embedded"]["firmware"]
    if not fw:
        raise RuntimeError("firmware API returned no entries")
    entry = fw[0]
    return Release(
        version=entry["version"].lstrip("v"),
        url=entry["_links"]["data"]["href"],
        sha256=entry["sha256_checksum"],
    )


def check_versions_match(amd64: Release, arm64: Release) -> None:
    if amd64.version != arm64.version:
        raise RuntimeError(
            f"platform version mismatch: amd64={amd64.version} arm64={arm64.version}"
        )


def version_key(version: str) -> tuple:
    return tuple(int(p) for p in version.split("."))


def read_pins(text: str) -> dict:
    pins = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            pins[k] = v
    return pins


def _sub_pin(text: str, key: str, value: str) -> str:
    new, n = re.subn(rf"^{key}=.*$", f"{key}={value}", text, count=1, flags=re.MULTILINE)
    if n != 1:
        raise RuntimeError(f"failed to rewrite {key} in pins.env")
    return new


def rewrite_pins(text: str, version: str, amd64: Release, arm64: Release) -> str:
    text = _sub_pin(text, "UOS_VERSION", version)
    text = _sub_pin(text, "UOS_URL_AMD64", amd64.url)
    text = _sub_pin(text, "UOS_SHA256_AMD64", amd64.sha256)
    text = _sub_pin(text, "UOS_URL_ARM64", arm64.url)
    text = _sub_pin(text, "UOS_SHA256_ARM64", arm64.sha256)
    return text


def rewrite_readme(text: str, version: str, link: str) -> str:
    row = f"| `ghcr.io/jamesbraid/unifi-os-server` | {version} | [Release notes]({link}) |"
    text, n = README_ROW_RE.subn(row, text, count=1)
    if n != 1:
        raise RuntimeError("failed to rewrite README unifi-os-server row")
    return text


def url_exists(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        return False


def cmd_verify(pins_path: Path, against: str) -> int:
    pins = read_pins(pins_path.read_text())
    for key in ("UOS_VERSION", "UOS_URL_AMD64", "UOS_SHA256_AMD64",
                "UOS_URL_ARM64", "UOS_SHA256_ARM64"):
        if not pins.get(key):
            print(f"FAIL: {key} missing from pins.env", file=sys.stderr)
            return 1
    for key in ("UOS_SHA256_AMD64", "UOS_SHA256_ARM64"):
        if not re.fullmatch(r"[0-9a-f]{64}", pins[key]):
            print(f"FAIL: {key} is not a 64-hex-digit sha256", file=sys.stderr)
            return 1
    for key in ("UOS_URL_AMD64", "UOS_URL_ARM64"):
        if not url_exists(pins[key]):
            print(f"FAIL: {key} not reachable: {pins[key]}", file=sys.stderr)
            return 1
    if against and version_key(pins["UOS_VERSION"]) < version_key(against):
        print(f"FAIL: pinned {pins['UOS_VERSION']} is older than {against}", file=sys.stderr)
        return 1
    print(f"ok: {pins['UOS_VERSION']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="rewrite pins.env and README (otherwise report only)")
    ap.add_argument("--verify", action="store_true",
                    help="pin sanity check: fields present, sha256 hex, URLs reachable")
    ap.add_argument("--against", metavar="X.Y.Z",
                    help="with --verify: fail unless pinned version >= this")
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    args = ap.parse_args()

    pins_path = args.repo_root / PINS
    readme_path = args.repo_root / README

    if args.verify:
        return cmd_verify(pins_path, args.against)

    current = read_pins(pins_path.read_text())["UOS_VERSION"]
    amd64 = parse_release(fetch(api_url("linux-x64")))
    arm64 = parse_release(fetch(api_url("linux-arm64")))
    check_versions_match(amd64, arm64)

    if version_key(amd64.version) <= version_key(current):
        print(f"up-to-date: pinned {current}, newest is {amd64.version}")
        return 0

    if args.write:
        pins_path.write_text(
            rewrite_pins(pins_path.read_text(), amd64.version, amd64, arm64))
        readme_path.write_text(
            rewrite_readme(readme_path.read_text(), amd64.version,
                           "https://community.ui.com/releases"))

    print(amd64.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
