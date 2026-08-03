"""The parts of the network entrypoint that can be wrong silently.

The java command line is the headline. A dropped `--add-opens` does not
fail at startup on Java 25 — it surfaces later as an unrelated-looking
reflection error deep in the application, which is exactly the sort of
regression no integration gate catches and no reviewer spots in a diff.
"""

import signal

import pytest

from unifi_runtime import sysprops
from unifi_runtime.entrypoint import network

REQUIRED_FLAGS = [
    "-Dfile.encoding=UTF-8",
    "-Djava.awt.headless=true",
    "-Dapple.awt.UIElement=true",
    "-Dunifi.core.enabled=false",
    "-XX:+ExitOnOutOfMemoryError",
    "-XX:+CrashOnOutOfMemoryError",
    "-XX:ErrorFile=/usr/lib/unifi/logs/hs_err_pid%p.log",
    "-Dunifi.datadir=/unifi/data",
    "-Dunifi.logdir=/unifi/log",
    "-Dunifi.rundir=/unifi/run",
]

ADD_OPENS_SPECS = [
    "java.base/java.lang=ALL-UNNAMED",
    "java.base/java.time=ALL-UNNAMED",
    "java.base/sun.security.util=ALL-UNNAMED",
    "java.base/java.io=ALL-UNNAMED",
    "java.rmi/sun.rmi.transport=ALL-UNNAMED",
]


@pytest.mark.parametrize("flag", REQUIRED_FLAGS)
def test_java_argv_carries_every_required_flag(flag):
    assert flag in network.java_argv()


@pytest.mark.parametrize("spec", ADD_OPENS_SPECS)
def test_java_argv_pairs_each_add_opens_with_its_flag(spec):
    argv = network.java_argv()
    # Adjacency matters: `--add-opens` takes the next argv entry, so a
    # mis-ordered list would still contain both strings and still be wrong.
    assert argv[argv.index(spec) - 1] == "--add-opens"


def test_java_argv_opens_exactly_the_five_vendor_packages():
    argv = network.java_argv()
    assert argv.count("--add-opens") == len(ADD_OPENS_SPECS)


def test_java_argv_launches_ace_jar_last():
    assert network.java_argv()[-3:] == ["-jar", "/usr/lib/unifi/lib/ace.jar", "start"]


def test_java_argv_defaults_to_the_vendor_heap_and_collector():
    argv = network.java_argv()
    assert "-Xmx1024M" in argv
    assert "-XX:+UseParallelGC" in argv


def test_jvm_opts_override_replaces_the_default_entirely():
    argv = network.java_argv("-Xmx256M -XX:+UseSerialGC")
    assert "-Xmx256M" in argv
    assert "-XX:+UseSerialGC" in argv
    assert "-Xmx1024M" not in argv
    assert "-XX:+UseParallelGC" not in argv
    # Overriding the heap must not cost the reflection flags with it.
    assert argv.count("--add-opens") == len(ADD_OPENS_SPECS)


def test_empty_jvm_opts_falls_back_rather_than_launching_without_a_heap():
    assert "-Xmx1024M" in network.java_argv("")


# --- UNIFI_JVM_OPTS resolution ----------------------------------------


def test_jvm_opts_uses_the_default_when_nothing_sets_it(tmp_path):
    assert network.jvm_opts({}, str(tmp_path / "system_env")) == network.DEFAULT_JVM_OPTS


def test_jvm_opts_reads_the_environment(tmp_path):
    resolved = network.jvm_opts({"UNIFI_JVM_OPTS": "-Xmx64M"}, str(tmp_path / "system_env"))
    assert resolved == "-Xmx64M"


def test_system_env_beats_the_environment(tmp_path):
    # The vendor helper turns `unifi.xmx` in system.properties into this
    # file, and unifi.init sources it after the environment. Verified in
    # the image: unifi.xmx=512 produces exactly this line.
    path = tmp_path / "system_env"
    path.write_text('UNIFI_JVM_OPTS="-Xmx512M -XX:+UseParallelGC"\n')
    resolved = network.jvm_opts({"UNIFI_JVM_OPTS": "-Xmx64M"}, str(path))
    assert resolved == "-Xmx512M -XX:+UseParallelGC"


def test_an_empty_system_env_does_not_blank_the_options(tmp_path):
    path = tmp_path / "system_env"
    path.write_text("")
    assert network.jvm_opts({}, str(path)) == network.DEFAULT_JVM_OPTS


# --- system.properties -------------------------------------------------


def test_system_properties_are_created_with_the_documented_ports(tmp_path):
    path = tmp_path / "data" / "system.properties"
    network.write_system_properties(str(path))
    assert sysprops.parse(path.read_text()) == network.SYSPROPS_DEFAULTS


