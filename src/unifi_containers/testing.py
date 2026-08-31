"""Support for the container integration tests: runtime contract, image pins, boot helper.

In the package rather than under tests/ so the contract is importable: ubitofu
carries a byte-for-byte fork of the capability list below.

On a daemon whose socket is not at /var/run/docker.sock — colima, lima, rootless
podman — testcontainers' reaper cannot bind-mount it and every boot fails at start.
Set `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import docker as docker_sdk
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HealthcheckWaitStrategy

from unifi_runtime import env
from unifi_runtime.seed import network_wizard

# --- the UniFi OS runtime contract ------------------------------------

#: systemd is PID 1 in the UOS images and will not boot without every one of
#: these. A missing capability shows up as unrelated misbehaviour, not a failed start.
UOS_CAPS = (
    "SYS_ADMIN",
    "NET_ADMIN",
    "NET_RAW",
    "NET_BIND_SERVICE",
    "DAC_OVERRIDE",
    "DAC_READ_SEARCH",
    "FOWNER",
    "CHOWN",
    "SETUID",
    "SETGID",
    "KILL",
    "SYS_CHROOT",
    "SYS_PTRACE",
    "SYS_RESOURCE",
    "AUDIT_WRITE",
    "MKNOD",
)

#: mountpoint -> mount options, the way docker-py wants them.
UOS_TMPFS = {
    "/run": "exec",
    "/run/lock": "",
    "/tmp": "exec",
    "/var/lib/journal": "",
    "/var/opt/unifi/tmp": "size=64m",
}

CGROUPFS = "/sys/fs/cgroup"


# --- image pins -------------------------------------------------------


def _pin(variable: str, default: str) -> str:
    """An image tag, overridable so a workflow can point a test at a just-built tag."""
    return os.environ.get(variable) or default


NETWORK_IMAGE = _pin("UNIFI_TEST_NETWORK_IMAGE", "ghcr.io/jamesbraid/unifi-network:10.4.57")
NETWORK_SIM_IMAGE = _pin(
    "UNIFI_TEST_NETWORK_SIM_IMAGE", "ghcr.io/jamesbraid/unifi-network:10.4.57-sim"
)
NETWORK_SEEDED_IMAGE = _pin(
    "UNIFI_TEST_NETWORK_SEEDED_IMAGE", "ghcr.io/jamesbraid/unifi-network:10.4.57-seeded"
)
UOS_IMAGE = _pin("UNIFI_TEST_UOS_IMAGE", "ghcr.io/jamesbraid/unifi-os-server:5.1.21")
UOS_SIM_IMAGE = _pin("UNIFI_TEST_UOS_SIM_IMAGE", "ghcr.io/jamesbraid/unifi-os-server:5.1.21-sim")
UOS_SEEDED_IMAGE = _pin(
    "UNIFI_TEST_UOS_SEEDED_IMAGE", "ghcr.io/jamesbraid/unifi-os-server:5.1.21-seeded"
)

#: The UOS healthchecks allow a 10-minute start period, so the wait has to
#: outlast it. The network images answer within a couple of minutes.
UOS_STARTUP_TIMEOUT = 900
NETWORK_STARTUP_TIMEOUT = 300

#: The Network App's HTTPS listener, EXPOSEd by the network images.
NETWORK_HTTPS_PORT = 8443
#: Baked into the seeded image at build time by make-seed.
NETWORK_SEEDED_PASSWORD = network_wizard.SEED_PASS

#: The seeded UOS variant cannot choose the key's value — ULP mints it — so the
#: published path and the key's name are the contract. Both come from the shipped
#: package: an assertion against a second copy would pass while the image failed.
UOS_API_KEY_FILE = env.KEY_FILE
UOS_API_KEY_NAME = env.SEED_DEFAULTS["UOS_API_KEY_NAME"]

#: mongod logs to a file, not stdout, so a clean shutdown is only visible here.
MONGOD_LOG = "/usr/lib/unifi/logs/mongod.log"
MONGOD_SHUTDOWN_MARKER = "mongod shutdown complete"


# --- booting ----------------------------------------------------------


def daemon_reachable() -> bool:
    """Whether there is a docker daemon to talk to at all."""
    try:
        docker_sdk.from_env().ping()
    except Exception:
        return False
    return True


def uos_container(image: str, **run_kwargs: Any) -> DockerContainer:
    """An unstarted UOS container carrying the runtime contract. No published ports.

    Extra `run_kwargs` are further docker-py `run()` keywords; naming one the
    contract already sets is a TypeError rather than a silent override.
    """
    container = DockerContainer(image)
    container.with_volume_mapping(CGROUPFS, CGROUPFS, "rw")
    for path, options in UOS_TMPFS.items():
        container.with_tmpfs_mount(path, options)
    container.with_kwargs(cgroupns="host", cap_drop=["ALL"], cap_add=list(UOS_CAPS), **run_kwargs)
    return container


def network_container(image: str, *ports: int) -> DockerContainer:
    """An unstarted Network App container with `ports` published on random host ports."""
    container = DockerContainer(image)
    if ports:
        container.with_exposed_ports(*ports)
    return container


def dump_boot_diagnostics(container: DockerContainer) -> None:
    """Whatever the never-healthy container can still tell us, to stderr.

    A wait timeout used to surface as "unhealthy, Output: " and nothing else —
    the 5.1.37 regression sat undiagnosed for nine days behind exactly that.
    Best effort throughout: the container may be dead, or not a systemd image.
    """
    try:
        wrapped = container.get_wrapped_container()
    except Exception:
        print("boot diagnostics: container never materialized", file=sys.stderr)
        return
    print(f"boot diagnostics for {wrapped.image}:", file=sys.stderr)
    try:
        print(wrapped.logs(tail=40).decode(errors="replace"), file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - diagnostics never mask the real failure
        print(f"docker logs unavailable: {exc}", file=sys.stderr)
    for label, cmd in (
        ("failed units", ["systemctl", "--failed", "--no-legend"]),
        ("journal tail", ["journalctl", "-p", "warning", "-n", "30", "--no-pager"]),
    ):
        try:
            code, output = wrapped.exec_run(cmd)
            print(f"--- {label} (rc={code})", file=sys.stderr)
            print(output.decode(errors="replace").strip(), file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"--- {label}: exec failed: {exc}", file=sys.stderr)


@contextmanager
def booted(container: DockerContainer, timeout: int) -> Iterator[DockerContainer]:
    """Start `container`, wait for its own healthcheck, and always tear it down."""
    container.waiting_for(HealthcheckWaitStrategy().with_startup_timeout(timeout))
    try:
        with container as running:
            yield running
    except RuntimeError:
        # The wait strategy's verdict, not a test assertion: grab evidence
        # from the container before the context manager tears it down.
        dump_boot_diagnostics(container)
        raise


def restart(container: DockerContainer, timeout: int) -> None:
    """Restart and wait for health. `timeout` is the health wait, not the stop grace."""
    container.get_wrapped_container().restart()
    HealthcheckWaitStrategy().with_startup_timeout(timeout).wait_until_ready(container)


def stop(container: DockerContainer) -> int:
    """`docker stop` the container and return its exit status, a smoke signal only."""
    wrapped = container.get_wrapped_container()
    wrapped.stop(timeout=10)
    wrapped.wait()
    wrapped.reload()
    return int((wrapped.attrs.get("State") or {}).get("ExitCode", -1))


# --- looking inside ---------------------------------------------------


def exec_in(container: DockerContainer, argv: list[str]) -> str:
    """Run a command inside a running container. Raises on a non-zero exit."""
    result = container.exec(argv)
    output = result.output.decode("utf-8", "replace")
    if result.exit_code != 0:
        raise RuntimeError(f"{' '.join(argv)} -> rc={result.exit_code}: {output[:500]}")
    return output


def read_file(container: DockerContainer, path: str) -> str | None:
    """A file's contents from inside a running container, or None when it is absent."""
    result = container.exec(["cat", path])
    if result.exit_code != 0:
        return None
    return result.output.decode("utf-8", "replace")


def archived_file(container: DockerContainer, path: str) -> str:
    """A file's contents copied out of the container — works when it is stopped."""
    stream, _ = container.get_wrapped_container().get_archive(path)
    with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as archive:
        member = archive.next()
        handle = archive.extractfile(member) if member else None
        if handle is None:
            raise RuntimeError(f"{path} is not a regular file in the container")
        return handle.read().decode("utf-8", "replace")


def http_status(container: DockerContainer, url: str, headers: dict[str, str] | None = None) -> str:
    """Status of an HTTP request from inside the container; curl's own failures give "000"."""
    argv = ["curl", "-ks", "--max-time", "15", "-o", "/dev/null", "-w", "%{http_code}"]
    for name, value in (headers or {}).items():
        argv += ["-H", f"{name}: {value}"]
    argv.append(url)
    return container.exec(argv).output.decode("utf-8", "replace").strip() or "000"
