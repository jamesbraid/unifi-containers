"""Produce seed.tgz for the seeded network variant.

Boots the base image against a scratch volume, completes the first-run wizard,
clean-stops, and snapshots /unifi for the seeded Dockerfile to ADD.
"""

import os
import subprocess
import sys
from pathlib import Path

from unifi_containers import docker
from unifi_runtime.seed import network_wizard as wizard

#: src/unifi_containers/ -> the repo root, where the image directories live.
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "network" / "seeded" / "seed.tgz"
PORT = 48443
HEALTH_TIMEOUT = 720
#: The controller needs room to close mongodb cleanly; a snapshot of a
#: hard-killed database is the one thing a seeded image must not contain.
STOP_TIMEOUT = 120


def discard(teardown):
    """Best-effort cleanup: a leaked container or volume must not mask the real failure."""
    try:
        teardown()
    except Exception as exc:  # noqa: BLE001 - nothing here is worth failing over
        print(f"warning: cleanup failed: {exc}", file=sys.stderr)


def snapshot(volume, out_path):
    """Tar a volume to a file, renamed into place: a truncated seed.tgz still ADDs cleanly.

    A subprocess, not the SDK: docker-py buffers a container's stdout in memory
    and this tarball is hundreds of megabytes.
    """
    partial = out_path.with_name(out_path.name + ".partial")
    argv = ["docker", "run", "--rm", "-v", f"{volume}:/unifi", "alpine"]
    argv += ["tar", "-czf", "-", "-C", "/unifi", "."]
    with open(str(partial), "wb") as out:
        proc = subprocess.run(argv, stdout=out, check=False)
    if proc.returncode != 0:
        partial.unlink()
        raise docker.DockerError(f"snapshotting {volume} exited {proc.returncode}")
    partial.replace(out_path)


def build(base_image, out=None):
    """Boot the base image, complete the wizard, snapshot /unifi to `out`."""
    out = DEFAULT_OUT if out is None else Path(out)
    name = f"seedgen-{os.getpid()}"
    volume = f"seedvol-{os.getpid()}"

    client = docker.client()
    client.volumes.create(volume)
    try:
        container = client.containers.run(
            base_image,
            name=name,
            detach=True,
            ports={"8443/tcp": PORT},
            volumes={volume: {"bind": "/unifi", "mode": "rw"}},
        )

        print("==> waiting for controller to become healthy")
        if not docker.wait_healthy(name, HEALTH_TIMEOUT):
            return 1

        wizard.seed(f"https://localhost:{PORT}", wizard.SEED_USER, wizard.SEED_PASS)

        # Stop, but leave removal to the `finally`: removing here too made every
        # successful run print a cleanup warning for the container it had removed.
        print("==> clean stop + snapshot")
        container.stop(timeout=STOP_TIMEOUT)
        out.parent.mkdir(parents=True, exist_ok=True)
        snapshot(volume, out)
    except wizard.WizardError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        discard(lambda: client.containers.get(name).remove(force=True))
        discard(lambda: client.volumes.get(volume).remove(force=True))

    print(f"==> wrote {out} ({out.stat().st_size // (1 << 20)} MiB)")
    return 0
