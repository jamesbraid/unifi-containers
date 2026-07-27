"""Cut the next release tag for a product.

Version from the pins, build number from the tags; idempotent. RCs come from
the branch, not the tag: a tag cut on `rc` is `<product>/<version>-rc-<n>`.
"""

import os

from packaging.version import Version

from unifi_containers import gitops, pins, release

RC_BRANCH = "rc"


def current_branch(env=None, repo=None):
    """The branch being built; Woodpecker's value wins, as a CI checkout is often detached."""
    environ = os.environ if env is None else env
    return (
        environ.get("CI_COMMIT_BRANCH")
        or environ.get("GITHUB_REF_NAME")
        or gitops.current_branch(repo)
    )


def decide(product, pinned, tags, is_rc):
    """(Release, reason) to cut, or (None, reason) when nothing is due."""
    if not pinned:
        return None, f"{product} has no pinned version to release"

    existing = release.builds_of(tags, product, pinned, is_rc)
    if existing:
        top = max(existing, key=lambda r: Version(r.version))
        return None, (
            f"{product} {pinned} is already released as {top.git_tag}; a "
            f"rebuild is deliberate — pass --rebuild to cut the next build"
        )

    return release.next_release(tags, product, pinned, is_rc=is_rc), (
        f"{product} pins {pinned}, which has no release tag yet"
    )


def decide_rebuild(product, pinned, tags, is_rc):
    """(Release, reason) for an explicit rebuild of the pinned version."""
    if not pinned:
        return None, f"{product} has no pinned version to rebuild"
    existing = release.builds_of(tags, product, pinned, is_rc)
    if not existing:
        return None, (
            f"{product} {pinned} has never been released, so there is nothing "
            f"to rebuild — cut the first release without --rebuild"
        )
    return release.next_release(tags, product, pinned, is_rc=is_rc), (
        f"rebuilding {product} {pinned}"
    )


def cut(products, push=False, rebuild=False):
    """Cut what is due for each product. Returns the releases cut, or that would be."""
    gitops.trust_workdir()
    repo = gitops.repository()
    target = gitops.push_target()
    # Every decision below is "which builds already exist", so the tags have to
    # be here first. A CI clone fetches with --no-tags, and this runs in an image
    # with no git binary, so it cannot be left to a shell `git fetch`.
    gitops.fetch_tags(target.url, token=target.token, repo=repo)
    tags = gitops.tags(repo)
    is_rc = current_branch(repo=repo) == RC_BRANCH
    chooser = decide_rebuild if rebuild else decide

    due = []
    for product in products:
        if is_rc and not release.has_rc_channel(product):
            print(f"skip  {product} has no release-candidate channel")
            continue
        rel, reason = chooser(product, pins.pinned_version(product), tags, is_rc)
        if rel is None:
            print(f"skip  {reason}")
            continue
        print(f"cut   {rel.git_tag}  ({reason})")
        due.append(rel)

    if not due:
        return []
    if not push:
        print("dry run; pass --push to tag and publish")
        return due

    author = gitops.signature(*gitops.IDENTITY)
    for rel in due:
        message = f"{rel.product} {rel.upstream} build {rel.revision}"
        gitops.create_tag(repo, rel.git_tag, message, author)
        gitops.push(target.url, f"refs/tags/{rel.git_tag}", token=target.token, repo=repo)
        print(f"pushed {rel.git_tag}")
    return due
