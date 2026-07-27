"""Turn a release git tag into everything a release workflow needs.

So no workflow ever parses a version string. The pin check rides along and
exits 1 on disagreement, so a mis-typed tag fails at the workflow's first step.
"""

import sys
from dataclasses import dataclass

from unifi_containers import gitops, pins, release


@dataclass(frozen=True)
class Plan:
    release: release.Release
    pinned: str
    #: The newest build of this upstream version on this channel.
    newest_build: bool
    #: The highest stable release of this product. Always False for an RC.
    top: bool


def plan(git_tag, tags, pinned_lookup=pins.pinned_version):
    """A Plan for a release tag, or raise ValueError."""
    rel = release.parse(git_tag)
    if rel is None:
        raise ValueError(
            f"{git_tag!r} is not a release tag. Expected "
            f"<product>/<version>[-rc]-<revision>, e.g. network/10.4.57-1 or "
            f"unifi-os/5.1.21-3"
        )

    if rel.is_rc and not release.has_rc_channel(rel.product):
        raise ValueError(
            f"{git_tag} is a release candidate, but {rel.product} has no RC "
            f"channel upstream. Nothing would be built for it to point at."
        )

    pinned = pinned_lookup(rel.product)
    if pinned != rel.upstream:
        raise ValueError(
            f"{git_tag} names upstream {rel.upstream} but {rel.product} pins "
            f"{pinned}. Tag and pin must agree or the release publishes an "
            f"image built from a different upstream version than its tag says."
        )

    return Plan(
        release=rel,
        pinned=pinned,
        newest_build=release.is_highest_build(tags, rel),
        top=release.is_top_of_channel(tags, rel),
    )


def emit(plan, stream):
    """Write the GITHUB_OUTPUT lines the release workflows read.

    Push and slide targets are separate: a variant is *pushed* to its
    version-level tag, and the unqualified names are pointed at that digest after.
    """
    rel = plan.release
    version = release.version_tags(rel) if plan.newest_build else {}
    values = [
        ("publish", "true" if plan.newest_build else "false"),
        ("image", rel.image),
        ("push_base", version.get("base", "")),
        ("push_sim", version.get("sim", "")),
        ("push_seeded", version.get("seeded", "")),
        ("globals", " ".join(release.global_tags(rel, plan.top)) if plan.newest_build else ""),
        ("rc", "true" if rel.is_rc else "false"),
        # The rc lane stops at the base image: sim needs a demo fleet nobody
        # tests against a pre-release, seeded needs a wizard run.
        ("build_variants", "false" if rel.is_rc else "true"),
        # Reporting only. Never a push target -- it carries the build number.
        ("build", rel.version),
    ]
    for key, value in values:
        print(f"{key}={value}", file=stream)


def report(tag, github=False):
    """Describe a release, or emit its GITHUB_OUTPUT lines."""
    try:
        gitops.trust_workdir()
        result = plan(tag, gitops.tags())
    except (ValueError, gitops.GitError) as exc:
        # Never fall back to "no tags": that reads as "this is the newest build"
        # and would slide `latest` onto a superseded image.
        print(f"release-plan: {exc}", file=sys.stderr)
        return 1

    if github:
        emit(result, sys.stdout)
        return 0

    rel = result.release
    channel = "release candidate" if rel.is_rc else "stable"
    print(f"{rel.product}: {channel} {rel.upstream}, build {rel.revision}")
    print(f"  image        {rel.image}")
    print(f"  pinned       {result.pinned}")
    print(f"  newest build {result.newest_build}")
    print(f"  highest      {result.top}")
    sliding = release.sliding_tags(rel, result.newest_build, result.top)
    print(f"  tags         {' '.join(sliding) or '(none)'}")
    print(f"  variants     {'base only' if rel.is_rc else 'base, sim, seeded'}")
    return 0
