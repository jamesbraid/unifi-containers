#!/usr/bin/env python3
"""Docker helpers shared by the CI gates.

Everything the image gates need to poll a container, read a file out of it,
and probe an HTTP endpoint from inside it. The container's own network
namespace is used deliberately: `run-uos.sh` publishes no ports, so the only
address that works in both CI and a local run is 127.0.0.1 *inside* the
container.

The decision logic is separated from the subprocess calls so it can be tested
without Docker: `classify()` is pure, and `wait_healthy()` takes its clock,
sleep, and inspector as arguments.

stdlib only.
"""
import json
import subprocess
import time

DEFAULT_TIMEOUT = 600
POLL_SECONDS = 5

# classify() verdicts
HEALTHY = "healthy"
DIED = "died"
TIMEOUT = "timeout"
WAIT = "wait"


class DockerError(RuntimeError):
    """A docker command failed."""


def run(argv, timeout=120):
    """Run a command, returning the CompletedProcess. Never raises on rc."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )


def inspect_state(name):
    """Return (health, running) for a container.

    health is docker's health status, or "missing" when the container is not
    inspectable and "none" when it has no healthcheck.
    """
    proc = run(["docker", "inspect", "--format", "{{json .State}}", name])
    if proc.returncode != 0:
        return "missing", False
    try:
        state = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return "missing", False
    health = (state.get("Health") or {}).get("Status") or "none"
    return health, bool(state.get("Running"))


def logs_tail(name, lines=50):
    """Return the last `lines` of a container's logs, stdout and stderr."""
    proc = run(["docker", "logs", "--tail", str(lines), name])
    return (proc.stdout or "") + (proc.stderr or "")


def exec_in(name, argv, timeout=60):
    """Run a command inside a container. Raises DockerError on failure."""
    proc = run(["docker", "exec", name, *argv], timeout=timeout)
    if proc.returncode != 0:
        raise DockerError(
            f"docker exec {name} {' '.join(argv)} -> rc={proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:500]}"
        )
    return proc.stdout


def http_status(name, url, headers=None, timeout=15):
    """Return the HTTP status of a request made from inside the container.

    Curl's own failures collapse to "000", which callers must treat as "no
    verdict" rather than as a rejection.
    """
    argv = ["curl", "-ks", "--max-time", str(timeout), "-o", "/dev/null",
            "-w", "%{http_code}"]
    for key, value in (headers or {}).items():
        argv += ["-H", f"{key}: {value}"]
    argv.append(url)
    proc = run(["docker", "exec", name, *argv], timeout=timeout + 15)
    return (proc.stdout or "").strip() or "000"


def read_file(name, path):
    """Return a file's contents from inside the container, or None if absent."""
    proc = run(["docker", "exec", name, "cat", path])
    if proc.returncode != 0:
        return None
    return proc.stdout


def restart(name, timeout=180):
    """Restart a container. Raises DockerError on failure."""
    proc = run(["docker", "restart", name], timeout=timeout)
    if proc.returncode != 0:
        raise DockerError(
            f"docker restart {name} -> rc={proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:500]}"
        )


def classify(health, running, elapsed, timeout):
    """Decide what a poll of (health, running) means. Pure.

    Healthy wins over an expired deadline: a container that just went healthy
    has passed, whatever the clock says.
    """
    if health == HEALTHY:
        return HEALTHY
    if not running:
        return DIED
    if elapsed >= timeout:
        return TIMEOUT
    return WAIT


def wait_healthy(name, timeout=DEFAULT_TIMEOUT, poll=POLL_SECONDS,
                 inspect=inspect_state, sleep=time.sleep,
                 clock=time.monotonic, logs=logs_tail, out=print):
    """Poll until a container is healthy. Return True on healthy.

    On failure the last 50 log lines go to `out`, because a gate that fails
    without saying why costs more than the run it blocked.
    """
    start = clock()
    while True:
        health, running = inspect(name)
        elapsed = int(clock() - start)
        verdict = classify(health, running, elapsed, timeout)
        if verdict == HEALTHY:
            out(f"healthy after {elapsed}s")
            return True
        if verdict == DIED:
            out(f"container stopped before becoming healthy (status={health})")
            out(logs(name))
            return False
        if verdict == TIMEOUT:
            out(f"timed out after {timeout}s (status={health})")
            out(logs(name))
            return False
        sleep(poll)
