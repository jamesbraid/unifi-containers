"""Discover UniFi OS Server releases and update the pinned build.

Queries the Ubiquiti firmware API for both platforms, requires their versions to
match, and rewrites unifi-os/pins.env plus the README row. The API supplies
sha256 checksums, so nothing is downloaded.
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from unifi_containers import pins as pinfile
from unifi_containers.download import fetch, url_exists

API = "https://fw-update.ubnt.com/api/firmware-latest"
PINS = "unifi-os/pins.env"
README = "README.md"
RELEASES_URL = "https://community.ui.com/releases"

README_ROW_RE = re.compile(
    r"^\| `ghcr\.io/jamesbraid/unifi-os-server` \| \S+ \| \[Release notes\]\([^)]*\) \|$",
    re.MULTILINE,
)


@dataclass
class Release:
    version: str  # bare X.Y.Z (v-prefix stripped)
    url: str
    sha256: str


#: One filter, then match in python. Stacking `filter=` params does not AND them
#: — asking for product+platform+channel together returns a single plausible-
#: looking record of the wrong version, so the answer has to be checked rather
#: than taken from position 0.
def api_url(platform: str) -> str:
    return f"{API}?filter=eq~~product~~unifi-os-server"


def parse_release(payload: bytes, platform: str, channel: str = "release") -> Release:
    """The `channel`/`platform` build of unifi-os-server, or raise saying what was there."""
    fw = json.loads(payload)["_embedded"]["firmware"]
    matched = [f for f in fw if f.get("platform") == platform and f.get("channel") == channel]
    if not matched:
        seen = sorted({f"{f.get('channel')}/{f.get('platform')}" for f in fw})
        raise RuntimeError(
            f"the firmware API listed no {channel}/{platform} build of "
            f"unifi-os-server, so its version is unknown (saw: {', '.join(seen) or 'nothing'})"
        )
    entry = matched[0]
    return Release(
        version=entry["version"].lstrip("v"),
        url=entry["_links"]["data"]["href"],
        sha256=entry["sha256_checksum"],
    )


#: The bundled Network application, as UOS's own updater fetches it: product
#: `unifi`, platform uos-deb11-<arch>. Both arches publish the same artifact;
#: requiring them to agree catches a mid-publish read the same way the
#: UOS-version pair below does.
APP_PRODUCT_URL = f"{API}?filter=eq~~product~~unifi"
APP_PLATFORMS = ("uos-deb11-amd64", "uos-deb11-arm64")


def parse_app_version(payload: bytes, channel: str = "release") -> str:
    """The release-channel version of the app's UOS deb, e.g. 10.6.101-35991-1."""
    fw = json.loads(payload)["_embedded"]["firmware"]
    versions = set()
    for platform in APP_PLATFORMS:
        matched = [f for f in fw if f.get("platform") == platform and f.get("channel") == channel]
        if not matched:
            seen = sorted({f"{f.get('channel')}/{f.get('platform')}" for f in fw})
            raise RuntimeError(
                f"the firmware API listed no {channel}/{platform} build of the "
                f"bundled application (saw: {', '.join(seen) or 'nothing'})"
            )
        versions.add(matched[0]["version"].lstrip("v"))
    if len(versions) != 1:
        raise RuntimeError(f"uos-deb11 platform versions disagree: {sorted(versions)}")
    return versions.pop()


def check_versions_match(amd64: Release, arm64: Release) -> None:
    if amd64.version != arm64.version:
        raise RuntimeError(
            f"platform version mismatch: amd64={amd64.version} arm64={arm64.version}"
        )


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


def app_version_moved(current: str, latest: str) -> bool:
    """Whether the app pin should move to `latest`.

    Full dpkg versions (10.6.101-35991-1) don't parse as PEP 440, so compare
    the upstream X.Y.Z part properly and treat any change at an equal or newer
    upstream as forward motion — the release channel only ever advances, and a
    same-version repackage is exactly what a console would install.
    """
    if latest == current:
        return False
    if not current:
        return True
    return Version(latest.split("-")[0]) >= Version(current.split("-")[0])


def bump(write=False, repo_root=Path(".")):
    """Select a release and optionally rewrite the pins.

    Returns the changed version (UOS release, or the bundled app's when only it
    moved), or None when both pins are current.
    """
    pins_path = repo_root / PINS
    readme_path = repo_root / README
    pins = pinfile.env_values(pins_path.read_text())
    current = pins["UOS_VERSION"]
    amd64 = parse_release(fetch(api_url("linux-x64")), "linux-x64")
    arm64 = parse_release(fetch(api_url("linux-arm64")), "linux-arm64")
    check_versions_match(amd64, arm64)
    app = parse_app_version(fetch(APP_PRODUCT_URL))

    uos_moved = Version(amd64.version) > Version(current)
    app_moved = app_version_moved(pins.get("UOS_APP_VERSION", ""), app)
    if not uos_moved and not app_moved:
        return None
    if write:
        text = pins_path.read_text()
        if uos_moved:
            text = rewrite_pins(text, amd64.version, amd64, arm64)
        text = _sub_pin(text, "UOS_APP_VERSION", app)
        pins_path.write_text(text)
        if uos_moved:
            readme_path.write_text(
                rewrite_readme(readme_path.read_text(), amd64.version, RELEASES_URL)
            )
    return amd64.version if uos_moved else app


SHA_KEYS = ("UOS_SHA256_AMD64", "UOS_SHA256_ARM64")
URL_KEYS = ("UOS_URL_AMD64", "UOS_URL_ARM64")


def verify(repo_root=Path(".")):
    """Pin sanity check: fields present, sha256 hex, URLs reachable."""
    pins = pinfile.env_values((repo_root / PINS).read_text())
    for key in ("UOS_VERSION", *SHA_KEYS, *URL_KEYS):
        if not pins.get(key):
            print(f"FAIL: {key} missing from pins.env", file=sys.stderr)
            return 1
    for key in SHA_KEYS:
        if not re.fullmatch(r"[0-9a-f]{64}", pins[key]):
            print(f"FAIL: {key} is not a 64-hex-digit sha256", file=sys.stderr)
            return 1
    for key in URL_KEYS:
        if not url_exists(pins[key]):
            print(f"FAIL: {key} not reachable: {pins[key]}", file=sys.stderr)
            return 1
    app = pins.get("UOS_APP_VERSION", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+-\d+-\d+", app):
        print(f"FAIL: UOS_APP_VERSION is not a full dpkg version: {app!r}", file=sys.stderr)
        return 1
    print(f"ok: {pins['UOS_VERSION']} app {app}")
    return 0
