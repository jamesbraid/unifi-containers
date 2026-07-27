"""Docker helpers for the CI lanes, on docker-py."""

import time

import docker as sdk
from docker.errors import DockerException

#: Long enough for `images.load` on a several-hundred-MB rootfs tarball.
API_TIMEOUT = 600
POLL_SECONDS = 5

_client = None


class DockerError(RuntimeError):
    """A docker operation failed."""


def client():
    """The shared daemon connection, opened on first use so tests need no daemon."""
    global _client
    if _client is None:
        try:
            _client = sdk.from_env(timeout=API_TIMEOUT)
        except DockerException as exc:
            raise DockerError(f"cannot reach the docker daemon: {exc}") from exc
    return _client


def inspect_state(name):
    """Return (health, running); health is "missing" when uninspectable, "none" with no check."""
    try:
        state = client().containers.get(name).attrs.get("State") or {}
    except DockerException:
        return "missing", False
    health = (state.get("Health") or {}).get("Status") or "none"
    return health, bool(state.get("Running"))


def logs_tail(name):
    """The last 50 lines of a container's logs, stdout and stderr."""
    try:
        return client().containers.get(name).logs(tail=50).decode("utf-8", "replace")
    except (DockerError, DockerException) as exc:
        return f"no logs for {name}: {exc}"


def load_image(tar_path):
    """`docker load` an image tarball, returning the loaded Image."""
    with open(str(tar_path), "rb") as handle:
        try:
            loaded = client().images.load(handle)
        except DockerException as exc:
            raise DockerError(f"loading {tar_path} failed: {exc}") from exc
    images = list(loaded)
    if len(images) != 1:
        raise DockerError(f"{tar_path} holds {len(images)} images, expected exactly one")
    return images[0]


def wait_healthy(
    name,
    timeout,
    inspect=inspect_state,
    sleep=time.sleep,
    clock=time.monotonic,
    logs=logs_tail,
    out=print,
):
    """Poll until a container is healthy. On failure the last log lines go to `out`."""
    start = clock()
    while True:
        health, running = inspect(name)
        elapsed = int(clock() - start)
        # Order matters: healthy wins over an expired deadline, because it passed
        # whatever the clock says.
        if health == "healthy":
            out(f"healthy after {elapsed}s")
            return True
        if not running:
            out(f"container stopped before becoming healthy (status={health})")
            out(logs(name))
            return False
        if elapsed >= timeout:
            out(f"timed out after {timeout}s (status={health})")
            out(logs(name))
            return False
        sleep(POLL_SECONDS)
