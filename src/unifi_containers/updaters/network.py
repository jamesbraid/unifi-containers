"""Discover UniFi Network Application releases and update the pinned build.

Reads the release RSS feed, picks the newest release on a channel, computes the
.deb's sha256, and rewrites the pins in network/Dockerfile plus the README row.
Adapted from jacobalberty/unifi-docker's unifi-updater.py (MIT).
"""

import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import feedparser
from debian.deb822 import Packages
from packaging.version import Version

from unifi_containers.download import fetch, sha256_of_url, url_exists
from unifi_containers.pins import PKGURL_RE, VERSION_IN_URL_RE

FEED_URL = (
    "https://community.ui.com/rss/releases/UniFi-Network-Application/"
    "e6712595-81bb-4829-8e42-9e2630fabcfe"
)
# The Ubiquiti apt repo's stable dist is the GA authority. The community
# feed stopped marking channels in titles (verified 2026-07-19: 10.5.62 RC
# appears with a bare title while apt stable ships 10.4.57), so feed
# titles alone cannot distinguish stable from RC.
APT_PACKAGES_URL = "https://dl.ui.com/unifi/debian/dists/stable/ubiquiti/binary-amd64/Packages"
FALLBACK_LINK = "https://community.ui.com/releases"
DOCKERFILE = "network/Dockerfile"
README = "README.md"

PKGSHA_RE = re.compile(r"^ARG PKGSHA256=(\S+)$", re.MULTILINE)
README_ROW_RE = re.compile(
    r"^\| `ghcr\.io/jamesbraid/unifi-network` \| \S+ \| \[Release notes\]\([^)]*\) \|$",
    re.MULTILINE,
)


@dataclass
class Release:
    version: str  # bare X.Y.Z
    link: str
    is_rc: bool
    is_beta: bool

    @property
    def tag_version(self) -> str:
        return f"{self.version}-rc" if self.is_rc else self.version


def parse_feed(feed_bytes: bytes) -> list:
    parsed = feedparser.parse(feed_bytes)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"the release feed did not parse: {parsed.get('bozo_exception')}")
    releases = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        m = re.search(r"(\d+\.\d+\.\d+)", title)
        if not m:
            continue
        lc = title.lower()
        releases.append(
            Release(
                version=m.group(1),
                link=(entry.get("link") or "").strip(),
                is_rc=(" rc" in lc or "release candidate" in lc),
                is_beta=("beta" in lc),
            )
        )
    return releases


def parse_apt_stable(packages_text: str) -> str:
    """Version of the `unifi` package in the apt stable dist (GA truth)."""
    for paragraph in Packages.iter_paragraphs(packages_text, use_apt_pkg=False):
        if paragraph.get("Package") == "unifi":
            return paragraph["Version"].split("-")[0]
    raise RuntimeError("unifi package not found in apt Packages index")


def select_release(releases, channel: str, apt_stable: str):
    """stable = whatever the apt repo ships; rc = newest non-beta feed version above it."""
    if channel == "stable":
        rel = next((r for r in releases if r.version == apt_stable), None)
        if rel is None:
            return Release(version=apt_stable, link=FALLBACK_LINK, is_rc=False, is_beta=False)
        return replace(rel, is_rc=False)
    pool = [r for r in releases if not r.is_beta and Version(r.version) > Version(apt_stable)]
    if not pool:
        return None
    return replace(max(pool, key=lambda r: Version(r.version)), is_rc=True)


def build_pkgurl(version: str) -> str:
    return f"https://dl.ui.com/unifi/{version}/unifi_sysvinit_all.deb"


def read_pins(dockerfile_text: str):
    url_m = PKGURL_RE.search(dockerfile_text)
    sha_m = PKGSHA_RE.search(dockerfile_text)
    if not url_m or not sha_m:
        raise RuntimeError("Dockerfile is missing ARG PKGURL / ARG PKGSHA256 pins")
    url = url_m.group(1)
    ver_m = VERSION_IN_URL_RE.search(url)
    if not ver_m:
        raise RuntimeError(f"cannot extract version from PKGURL: {url}")
    return url, sha_m.group(1), ver_m.group(1)


def rewrite_dockerfile(text: str, url: str, sha256: str) -> str:
    text, n_url = PKGURL_RE.subn(f"ARG PKGURL={url}", text, count=1)
    text, n_sha = PKGSHA_RE.subn(f"ARG PKGSHA256={sha256}", text, count=1)
    if n_url != 1 or n_sha != 1:
        raise RuntimeError("failed to rewrite Dockerfile pins")
    return text


def rewrite_readme(text: str, version: str, link: str) -> str:
    row = f"| `ghcr.io/jamesbraid/unifi-network` | {version} | [Release notes]({link}) |"
    text, n = README_ROW_RE.subn(row, text, count=1)
    if n != 1:
        raise RuntimeError("failed to rewrite README current-versions row")
    return text


def bump(channel="stable", write=False, repo_root=Path(".")):
    """Select a release and optionally rewrite the pins.

    Returns the tag version (`10.4.57`, or `10.5.66-rc`), or None when the pin is
    already current. The lane driver reads that return value; nothing parses stdout.
    """
    dockerfile = repo_root / DOCKERFILE
    readme = repo_root / README
    _, _, current = read_pins(dockerfile.read_text())

    apt_stable = parse_apt_stable(fetch(APT_PACKAGES_URL).decode())
    rel = select_release(parse_feed(fetch(FEED_URL)), channel, apt_stable)
    if rel is None or Version(rel.version) <= Version(current):
        return None

    url = build_pkgurl(rel.version)
    sha = sha256_of_url(url)
    if write:
        dockerfile.write_text(rewrite_dockerfile(dockerfile.read_text(), url, sha))
        readme.write_text(rewrite_readme(readme.read_text(), rel.tag_version, rel.link))
    return rel.tag_version


def verify(repo_root=Path(".")):
    """Pin sanity check: sha256 format, URL layout and reachability."""
    url, sha, version = read_pins((repo_root / DOCKERFILE).read_text())
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        print(f"FAIL: PKGSHA256 is not a 64-hex-digit sha256: {sha}", file=sys.stderr)
        return 1
    if url != build_pkgurl(version):
        print(f"FAIL: PKGURL does not match the expected layout: {url}", file=sys.stderr)
        return 1
    if not url_exists(url):
        print(f"FAIL: PKGURL not reachable: {url}", file=sys.stderr)
        return 1
    print(f"ok: {version} {url} sha256={sha}")
    return 0
