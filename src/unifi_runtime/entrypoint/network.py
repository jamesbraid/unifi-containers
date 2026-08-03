"""Container entrypoint for the standalone UniFi Network Application.

Follows Ubiquiti's `unifi.init` and `unifi-network-service-helper`.

mongod is a grandchild, spawned by ace.jar, and it outlives the JVM. It shuts
itself down cleanly on SIGTERM — but only `killpg` reaches a grandchild, so the
controller is started in a session of its own and the whole group is signalled.
"""

import os
import shlex
import signal
import string
import subprocess
import sys
import time

from .. import healthcheck, readyz, sysprops

#: The deb's install prefix. Its data/logs/run entries are symlinks that the
#: vendor helper maintains; everything persistent lives under /unifi.
BASEDIR = "/usr/lib/unifi"
DATADIR = "/unifi/data"
LOGDIR = "/unifi/log"
RUNDIR = "/unifi/run"

HELPER = "/usr/sbin/unifi-network-service-helper"
JAVA = "/usr/bin/java"

SYSPROPS_PATH = DATADIR + "/system.properties"
SYSTEM_ENV_PATH = DATADIR + "/system_env"

#: Hooks a derived image drops in here run after the vendor init and before
#: the JVM. /unifi is a VOLUME, so an image cannot COPY hooks under it.
INIT_HOOKS = "/usr/local/unifi/init.d"

UMASK = 0o027

#: The only JVM knob, under Ubiquiti's own name and with their default.
DEFAULT_JVM_OPTS = "-Xmx1024M -XX:+UseParallelGC"

#: Java 25 denies reflective access to these packages by default and the
#: application needs every one. A dropped entry is not a startup failure — it
#: surfaces later as an unrelated-looking runtime error.
ADD_OPENS = (
    "java.base/java.lang",
    "java.base/java.time",
    "java.base/sun.security.util",
    "java.base/java.io",
    "java.rmi/sun.rmi.transport",
)

#: Ports the image documents and EXPOSEs. Written only when absent, so the
#: application and a seeded volume both keep the last word.
SYSPROPS_DEFAULTS = {
    "unifi.http.port": "8080",
    "unifi.https.port": "8443",
}

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

#: `docker stop` allows 10s before SIGKILL. The rungs below have to fit inside
#: that or the clean shutdown this module exists for never completes.
SHUTDOWN_BUDGET = 8.0
#: How long the JVM gets to act on the vendor's server.stop file. Measured at
#: about a second against the real controller.
GRACEFUL_WAIT = 3.0
#: How long the database gets after the group SIGTERM. A budgeted pause rather
#: than a poll: mongod is a grandchild, so its exit is nobody's to wait for.
DB_WAIT = 3.0
#: Extra time allowed after the SIGKILL backstop, purely to reap.
KILL_GRACE = 1.0


def log(message):
    """One line to docker logs. Unbuffered: PID 1 often dies mid-line."""
    sys.stdout.write(f"unifi-entrypoint: {message}\n")
    sys.stdout.flush()


# --- the java command line --------------------------------------------


def java_argv(
    jvm_opts=None, basedir=BASEDIR, datadir=DATADIR, logdir=LOGDIR, rundir=RUNDIR, java=JAVA
):
    """Ubiquiti's invocation, in their order."""
    argv = [
        java,
        "-Dfile.encoding=UTF-8",
        "-Djava.awt.headless=true",
        "-Dapple.awt.UIElement=true",
        # unifi-core is the UniFi OS side-car. There is none in this image,
        # and the vendor default is false regardless.
        "-Dunifi.core.enabled=false",
    ]
    argv += shlex.split(jvm_opts or DEFAULT_JVM_OPTS)
    argv += [
        # An out-of-memory JVM must take the container down. A wedged one
        # just makes the healthcheck time out and hides the cause.
        "-XX:+ExitOnOutOfMemoryError",
        "-XX:+CrashOnOutOfMemoryError",
        f"-XX:ErrorFile={basedir}/logs/hs_err_pid%p.log",
        f"-Dunifi.datadir={datadir}",
        f"-Dunifi.logdir={logdir}",
        f"-Dunifi.rundir={rundir}",
    ]
    for package in ADD_OPENS:
        argv += ["--add-opens", f"{package}=ALL-UNNAMED"]
    argv += ["-jar", f"{basedir}/lib/ace.jar", "start"]
    return argv


