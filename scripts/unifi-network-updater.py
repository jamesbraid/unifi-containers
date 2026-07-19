#!/usr/bin/env python3
"""Discover UniFi Network Application releases and update the pinned build.

Reads the release RSS feed, picks the newest release on a channel (stable
or rc), downloads the .deb to compute its sha256, and rewrites the pins in
network/Dockerfile (ARG PKGURL / ARG PKGSHA256) plus the current-versions
row in README.md.

Adapted from jacobalberty/unifi-docker's .github/scripts/unifi-updater.py
(MIT). stdlib only.
"""
import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

FEED_URL = (
    "https://community.ui.com/rss/releases/UniFi-Network-Application/"
    "e6712595-81bb-4829-8e42-9e2630fabcfe"
)
USER_AGENT = "Mozilla/5.0 (unifi-containers updater)"
DOCKERFILE = "network/Dockerfile"
README = "README.md"

PKGURL_RE = re.compile(r"^ARG PKGURL=(\S+)$", re.MULTILINE)
PKGSHA_RE = re.compile(r"^ARG PKGSHA256=(\S+)$", re.MULTILINE)
VERSION_IN_URL_RE = re.compile(r"/unifi/(\d+\.\d+\.\d+)/")
README_ROW_RE = re.compile(
    r"^\| `ghcr\.io/jamesbraid/unifi-network` \| \S+ \| \[Release notes\]\([^)]*\) \|$",
    re.MULTILINE,
)


@dataclass
class Release:
    version: str  # bare X.Y.Z
    title: str
    link: str
    is_rc: bool
    is_beta: bool

    @property
    def tag_version(self) -> str:
        return f"{self.version}-rc" if self.is_rc else self.version


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_feed(feed_bytes: bytes) -> list:
    root = ET.fromstring(feed_bytes)
    releases = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None:
            continue
        title = (title_el.text or "").strip()
        m = re.search(r"(\d+\.\d+\.\d+)", title)
        if not m:
            continue
        lc = title.lower()
        releases.append(
            Release(
                version=m.group(1),
                title=title,
                link=(link_el.text or "").strip(),
                is_rc=(" rc" in lc or "release candidate" in lc),
                is_beta=("beta" in lc),
            )
        )
    return releases


def version_key(version: str) -> tuple:
    return tuple(int(p) for p in version.split("."))


def select_release(releases, channel: str):
    if channel == "stable":
        pool = [r for r in releases if not r.is_rc and not r.is_beta]
    else:
        pool = [r for r in releases if r.is_rc and not r.is_beta]
    return max(pool, key=lambda r: version_key(r.version)) if pool else None


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


def sha256_of_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as resp:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def url_exists(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.URLError:
        return False


def cmd_verify(dockerfile: Path, against: str) -> int:
    url, sha, version = read_pins(dockerfile.read_text())
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        print(f"FAIL: PKGSHA256 is not a 64-hex-digit sha256: {sha}", file=sys.stderr)
        return 1
    if url != build_pkgurl(version):
        print(f"FAIL: PKGURL does not match the expected layout: {url}", file=sys.stderr)
        return 1
    if not url_exists(url):
        print(f"FAIL: PKGURL not reachable: {url}", file=sys.stderr)
        return 1
    if against and version_key(version) < version_key(against):
        print(f"FAIL: pinned {version} is older than {against}", file=sys.stderr)
        return 1
    print(f"ok: {version} {url} sha256={sha}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", choices=("stable", "rc"), default="stable")
    ap.add_argument("--pin", metavar="X.Y.Z",
                    help="use this exact version instead of the newest feed entry")
    ap.add_argument("--write", action="store_true",
                    help="rewrite Dockerfile and README (otherwise report only)")
    ap.add_argument("--verify", action="store_true",
                    help="pin sanity check: sha256 format, URL layout+reachability")
    ap.add_argument("--against", metavar="X.Y.Z",
                    help="with --verify: fail unless pinned version >= this")
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    args = ap.parse_args()

    dockerfile = args.repo_root / DOCKERFILE
    readme = args.repo_root / README

    if args.verify:
        return cmd_verify(dockerfile, args.against)

    _, _, current = read_pins(dockerfile.read_text())

    if args.pin:
        version, is_rc = args.pin, args.channel == "rc"
        link = "https://community.ui.com/releases"
        try:
            rel = next((r for r in parse_feed(fetch(FEED_URL)) if r.version == args.pin), None)
            if rel:
                link, is_rc = rel.link, rel.is_rc
        except Exception as exc:  # feed is best-effort in pin mode
            print(f"warning: feed lookup failed ({exc}); using generic link", file=sys.stderr)
    else:
        rel = select_release(parse_feed(fetch(FEED_URL)), args.channel)
        if rel is None:
            print(f"no {args.channel} release found in feed", file=sys.stderr)
            return 1
        if version_key(rel.version) <= version_key(current):
            print(f"up-to-date: pinned {current}, newest {args.channel} is {rel.version}")
            return 0
        version, link, is_rc = rel.version, rel.link, rel.is_rc

    url = build_pkgurl(version)
    tag_version = f"{version}-rc" if is_rc else version
    print(f"selected {tag_version} ({args.channel}); downloading for sha256 ...",
          file=sys.stderr)
    sha = sha256_of_url(url)
    print(f"sha256 {sha}", file=sys.stderr)

    if args.write:
        dockerfile.write_text(rewrite_dockerfile(dockerfile.read_text(), url, sha))
        readme.write_text(rewrite_readme(readme.read_text(), tag_version, link))

    print(tag_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