def test_existing_values_are_never_clobbered(tmp_path):
    path = tmp_path / "system.properties"
    path.write_text("unifi.https.port=9443\n")
    network.write_system_properties(str(path))
    parsed = sysprops.parse(path.read_text())
    assert parsed["unifi.https.port"] == "9443"
    assert parsed["unifi.http.port"] == "8080"


def test_unrelated_content_and_comments_survive(tmp_path):
    path = tmp_path / "system.properties"
    # A line the shell this replaces would have mangled: its sed built a
    # replacement expression out of both sides. Untouched keys keep their
    # source text byte for byte, so no rewrite of ours can corrupt one.
    path.write_text("# wizard wrote this\nsome.key=a/b&c\\d=e%f\n")
    network.write_system_properties(str(path))
    text = path.read_text()
    assert "# wizard wrote this" in text
    assert "some.key=a/b&c\\d=e%f" in text


def test_writing_is_skipped_when_nothing_would_change(tmp_path):
    path = tmp_path / "system.properties"
    network.write_system_properties(str(path))
    before = path.stat().st_mtime_ns
    network.write_system_properties(str(path))
    assert path.stat().st_mtime_ns == before


# --- init hooks --------------------------------------------------------


def _hook(directory, name, body="#!/bin/sh\nexit 0\n", mode=0o755):
    path = directory / name
    path.write_text(body)
    path.chmod(mode)
    return path


def test_hooks_run_in_name_order(tmp_path):
    _hook(tmp_path, "20-second")
    _hook(tmp_path, "10-first")
    assert network.run_hooks(str(tmp_path)) == ["10-first", "20-second"]


def test_non_executable_and_odd_names_are_skipped(tmp_path):
    _hook(tmp_path, "demo-mode")
    _hook(tmp_path, "not-executable", mode=0o644)
    # run-parts' own rule, so a package or editor leftover is not executed.
    _hook(tmp_path, "demo-mode.dpkg-dist")
    _hook(tmp_path, "demo-mode~")
    assert network.run_hooks(str(tmp_path)) == ["demo-mode"]


def test_a_missing_hook_directory_is_not_an_error():
    assert network.run_hooks("/nonexistent/init.d") == []


def test_a_failing_hook_stops_the_boot(tmp_path):
    # demo-mode is what makes the sim image a sim image. Starting the controller
    # without it produces an image whose credentials never work and whose
    # healthcheck never goes green — a far worse outcome than refusing to boot.
    _hook(tmp_path, "10-ok")
    _hook(tmp_path, "20-broken", body="#!/bin/sh\nexit 3\n")
    with pytest.raises(RuntimeError, match="20-broken failed"):
        network.run_hooks(str(tmp_path))


# --- exit status -------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (0, 0),
        (1, 1),
        (-int(signal.SIGKILL), 137),
        (-int(signal.SIGTERM), 143),
        (None, 143),
    ],
)
def test_signalled_children_report_the_shell_convention(code, expected):
    # os.waitstatus_to_exitcode reports SIGKILL as -9, and sys.exit(-9)
    # makes docker record 247 rather than 137.
    assert network.exit_status(code) == expected


def test_vendor_paths_point_the_helper_at_the_volume():
    env = {}
    network.apply_vendor_paths(env)
    assert env == {
        "UNIFI_DATA_DIR": "/unifi/data",
        "UNIFI_LOG_DIR": "/unifi/log",
        "UNIFI_RUN_DIR": "/unifi/run",
    }


def test_clearing_readiness_state_takes_the_session_with_the_marker(tmp_path):
    """A restart must re-prove readiness from scratch, session included.

    /tmp is the writable layer on this image, not a tmpfs, so `docker restart`
    keeps whatever the last boot left. A surviving login marker would skip
    authentication and leave the later stages probing with a cookie the
    restarted controller has never issued.
    """
    state = [tmp_path / name for name in ("unifi-ready", "unifi-login-ready", "unifi-cookies")]
    for path in state:
        path.write_text("stale")

    network.clear_readiness_state([str(p) for p in state])

    assert not [p for p in state if p.exists()]


def test_clearing_readiness_state_is_fine_when_nothing_is_there(tmp_path):
    # The first boot of a fresh container, and every boot on UOS's tmpfs.
    network.clear_readiness_state([str(tmp_path / "absent")])


def test_the_cleared_set_is_everything_the_gate_writes():
    """Named from the healthcheck rather than restated here: a marker added
    there and forgotten here would survive the boot that proved it."""
    from unifi_runtime import healthcheck

    assert healthcheck.MARKER in healthcheck.BOOT_STATE
    assert healthcheck.LOGIN_MARKER in healthcheck.BOOT_STATE
    assert healthcheck.COOKIE_JAR in healthcheck.BOOT_STATE
