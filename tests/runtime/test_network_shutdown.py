"""The shutdown ladder, against real processes.

A process that ignores SIGTERM is one line of shell, and only a real one proves
the escalation reaches a process *group* rather than just a pid.

Everything here is bounded: the tests assert wall-clock time as well as
outcome, because "shuts down eventually" is the failure mode the ladder exists
to prevent — `docker stop` sends SIGKILL after 10 seconds.
"""

import os
import signal
import subprocess
import sys
import time

import pytest
from conftest import REPO_ROOT

from unifi_runtime.entrypoint import network


@pytest.fixture(autouse=True)
def restored_signal_handlers():
    """Undo what the ladder installs, because SIG_IGN is inherited across exec.

    Leaking the SIG_IGN `stop()` sets out of one test makes every child a later
    test spawns immune to SIGTERM from birth.
    """
    saved = [(sig, signal.getsignal(sig)) for sig in network.STOP_SIGNALS]
    try:
        yield
    finally:
        for sig, handler in saved:
            signal.signal(sig, handler)


@pytest.fixture
def logged(monkeypatch):
    """Capture the entrypoint's log lines."""
    lines = []
    monkeypatch.setattr(network, "log", lines.append)
    return lines


def supervisor(tmp_path, argv, budget=2.0):
    return network.Supervisor(argv, cwd=str(tmp_path), rundir=str(tmp_path), budget=budget)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def assert_dies(pid):
    for _ in range(20):
        if not alive(pid):
            return
        time.sleep(0.1)
    raise AssertionError(f"pid {pid} is still alive")


#: Backgrounds a sleep whose pid it reports, so the group holds a process the
#: JVM's own pid does not reach. `wait` keeps the parent alive; without it the
#: parent exits and leaves the sleep behind in the group.
GRANDCHILD = 'trap "" TERM; sleep 30 & echo $! > "$1"; wait'


def spawn_with_grandchild(tmp_path, script=GRANDCHILD):
    """Start a supervisor whose tree has a grandchild. Returns it and that pid."""
    marker = tmp_path / "grandchild.pid"
    sup = supervisor(tmp_path, ["sh", "-c", script, "sh", str(marker)])
    sup.spawn()
    for _ in range(50):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.1)
    return sup, int(marker.read_text().strip())


def test_a_child_that_ignores_sigterm_is_killed_and_reported_as_137(tmp_path, logged):
    sup = supervisor(tmp_path, ["sh", "-c", 'trap "" TERM; sleep 30'])
    sup.spawn()

    started = time.monotonic()
    sup.stop()
    elapsed = time.monotonic() - started

    assert sup.exit_code == -int(signal.SIGKILL)
    assert network.exit_status(sup.exit_code) == 137
    # Every rung was tried, in order, before the kill.
    assert (tmp_path / "server.stop").exists()
    assert any("sent SIGTERM to process group" in line for line in logged)
    assert any("SIGKILL to process group" in line for line in logged)
    assert elapsed < 6.0


def test_the_kill_reaches_a_grandchild_that_terminate_would_miss(tmp_path):
    """mongod is a grandchild in the real image, so this is the shape.

    ace.jar spawns mongod, so signalling the JVM's pid — which is all
    `Popen.terminate()` does — leaves the database running. Only the process
    group reaches it, which is the entire reason for `start_new_session`.
    """
    sup, grandchild = spawn_with_grandchild(tmp_path)
    assert grandchild != sup.child.pid

    sup.stop()

    assert_dies(grandchild)


def test_terminate_alone_would_not_have_reached_it(tmp_path):
    """Same shape as above, but signalling the pid: the grandchild survives.

    If this ever stops holding, the group kill is no longer load-bearing.
    """
    sup, grandchild = spawn_with_grandchild(tmp_path)

    try:
        sup.child.terminate()
        time.sleep(0.5)
        assert alive(grandchild)
    finally:
        sup.signal_group(signal.SIGKILL)
        sup.child.wait(timeout=5)


