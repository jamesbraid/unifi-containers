"""Cut the next release tag for a product.

Version from the pins, build number from the tags; idempotent.
"""

from pathlib import Path

from packaging.version import Version

from unifi_containers import gitops, pins, release


def decide(product, pinned, tags, pins_changed_since=None):
    """(Release, reason) to cut, or (None, reason) when nothing is due.

    `pins_changed_since` answers "do the pins differ from that release tag";
    when it says yes, the pinned version being released already is not the end
    of the story — the tag was built from different pins (the bundled app
    moved under an unchanged UOS version), so the next build is due without
    anyone passing --rebuild.
    """
    if not pinned:
        return None, f"{product} has no pinned version to release"

    existing = release.builds_of(tags, product, pinned)
    if existing:
        top = max(existing, key=lambda r: Version(r.version))
        if pins_changed_since and pins_changed_since(top.git_tag):
            return release.next_release(tags, product, pinned), (
                f"the pins changed since {top.git_tag} was cut"
            )
        return None, (
            f"{product} {pinned} is already released as {top.git_tag}; a "
            f"rebuild is deliberate — pass --rebuild to cut the next build"
        )

    return release.next_release(tags, product, pinned), (
        f"{product} pins {pinned}, which has no release tag yet"
    )


def decide_rebuild(product, pinned, tags):
    """(Release, reason) for an explicit rebuild of the pinned version."""
    if not pinned:
        return None, f"{product} has no pinned version to rebuild"
    existing = release.builds_of(tags, product, pinned)
    if not existing:
        return None, (
            f"{product} {pinned} has never been released, so there is nothing "
            f"to rebuild — cut the first release without --rebuild"
        )
    return release.next_release(tags, product, pinned), (f"rebuilding {product} {pinned}")


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
    chooser = decide_rebuild if rebuild else decide

    due = []
    for product in products:
        pin_file = None if rebuild else pins.pin_file(product)
        if pin_file is None:
            rel, reason = chooser(product, pins.pinned_version(product), tags)
        else:
            rel, reason = decide(
                product,
                pins.pinned_version(product),
                tags,
                pins_changed_since=lambda tag, f=pin_file: (
                    gitops.file_at(repo, f"refs/tags/{tag}", f) != Path(f).read_text()
                ),
            )
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
