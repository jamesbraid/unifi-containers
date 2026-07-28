"""Point the sliding GHCR tags at a release's just-pushed digests.

`release_plan` decides which tags move; this only applies the decision.
"""

import re
import subprocess
import sys

#: The unqualified names, which only the highest stable release carries.
GLOBAL_TRACKS = {
    "latest": "base",
    "sim": "sim",
    "seeded": "seeded",
}

#: `10.4.57`, `10.4.57-sim`, `10.4.57-seeded`. Build numbers are a
#: git concept and never reach a registry, so a `-2` here is a mistake.
VERSION_TAG_RE = re.compile(r"^\d+\.\d+\.\d+(?:-(?:sim|seeded))?$")


def variant_for(name):
    """Which pushed digest a tag name should track."""
    if name in GLOBAL_TRACKS:
        return GLOBAL_TRACKS[name]
    if not VERSION_TAG_RE.match(name):
        raise ValueError(
            f"unknown sliding tag {name!r}; expected one of "
            f"{', '.join(sorted(GLOBAL_TRACKS))}, or <version>[-sim|-seeded]"
        )
    if name.endswith("-sim"):
        return "sim"
    if name.endswith("-seeded"):
        return "seeded"
    return "base"


def imagetools_create(image, tag, digest):
    """Point image:tag at image@digest."""
    argv = ["docker", "buildx", "imagetools", "create", "--tag", f"{image}:{tag}"]
    proc = subprocess.run(argv + [f"{image}@{digest}"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"imagetools create {image}:{tag} -> {digest} failed: "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )


def slide(image, names, digests, create=imagetools_create, out=print):
    """Move every named sliding tag. A requested name with no digest is a failure.

    release_plan only asks for a variant it also built — an RC asks for `base`
    alone — so a missing digest means a workflow output went astray. Skipping it
    would leave that name on the previous image while the run reported success.
    """
    moved = []
    for name in names:
        variant = variant_for(name)
        digest = digests.get(variant)
        if not digest:
            raise ValueError(
                f"{name!r} tracks the {variant} variant but no {variant} digest was "
                f"given; refusing to leave :{name} on the previous image"
            )
        out(f"slide :{name} -> {digest}")
        create(image, name, digest)
        moved.append(name)
    return moved


def apply(image, tags, base=None, sim=None, seeded=None):
    """Point each sliding name at the digest of the variant it tracks."""
    names = tags.split()
    if not names:
        print("no sliding tags to move")
        return 0

    digests = {"base": base, "sim": sim, "seeded": seeded}
    try:
        moved = slide(image, names, digests)
    except (RuntimeError, ValueError) as exc:
        print(f"slide-tags: {exc}", file=sys.stderr)
        return 1
    print(f"moved {len(moved)}/{len(names)} sliding tags")
    return 0