def test_the_graceful_stop_alone_ends_a_child_that_watches_for_it(tmp_path, logged):
    """The vendor's mechanism: a server.stop file in the run directory.

    Measured against the real controller, the JVM exits about a second after
    this file appears, so the normal path never needs a SIGKILL.
    """
    stopfile = tmp_path / "server.stop"
    sup = supervisor(
        tmp_path,
        ["sh", "-c", 'while [ ! -f "$1" ]; do sleep 0.1; done', "sh", str(stopfile)],
    )
    sup.spawn()

    started = time.monotonic()
    sup.stop()
    elapsed = time.monotonic() - started

    assert sup.exit_code == 0
    assert not any("SIGKILL" in line for line in logged)
    assert elapsed < 3.0


def test_launcher_looping_is_removed_before_the_stop_file_is_written(tmp_path):
    # The vendor's launcher restarts the app while this file exists, so
    # writing server.stop without removing it first stops nothing.
    looping = tmp_path / "launcher.looping"
    looping.write_text("")
    sup = supervisor(tmp_path, ["true"])
    sup.request_graceful_stop()
    assert not looping.exists()
    assert (tmp_path / "server.stop").exists()


def test_the_group_is_still_signalled_after_the_controller_has_exited(tmp_path, logged):
    """The bug this ladder is shaped around: mongod outlives the JVM.

    Measured — after server.stop, java is gone in about a second and mongod is
    still running with no shutdown record. So an early return on "the child has
    exited" would leave the database to be SIGKILLed by docker.
    """
    # No `wait`: the parent exits immediately, leaving the sleep in the group.
    sup, grandchild = spawn_with_grandchild(tmp_path, 'sleep 30 & echo $! > "$1"')
    sup.child.wait(timeout=5)
    assert alive(grandchild)

    sup.stop()

    assert not sup.running
    assert any("sent SIGTERM to process group" in line for line in logged)
    assert_dies(grandchild)


def test_an_already_empty_group_is_not_reported_as_a_failure(tmp_path, logged):
    sup = supervisor(tmp_path, ["true"])
    sup.spawn()
    sup.child.wait(timeout=5)

    started = time.monotonic()
    sup.stop()

    assert sup.exit_code == 0
    # Nothing to signal, so nothing is waited for either.
    assert time.monotonic() - started < 1.0
    assert not any("killpg" in line for line in logged)
    assert not any("SIGTERM" in line for line in logged)


def test_the_childs_exit_status_is_reported_verbatim(tmp_path):
    sup = supervisor(tmp_path, ["sh", "-c", "exit 42"])
    sup.spawn()
    sup.child.wait(timeout=5)
    assert sup.exit_code == 42
    assert network.exit_status(sup.exit_code) == 42


def test_the_child_gets_a_session_of_its_own(tmp_path):
    # Without this the whole tree shares PID 1's process group, and killpg
    # from PID 1 would signal the entrypoint itself.
    sup = supervisor(tmp_path, ["sleep", "5"])
    sup.spawn()
    try:
        assert sup.pgid == sup.child.pid
        assert sup.pgid != os.getpgid(os.getpid())
    finally:
        sup.signal_group(signal.SIGKILL)
        sup.child.wait(timeout=5)


# --- signal handling ---------------------------------------------------


def test_a_stop_signal_during_the_wait_starts_the_ladder(tmp_path):
    """`run()` end to end, in a subprocess, because it installs signal handlers.

    PEP 475 has the blocking wait retry on EINTR, so only a *raising* handler is
    ever noticed. In-process this would also leave SIG_IGN behind for pytest.
    """
    ready = tmp_path / "spawned"
    script = (
        "import sys\n"
        "from unifi_runtime.entrypoint import network\n"
        "sup = network.Supervisor(\n"
        f"    ['sh', '-c', 'trap \"\" TERM; touch \"$1\"; sleep 30', 'sh', {str(ready)!r}],\n"
        f"    cwd={str(tmp_path)!r}, rundir={str(tmp_path)!r}, budget=2.0,\n"
        ")\n"
        "sys.exit(sup.run())\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src"))
    child = subprocess.Popen(
        [sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, text=True
    )
    # The handlers go in before the spawn, so the marker means both have happened.
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists()

    started = time.monotonic()
    child.terminate()
    status = child.wait(timeout=15)
    elapsed = time.monotonic() - started

    out = child.stdout.read()
    assert "caught SIGTERM; stopping" in out, out
    assert status == 137, out
    # `docker stop` allows 10s. Overrunning it means docker kills the
    # container mid-ladder and the clean shutdown never happens.
    assert elapsed < 8.0