def jvm_opts(env, system_env_path=SYSTEM_ENV_PATH):
    """Resolve UNIFI_JVM_OPTS: system_env beats the process environment.

    `unifi.init` sources system_env last, so `unifi.xmx=512` in
    system.properties beats `docker run -e`.
    """
    from_file = _read_shell_assignments(system_env_path).get("UNIFI_JVM_OPTS")
    return from_file or env.get("UNIFI_JVM_OPTS") or DEFAULT_JVM_OPTS


def _read_shell_assignments(path):
    """One plain `KEY=value` per line, as the vendor helper writes them. Not a shell parser."""
    values: dict[str, str] = {}
    try:
        with open(path) as handle:
            text = handle.read()
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        try:
            words = shlex.split(value)
        except ValueError:
            continue
        values[key.strip()] = " ".join(words)
    return values


# --- setup steps -------------------------------------------------------


def apply_vendor_paths(env):
    """Point the vendor tooling at the /unifi volume."""
    env["UNIFI_DATA_DIR"] = DATADIR
    env["UNIFI_LOG_DIR"] = LOGDIR
    env["UNIFI_RUN_DIR"] = RUNDIR


def vendor_init(helper=HELPER):
    """Run Ubiquiti's own setup step."""
    if not os.path.exists(helper):
        log(f"{helper} is missing; skipping vendor init")
        return None
    status = subprocess.call([helper, "init"])
    log(f"vendor init exited {status}")
    return status


def write_system_properties(path=SYSPROPS_PATH, settings=None):
    """Add the documented ports only if absent; the application rewrites this file itself."""
    settings = SYSPROPS_DEFAULTS if settings is None else settings
    try:
        with open(path) as handle:
            existing = handle.read()
    except OSError:
        existing = ""
    merged = sysprops.merge(existing, settings, only_if_absent=True)
    if merged == existing:
        return merged
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as handle:
        handle.write(merged)
    return merged


#: run-parts' own rule for which filenames count, so a `.dpkg-dist` or an
#: editor backup left in the hook dir is ignored rather than executed.
_HOOK_NAME_CHARS = frozenset(string.ascii_letters + string.digits + "_-")


def clear_readiness_state(paths=healthcheck.BOOT_STATE):
    """Make this boot re-prove readiness from scratch.

    The markers turn the healthcheck into a file test once readiness is proven,
    so none of them may outlive the boot that proved it. Only UniFi OS mounts
    /tmp as a tmpfs; here it is the container's writable layer, which `docker
    restart` keeps — leaving a restarted controller healthy from its first
    probe, before it can serve anything.

    The session goes with them. A surviving login marker would skip
    authentication on the next boot and leave the later stages probing with a
    cookie the restarted controller has never heard of.
    """
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def run_hooks(directory=INIT_HOOKS):
    """Execute the init hooks in name order."""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    ran = []
    for name in names:
        path = os.path.join(directory, name)
        if not set(name) <= _HOOK_NAME_CHARS:
            continue
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        status = subprocess.call([path])
        if status != 0:
            # A hook is what makes a variant that variant: the sim image's
            # demo-mode writes the simulation keys. Starting the controller
            # without them yields an image whose credentials never work and
            # whose healthcheck never goes green, with one log line as the
            # only evidence. The UniFi OS entrypoint already refuses this.
            raise RuntimeError(f"init hook {name} failed (rc={status})")
        log(f"init hook {name} exited {status}")
        ran.append(name)
    return ran


# --- supervision -------------------------------------------------------


def exit_status(code):
    """Shell convention, 128 + signal number: `sys.exit(-9)` makes docker record 247, not 137."""
    if code is None:
        return 128 + int(signal.SIGTERM)
    return 128 + (-code) if code < 0 else code


