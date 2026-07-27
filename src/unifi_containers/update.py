"""Cron driver: run one updater lane and open an auto-merging bump PR.

Every endpoint comes from Woodpecker's CI_* variables; needs the FORGEJO_TOKEN
secret.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from unifi_containers import forge, gitops, pins
from unifi_containers.updaters import network as network_updater
from unifi_containers.updaters import uos as uos_updater


@dataclass(frozen=True)
class Lane:
    bump: Callable[[], Optional[str]]
    prefix: str  # commit-subject and branch prefix
    files: tuple[str, ...]
    base: str  # branch the PR targets


LANES = {
    "stable": Lane(
        lambda: network_updater.bump(channel="stable", write=True),
        "network",
        ("network/Dockerfile", "README.md"),
        "main",
    ),
    "rc": Lane(
        lambda: network_updater.bump(channel="rc", write=True),
        "network",
        ("network/Dockerfile", "README.md"),
        "rc",
    ),
    "uos": Lane(
        lambda: uos_updater.bump(write=True),
        "unifi-os",
        ("unifi-os/pins.env", "README.md"),
        "main",
    ),
}


@dataclass(frozen=True)
class Bump:
    lane: str
    version: str  # 10.5.62-rc — what the tag will carry
    semver: str  # 10.5.62 — what the deb URL and pins carry
    prefix: str
    files: tuple[str, ...]
    base: str
    branch: str


def plan(lane_name, version):
    """The bump to make, or None when the updater found nothing newer."""
    lane = LANES.get(lane_name)
    if lane is None:
        raise ValueError(f"unknown lane: {lane_name}")
    if version is None:
        return None
    semver = version[: -len("-rc")] if version.endswith("-rc") else version
    return Bump(
        lane=lane_name,
        version=version,
        semver=semver,
        prefix=lane.prefix,
        files=lane.files,
        base=lane.base,
        branch=f"bump/{lane.prefix}-{version}",
    )


def reset_rc_branch(client, bump):
    """Recreate the disposable rc branch from main; False to stand down."""
    pinned = pins.pkgurl_version(client.raw_file("rc", "network/Dockerfile"))
    if pinned == bump.semver:
        print(f"rc lane already at {bump.semver}; nothing to do")
        return False
    client.delete_branch("rc")
    client.create_branch("rc", "main")
    return True


def open_pull(client, bump):
    """Open the bump PR, or find the one an earlier run left behind."""
    title = f"{bump.prefix}: bump to {bump.version}"
    number = client.create_pull(
        bump.base, bump.branch, title, f"Automated update ({bump.lane} lane)."
    )
    if number is None:
        number = client.find_pull(bump.branch)
    if number is None:
        raise forge.ForgeError("failed to create or find the bump PR")
    return number


def run_lane(lane):
    """Run one updater lane and open an auto-merging bump PR."""
    gitops.trust_workdir()
    repo = gitops.repository()
    target = gitops.push_target()

    # Start from this lane's base, not from wherever the previous lane finished.
    # The three lanes are consecutive steps in one workspace, so without this the
    # rc and uos bumps are computed against — and committed on top of — the
    # stable lane's bump, and the uos PR auto-merges that to main.
    gitops.reset_to_remote_branch(target.url, LANES[lane].base, token=target.token, repo=repo)

    version = LANES[lane].bump()
    print(f"{lane}: {version or 'up to date'}")

    bump = plan(lane, version)
    if bump is None:
        return 0

    client = forge.Forge.from_env()
    if bump.base == "rc" and not reset_rc_branch(client, bump):
        return 0

    gitops.checkout_new_branch(repo, bump.branch)
    message = f"{bump.prefix}: bump to {bump.version}"
    gitops.commit(repo, bump.files, message, gitops.signature(*gitops.IDENTITY))

    gitops.push(
        target.url,
        f"refs/heads/{bump.branch}:refs/heads/{bump.branch}",
        token=target.token,
        force=True,
        repo=repo,
    )

    number = open_pull(client, bump)
    client.merge_when_green(number)
    print(f"opened auto-merging PR #{number} ({bump.lane} {bump.version})")
    return 0
