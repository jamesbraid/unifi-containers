"""The release model: one regex, one place, one answer.

A git tag names a release completely — `network/10.4.57-1`,
`network/10.5.66-rc-2`, `unifi-os/5.1.21-3`. Product prefixes are exact and a
revision is always present, RCs included.
"""

import re
from dataclasses import dataclass

from packaging.version import Version

IMAGES = {
    "network": "ghcr.io/jamesbraid/unifi-network",
    "unifi-os": "ghcr.io/jamesbraid/unifi-os-server",
}

#: Which products have a release-candidate channel. UniFi OS ships no prerelease,
#: so `unifi-os/<version>-rc-<n>` names a release that cannot exist upstream. Both
#: the cutter and the planner read this, so neither can accept what the other
#: refuses.
RC_PRODUCTS = frozenset({"network"})


def has_rc_channel(product):
    """Whether `product` releases candidates at all."""
    return product in RC_PRODUCTS


#: Anchored and exhaustive. A tag that does not match is not a release, and
#: callers are expected to say so rather than guess.
TAG_RE = re.compile(
    r"^(?P<product>network|unifi-os)/"
    r"(?P<upstream>\d+\.\d+\.\d+)"
    r"(?P<rc>-rc)?"
    r"-(?P<revision>\d+)$"
)


@dataclass(frozen=True)
class Release:
    product: str
    upstream: str  # 10.4.57
    revision: int  # 1
    is_rc: bool

    @property
    def image(self):
        return IMAGES[self.product]

    @property
    def version(self):
        """The published image tag: what a consumer pins."""
        suffix = "-rc" if self.is_rc else ""
        return f"{self.upstream}{suffix}-{self.revision}"

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
        is_rc=bool(match.group("rc")),
    )


def releases_for(tags, product):
    """Every release of `product` in `tags`; anything that is not a release tag does not match."""
    parsed = (parse(tag) for tag in tags)
    return [r for r in parsed if r is not None and r.product == product]


def builds_of(tags, product, upstream, is_rc):
    """Every build of one upstream version on one channel. Stable and rc count separately."""
    return [r for r in releases_for(tags, product) if r.upstream == upstream and r.is_rc == is_rc]


def highest(tags, product):
    """The greatest stable release of `product`, or None; RCs never move `latest`.

    `version` is valid PEP 440 as it stands — `10.4.57-1` is post-release 1 of
    10.4.57 — so `packaging` orders builds and channels for us.
    """
    pool = [r for r in releases_for(tags, product) if not r.is_rc]
    return max(pool, key=lambda r: Version(r.version)) if pool else None


def next_release(tags, product, upstream, is_rc=False):
    """The next release to cut for `upstream`: revision 1, or one past its highest revision."""
    same = builds_of(tags, product, upstream, is_rc)
    revision = max((r.revision for r in same), default=0) + 1
    return Release(product=product, upstream=upstream, revision=revision, is_rc=is_rc)


def is_highest_stable(tags, release):
    """True when `release` tops its product's stable releases. An RC is never highest."""
    if release.is_rc:
        return False
    top = highest(tags + [release.git_tag], release.product)
    return top is not None and Version(top.version) == Version(release.version)


def is_highest_rc(tags, release):
    """True when no higher upstream RC of this product exists.

    The `rc` pointer floats the same way `latest` does, so it needs the same
    guard. is_highest_build only compares builds within one upstream version, so
    without this, releasing 10.5.60-rc-1 after 10.5.66-rc-2 has shipped drags
    `rc` back onto the older candidate.
    """
    if not release.is_rc:
        return False
    pool = [r for r in releases_for(tags, release.product) if r.is_rc]
    highest_rc = max(pool + [release], key=lambda r: (Version(r.upstream), r.revision))
    return Version(highest_rc.upstream) <= Version(release.upstream)


def is_highest_build(tags, release):
    """True when no later build of this upstream version exists on this channel.

    Every published tag slides, so this is what stops re-running an older
    build's release workflow from pointing a tag back at the older image.
    """
    same = builds_of(tags, release.product, release.upstream, release.is_rc)
    return all(r.revision <= release.revision for r in same)


def is_top_of_channel(tags, release):
    """Whether this release carries its channel's floating names: `latest` or `rc`."""
    return is_highest_rc(tags, release) if release.is_rc else is_highest_stable(tags, release)


def version_tags(release):
    """The version-level names this release's variants are pushed to, one per variant.

    Build numbers are a git concept and never appear here.
    """
    if release.is_rc:
        return {"base": f"{release.upstream}-rc"}
    return {
        "base": release.upstream,
        "sim": f"{release.upstream}-sim",
        "seeded": f"{release.upstream}-seeded",
    }


def global_tags(release, is_top):
    """The unqualified floating names. `is_top` means highest on this release's channel."""
    if release.is_rc:
        return ["rc"] if is_top else []
    return ["latest", "sim", "seeded"] if is_top else []


def sliding_tags(release, is_newest_build, is_top):
    """Every GHCR name that ends up pointing at this release. For reporting.

    Empty for anything but the newest build: see `is_highest_build`.
    """
    if not is_newest_build:
        return []
    return list(version_tags(release).values()) + global_tags(release, is_top)