class Supervisor:
    """Runs the controller as PID 1 and takes its whole process group down."""

    def __init__(self, argv, cwd=BASEDIR, rundir=RUNDIR, budget=SHUTDOWN_BUDGET):
        self.argv = list(argv)
        self.cwd = cwd
        self.rundir = rundir
        self.budget = budget
        self.child = None
        self.pgid = None

    @property
    def exit_code(self):
        return None if self.child is None else self.child.returncode

    @property
    def running(self):
        return self.child is not None and self.child.poll() is None

    # --- lifecycle ---

    def spawn(self):
        """Start the controller in a session of its own, and return it.

        `start_new_session` earns its place twice: it gives the tree a group of
        its own to signal, and it keeps that signal off this process.
        """
        child = subprocess.Popen(self.argv, cwd=self.cwd, start_new_session=True)
        self.child = child
        try:
            self.pgid = os.getpgid(child.pid)
        except OSError:
            # It exited before we looked. setsid already made pid == pgid.
            self.pgid = child.pid
        return child

    def run(self):
        """Supervise until a stop signal or the controller's own exit."""
        _install_stop_handlers()
        try:
            child = self.spawn()
            log(f"controller running as pid {child.pid} (process group {self.pgid})")
            child.wait()
            log(f"controller exited with {self.exit_code}")
        except _Stop as stop:
            log(f"caught {stop.name()}; stopping")
        self.stop()
        status = exit_status(self.exit_code)
        log(f"exiting {status}")
        return status

    # --- the shutdown ladder ---

    def stop(self):
        """Down the tree within the budget, loudest rung last.

        The group is signalled whether or not the JVM is still up: measured,
        after server.stop the JVM is gone in about a second and mongod is still
        running with no shutdown record.
        """
        _ignore_stop_signals()
        deadline = time.monotonic() + self.budget
        if self.running:
            self.request_graceful_stop()
            self._wait_for_exit(deadline, GRACEFUL_WAIT)
        if self.signal_group(signal.SIGTERM):
            log(f"sent SIGTERM to process group {self.pgid}")
            _pause_until(deadline, DB_WAIT)
        if self.running:
            log(f"controller ignored SIGTERM; SIGKILL to process group {self.pgid}")
            self.signal_group(signal.SIGKILL)
            # Deliberately past the budget: SIGKILL always lands, and reaping
            # it is the difference between reporting 137 and reporting a
            # made-up 143. Still inside `docker stop`'s grace period.
            self._wait_for_exit(time.monotonic() + KILL_GRACE, KILL_GRACE)
        return self.exit_code

    def request_graceful_stop(self):
        """The vendor's documented stop. launcher.looping goes first so nothing restarts it."""
        try:
            os.remove(os.path.join(self.rundir, "launcher.looping"))
        except OSError:
            pass
        stopfile = os.path.join(self.rundir, "server.stop")
        try:
            with open(stopfile, "w"):
                pass
            os.chmod(stopfile, 0o640)
        except OSError as exc:
            log(f"could not write {stopfile}: {exc}")
            return False
        log(f"requested graceful stop via {stopfile}")
        return True

    def signal_group(self, sig):
        """Signal every process in the controller's group. False if there are none left."""
        if self.pgid is None:
            return False
        try:
            os.killpg(self.pgid, sig)
        except ProcessLookupError:
            # An empty group is the outcome this was aiming for, not a fault.
            return False
        except OSError as exc:
            log(f"killpg({self.pgid}, {int(sig)}): {exc}")
            return False
        return True

    def _wait_for_exit(self, deadline, want):
        """Wait for the controller, capped by both `want` seconds and the deadline."""
        if self.child is None:
            return True
        try:
            self.child.wait(timeout=max(0.0, min(want, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            return False
        return True


def _pause_until(deadline, want):
    """A flat pause of `want` seconds, never past the deadline."""
    remaining = min(want, deadline - time.monotonic())
    if remaining > 0:
        time.sleep(remaining)


class _Stop(Exception):
    """A stop signal arrived. Raised from the handler; caught only in `run`."""

    def name(self):
        return signal.Signals(self.args[0]).name


def _raise_stop(signum, frame):
    raise _Stop(signum)


def _install_stop_handlers():
    """A handler that raises, because both quieter designs are wrong.

    The kernel discards a default-action signal aimed at a PID-namespace init,
    so with no handler `docker stop` waits out its grace period and reports 137.
    PEP 475 rules out a flag-setting handler: it makes the blocking wait retry
    on EINTR, so nothing ever notices the flag.
    """
    for sig in STOP_SIGNALS:
        signal.signal(sig, _raise_stop)


def _ignore_stop_signals():
    """Stop reacting once the ladder is running: a second SIGTERM must not restart it."""
    for sig in STOP_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)


# --- entry point -------------------------------------------------------


def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    if argv and argv[0] != "unifi":
        # Anything else is someone asking for a shell. Hand the container
        # over rather than pretending to be an init system for it.
        os.execvp(argv[0], argv)

    os.umask(UMASK)
    clear_readiness_state()
    # Directly after the state is cleared and before anything slow: a caller
    # polling from second zero should get an honest 503, not a refused
    # connection it has to tell apart from "wrong port". Starting it any earlier
    # would risk serving the last boot's marker as this boot's verdict.
    #
    # A daemon thread, because this process stays PID 1 and supervises the JVM
    # rather than exec'ing it, so the endpoint lives as long as the container.
    readyz.start_configured(env)
    apply_vendor_paths(env)
    vendor_init()
    write_system_properties()
    run_hooks()
    return Supervisor(java_argv(jvm_opts(env))).run()


if __name__ == "__main__":
    sys.exit(main())
