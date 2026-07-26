#!/usr/bin/env python3
"""Cut a rebuild release: same upstream version, next packaging revision.

    rebuild-tag.py unifi-os [--push]
    rebuild-tag.py network  [--push]

Version bumps are automated end to end: an updater rewrites the pins, and
mint-tag.sh mints X.Y.Z-1 from the bump commit. Rebuilds are not. An
image-side fix ships no new upstream version, so it needs a new packaging
revision on the same X.Y.Z — X.Y.Z-1 to X.Y.Z-2 — which this mints from the
tags that already exist. Pushing the tag syncs the GitHub mirror (see
.woodpecker/sync-tag.yml), and the release workflow builds and publishes.

Prints the plan and stops unless --push is given.

stdlib only.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

# product -> (tag prefix, pin file, pin key). The pin is a guard, not the
# source of the version: it catches a rebuild attempted when the pins have
# moved ahead of the tags, which wants a bump instead.
PRODUCTS = {
    "unifi-os": ("unifi-os-v", "unifi-os/pins.env", "UOS_VERSION"),
    "network": ("v", None, None),
}

TAG_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<rev>\d+))?$")


def parse_release_tags(tags, prefix):
    """Return [((major,minor,patch), revision, tag)] for stable release tags.

    Pure. Skips rc tags, other products' tags, and anything unparseable. Tags
    predating the packaging-revision scheme carry no -N and count as revision
    1, which is what they published as.
    """
    found = []
    for tag in tags:
        if not tag.startswith(prefix):
            continue
        rest = tag[len(prefix):]
        match = TAG_RE.match(rest)
        if not match:
            continue
        version = tuple(int(match.group(p)) for p in ("major", "minor", "patch"))
        revision = int(match.group("rev") or 1)
        found.append((version, revision, tag))
    return found


def next_rebuild_tag(tags, prefix):
    """Return (new_tag, version_string, revision) for the highest release.

    Pure. Raises when no release tag exists, since there is nothing to rebuild.
    """
    found = parse_release_tags(tags, prefix)
    if not found:
        raise RuntimeError(
            f"no release tags matching {prefix!r} — nothing to rebuild; cut a "
            f"version bump first"
        )
    version, revision, _ = max(found, key=lambda item: (item[0], item[1]))
    version_string = ".".join(str(part) for part in version)
    return f"{prefix}{version_string}-{revision + 1}", version_string, revision + 1


def read_pin(pin_file, key):
    """Return the pinned version from a pins.env-style file."""
    for line in Path(pin_file).read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip()
    raise RuntimeError(f"{pin_file} has no {key}")


def git(*args):
    proc = subprocess.run(["git", *args], capture_output=True, text=True,
                          check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product", choices=sorted(PRODUCTS))
    parser.add_argument("--push", action="store_true",
                        help="create and push the tag (otherwise print only)")
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)

    prefix, pin_file, pin_key = PRODUCTS[args.product]
    tags = git("tag", "-l").split()
    tag, version, revision = next_rebuild_tag(tags, prefix)

    if pin_file:
        pinned = read_pin(pin_file, pin_key)
        if pinned != version:
            print(f"{pin_file} pins {pinned} but the highest {args.product} tag "
                  f"is {version} — that is a version bump, not a rebuild",
                  file=sys.stderr)
            return 1

    head = git("rev-parse", "--short", "HEAD").strip()
    print(f"{args.product}: rebuild {version} as revision {revision} -> {tag} "
          f"at {head}")
    if not args.push:
        print("dry run; pass --push to tag and publish")
        return 0

    git("tag", "-a", tag, "-m", f"{version}-{revision} ({tag})")
    git("push", args.remote, f"refs/tags/{tag}")
    print(f"pushed {tag}; the tag pipeline syncs the mirror and the release "
          f"workflow publishes the images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
