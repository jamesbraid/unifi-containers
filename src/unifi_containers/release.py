"""The release model: one regex, one place, one answer.

A git tag names a release completely — `network/10.4.57-1`, `unifi-os/5.1.21-3`.
Product prefixes are exact and a revision is always present.

Only GA upstream versions are released here. Ubiquiti does publish release
candidates, but which versions those are is stated only in a badge on a
client-rendered page: the community feed carries no channel field at all, and
the firmware API this repo pins from lists GA builds alone. A prerelease has no
reliable machine-readable identity, so nothing here claims to recognise one.
"""

import re
from dataclasses import dataclass

from packaging.version import Version

IMAGES = {
    "network": "ghcr.io/jamesbraid/unifi-network",
    "unifi-os": "ghcr.io/jamesbraid/unifi-os-server",
}

#: Anchored and exhaustive. A tag that does not match is not a release, and
#: callers are expected to say so rather than guess. Retired `-rc` tags no longer
#: match, which is what keeps them out of every build-number decision.
TAG_RE = re.compile(
    r"^(?P<product>network|unifi-os)/(?P<upstream>\d+\.\d+\.\d+)-(?P<revision>\d+)$"
)


@dataclass(frozen=True)
class Release:
    product: str
    upstream: str  # 10.4.57
    revision: int  # 1

    @property
    def image(self):
        return IMAGES[self.product]

    @property
    def version(self):
        """The published image tag: what a consumer pins."""
        return f"{self.upstream}-{self.revision}"

    @property
    def git_tag(self):
        return f"{self.product}/{self.version}"


def parse(git_tag):
    """A Release from a git tag, or None if the tag is not a release."""
    match = TAG_RE.match(git_tag)
    if not match:
        return None
    return Release(
        product=match.group("product"),
        upstream=match.group("upstream"),
        revision=int(match.group("revision")),
    )


def releases_for(tags, product):
    """Every release of `product` in `tags`; anything that is not a release tag does not match."""
    parsed = (parse(tag) for tag in tags)
    return [r for r in parsed if r is not None and r.product == product]


def builds_of(tags, product, upstream):
    """Every build of one upstream version."""
    return [r for r in releases_for(tags, product) if r.upstream == upstream]


def highest(tags, product):
    """The greatest release of `product`, or None.

    `version` is valid PEP 440 as it stands — `10.4.57-1` is post-release 1 of
    10.4.57 — so `packaging` orders builds and versions for us.
    """
    pool = releases_for(tags, product)
    return max(pool, key=lambda r: Version(r.version)) if pool else None


def next_release(tags, product, upstream):
    """The next release to cut for `upstream`: revision 1, or one past its highest revision."""
    same = builds_of(tags, product, upstream)
    revision = max((r.revision for r in same), default=0) + 1
    return Release(product=product, upstream=upstream, revision=revision)


def is_highest_stable(tags, release):
    """True when `release` tops its product's releases, so it carries the global names."""
    top = highest(tags + [release.git_tag], release.product)
    return top is not None and Version(top.version) == Version(release.version)


def is_highest_build(tags, release):
    """True when no later build of this upstream version exists.

    Every published tag slides, so this is what stops re-running an older
    build's release workflow from pointing a tag back at the older image.
    """
    same = builds_of(tags, release.product, release.upstream)
    return all(r.revision <= release.revision for r in same)


def version_tags(release):
    """The version-level names this release's variants are pushed to, one per variant.

    Build numbers are a git concept and never appear here.
    """
    return {
        "base": release.upstream,
        "sim": f"{release.upstream}-sim",
        "seeded": f"{release.upstream}-seeded",
    }


def global_tags(release, is_top):
    """The unqualified floating names, which follow the highest release."""
    return ["latest", "sim", "seeded"] if is_top else []


def sliding_tags(release, is_newest_build, is_top):
    """Every GHCR name that ends up pointing at this release. For reporting.

    Empty for anything but the newest build: see `is_highest_build`.
    """
    if not is_newest_build:
        return []
    return list(version_tags(release).values()) + global_tags(release, is_top)
