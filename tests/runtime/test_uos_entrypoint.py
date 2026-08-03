"""The decisions the UOS entrypoint makes, without the syscalls it makes.

`main()` is deliberately untested: it calls the functions below and then
execs /sbin/init, and a harness built to observe an exec tests the harness.
"""

import os
import shlex
import shutil

import pytest
from conftest import REPO_ROOT

from unifi_runtime.entrypoint import uos

# --- symlink plan -----------------------------------------------------


def test_plan_covers_every_mapping_with_stable_order(tmp_path):
    plans = uos.plan_symlinks(root=tmp_path)
    assert [p.original for p in plans] == [str(tmp_path) + original for original in uos.SYMLINK_MAP]
    assert [p.target for p in plans] == [
        str(tmp_path) + target for target in uos.SYMLINK_MAP.values()
    ]


def test_only_real_directories_are_migrated(tmp_path):
    (tmp_path / "data").mkdir()  # real content
    (tmp_path / "srv").symlink_to(tmp_path / "unifi" / "srv")  # already done
    # /var/lib/mongodb simply absent

    by_original = {p.original: p for p in uos.plan_symlinks(root=tmp_path)}
    assert by_original[str(tmp_path) + "/data"].migrate is True
    assert by_original[str(tmp_path) + "/srv"].migrate is False
    assert by_original[str(tmp_path) + "/var/lib/mongodb"].migrate is False


def test_apply_migrates_then_relinks(tmp_path):
    original = tmp_path / "data"
    original.mkdir()
    (original / "keep.txt").write_text("from the image\n")
    (original / "nested").mkdir()
    (original / "nested" / "deep.txt").write_text("deep\n")
    target = tmp_path / "unifi" / "data"

    uos.apply_symlinks([uos.Relink(str(original), str(target), True)])

    assert original.is_symlink()
    assert os.readlink(str(original)) == str(target)
    assert (target / "keep.txt").read_text() == "from the image\n"
    assert (target / "nested" / "deep.txt").read_text() == "deep\n"
    assert oct(target.stat().st_mode)[-3:] == "755"


def test_apply_does_not_clobber_volume_content(tmp_path):
    # The volume's copy is the one that has been running; the image's
    # pristine copy must not win on restart.
    original = tmp_path / "data"
    original.mkdir()
    (original / "state").write_text("pristine\n")
    target = tmp_path / "unifi" / "data"
    target.mkdir(parents=True)
    (target / "state").write_text("live\n")

    uos.apply_symlinks([uos.Relink(str(original), str(target), True)])

    assert (target / "state").read_text() == "live\n"


def test_apply_is_idempotent(tmp_path):
    original = tmp_path / "data"
    original.mkdir()
    (original / "state").write_text("live\n")
    target = tmp_path / "unifi" / "data"

    for _ in range(3):
        uos.apply_symlinks(uos.plan_symlinks(root=tmp_path, links={"/data": "/unifi/data"}))

    assert original.is_symlink()
    assert (target / "state").read_text() == "live\n"


# --- stamps -----------------------------------------------------------


@pytest.mark.parametrize(
    "arch,expected",
    [
        ("amd64", "linux-x64"),
        ("x86_64", "linux-x64"),
        ("arm64", "arm64"),
        ("aarch64", "arm64"),
    ],
)
def test_firmware_platform(arch, expected):
    assert uos.firmware_platform(arch) == expected


def test_unsupported_arch_is_a_loud_error():
    with pytest.raises(ValueError) as excinfo:
        uos.firmware_platform("riscv64")
    assert "riscv64" in str(excinfo.value)


def test_version_stamp_shape():
    assert (
        uos.version_stamp("UOSSERVER", "5.1.21") == "UOSSERVER.0000000.5.1.21.0000000.000000.0000"
    )


# --- first-boot UUID --------------------------------------------------


def test_uuid_version_nibble_is_forced_to_five():
    # /proc/sys/kernel/random/uuid emits a v4; the console id wants a v5.
    assert (
        uos.force_uuid_version_5("f81d4fae-7dec-41d0-a765-00a0c91e6bf6")
        == "f81d4fae-7dec-51d0-a765-00a0c91e6bf6"
    )


def test_uuid_is_generated_once_and_then_reused(tmp_path):
    source = tmp_path / "random"
    source.write_text("f81d4fae-7dec-41d0-a765-00a0c91e6bf6\n")
    path = tmp_path / "unifi" / "data" / "uos_uuid"

    first = uos.ensure_uuid({}, path=str(path), source=str(source))
    assert first == "f81d4fae-7dec-51d0-a765-00a0c91e6bf6"

    source.write_text("00000000-0000-4000-8000-000000000000\n")
    assert uos.ensure_uuid({}, path=str(path), source=str(source)) == first


def test_explicit_uuid_is_taken_verbatim(tmp_path):
    path = tmp_path / "uos_uuid"
    value = uos.ensure_uuid(
        {"UOS_UUID": "supplied-by-the-operator"}, path=str(path), source="/nonexistent"
    )
    assert value == "supplied-by-the-operator"
    assert path.read_text() == "supplied-by-the-operator\n"


# --- optional systemd units -------------------------------------------

#: The unit files are static and COPY'd into the image, so the repo copies are
#: the artefacts under test. A Dockerfile typo would move them without a
#: failure here, which is what the reachability assertion below is for.
UNIT_DIR = REPO_ROOT / "unifi-os" / "units"


def unit_text(name):
    return (UNIT_DIR / name).read_text()


def test_every_shipped_unit_is_accounted_for():
    # A unit added to unifi-os/units/ that nothing enables is dead weight; one
    # named in UNIT_FLAGS but missing from the image never starts.
    shipped = {path.name for path in UNIT_DIR.iterdir()}
    assert shipped == {
        "uos-network-direct.socket",
        "uos-network-direct.service",
        "uos-seed-owner.service",
        "uos-readyz.service",
    }
    for _, names in uos.UNIT_FLAGS:
        assert set(names) <= shipped


@pytest.fixture
def systemctl(monkeypatch):
    """Record what would have been run. There is no systemctl on a laptop."""
    calls = []
    monkeypatch.setattr(uos.subprocess, "call", lambda argv, **kw: calls.append(argv) or 0)
    return calls


def test_base_image_enables_nothing(systemctl):
    # The base image must stay inert; the variants and the env flags opt in.
    assert uos.enable_units({}) == []
    assert all(argv[1] == "disable" for argv in systemctl), systemctl


@pytest.mark.parametrize("value", ["true", "1"])
def test_network_direct_flag_spellings(value, systemctl):
    assert uos.enable_units({"UOS_NETWORK_DIRECT": value}) == ["uos-network-direct.socket"]
    assert ["systemctl", "enable", "uos-network-direct.socket"] in systemctl


@pytest.mark.parametrize("value", ["yes", "TRUE", "on", "0", "", "false"])
def test_other_spellings_stay_off(value, systemctl):
    env = {"UOS_NETWORK_DIRECT": value, "UOS_SEED_OWNER": value}
    assert uos.enable_units(env) == []
    assert all(argv[1] == "disable" for argv in systemctl), systemctl


def test_a_flag_turned_off_disables_a_unit_the_image_enabled_at_build_time(systemctl):
    # The sim and seeded Dockerfiles `systemctl enable` their unit, so "off" has
    # to actively disable it. Doing nothing would leave the unit running with the
    # flag saying it should not be.
    uos.enable_units({"UOS_SEED_OWNER": "false"})
    assert ["systemctl", "disable", "uos-seed-owner.service"] in systemctl


def test_both_flags_together_enable_both(systemctl):
    env = {"UOS_NETWORK_DIRECT": "true", "UOS_SEED_OWNER": "true"}
    assert uos.enable_units(env) == ["uos-network-direct.socket", "uos-seed-owner.service"]


def test_the_direct_port_needs_only_the_socket_enabled():
    # Socket activation starts the same-named service, so the service carries no
    # [Install] and enabling it would be wrong rather than merely redundant.
    assert dict(uos.UNIT_FLAGS)["UOS_NETWORK_DIRECT"] == ("uos-network-direct.socket",)
    assert "[Install]" in unit_text("uos-network-direct.socket").splitlines()
    assert "[Install]" not in unit_text("uos-network-direct.service").splitlines()


def test_network_direct_units_expose_7443_and_proxy_to_8081():
    assert "ListenStream=7443" in unit_text("uos-network-direct.socket")
    assert "WantedBy=sockets.target" in unit_text("uos-network-direct.socket")
    assert "ExecStart=/lib/systemd/systemd-socket-proxyd 127.0.0.1:8081" in unit_text(
        "uos-network-direct.service"
    )


def test_seed_owner_unit_carries_pythonpath():
    # systemd units inherit nothing from the container env, so a `python3 -m`
    # ExecStart without this line cannot find the package at all.
    text = unit_text("uos-seed-owner.service")
    assert "Environment=PYTHONPATH=/usr/local/lib/unifi-containers" in text
    assert "ExecStart=/usr/bin/python3 -m unifi_runtime.seed.uos_owner" in text
    assert f"EnvironmentFile=-{uos.SEED_OWNER_ENV_PATH}" in text
    assert "WantedBy=multi-user.target" in text


def test_the_unit_pythonpath_matches_the_image():
    # The unit hardcodes the path the base Dockerfile sets. If one moves and the
    # other does not, the seed fails with an ImportError at first boot.
    dockerfile = (REPO_ROOT / "unifi-os" / "Dockerfile").read_text()
    assert "PYTHONPATH=/usr/local/lib/unifi-containers" in dockerfile
    assert "COPY unifi-os/units/ /etc/systemd/system/" in dockerfile


def test_seed_owner_env_defaults(tmp_path):
    path = tmp_path / "uos-seed-owner.env"
    uos.write_seed_owner_env({"UOS_SEED_OWNER": "1"}, path=str(path))
    assert path.read_text() == (
        "UOS_ADMIN_USER='admin'\n"
        "UOS_ADMIN_PASS='admin'\n"
        "UOS_COUNTRY='840'\n"
        "UOS_TIMEZONE='UTC'\n"
        "UOS_CONSOLE_NAME='unifi-os-sim'\n"
        "UOS_SEED_API_KEY='0'\n"
        "UOS_API_KEY_NAME='unifi-containers-seeded'\n"
        "UOS_API_KEY_FILE='/unifi/api-key'\n"
    )


def test_seed_owner_env_takes_the_seeded_variant_settings():
    text = uos.seed_owner_env(
        {
            "UOS_ADMIN_USER": "operator",
            "UOS_ADMIN_PASS": "hunter2",
            "UOS_SEED_API_KEY": "true",
            "UOS_API_KEY_FILE": "/unifi/api-key",
        }
    )
    assert "UOS_ADMIN_USER='operator'\n" in text
    assert "UOS_ADMIN_PASS='hunter2'\n" in text
    assert "UOS_SEED_API_KEY='true'\n" in text
    assert "UOS_COUNTRY='840'\n" in text  # untouched default


# --- init hooks -------------------------------------------------------

# run-parts is a Debian binary (busybox has it too, so CI's alpine image is
# fine); a developer macbook has neither. Skipping beats faking the runner
# just so a mock can be asserted against.
needs_run_parts = pytest.mark.skipif(
    shutil.which("run-parts") is None, reason="run-parts not installed"
)


def test_no_hook_dir_is_not_a_failure(tmp_path):
    uos.run_init_hooks(str(tmp_path / "absent"))


@needs_run_parts
def test_a_failing_hook_stops_the_boot(tmp_path):
    # Under `set -e` the shell aborted here too. A sim image whose
    # demo-mode hook did nothing boots into a ten-minute healthcheck
    # timeout with no clue why.
    hook = tmp_path / "broken"
    hook.write_text("#!/bin/sh\nexit 3\n")
    hook.chmod(0o755)

    with pytest.raises(RuntimeError):
        uos.run_init_hooks(str(tmp_path))


@needs_run_parts
def test_a_working_hook_runs(tmp_path):
    hooks = tmp_path / "init.d"
    hooks.mkdir()
    witness = tmp_path / "ran"
    hook = hooks / "seed"
    hook.write_text(f"#!/bin/sh\ntouch {witness}\n")
    hook.chmod(0o755)

    uos.run_init_hooks(str(hooks))

    assert witness.exists()


def test_migration_preserves_ownership(tmp_path, monkeypatch):
    """`cp -a` keeps uid/gid; shutil does not, and services run as their own users.

    A root-owned /var/log/mongodb stops mongod starting, which takes
    unifi-core's /api/system to 500 and the image never goes healthy.
    """
    source = tmp_path / "var-log"
    (source / "mongodb").mkdir(parents=True)
    (source / "mongodb" / "mongodb.log").write_text("")
    target = tmp_path / "unifi-logs"
    target.mkdir()

    chowned = []
    monkeypatch.setattr(uos.os, "chown", lambda p, uid, gid, **kw: chowned.append(str(p)))

    uos._copy_no_clobber(str(source), str(target))

    # Every migrated path, not just the top of the tree: mongod's log directory
    # is one level down and it is the one that has to be writable.
    assert set(chowned) == {
        str(target / "mongodb"),
        str(target / "mongodb" / "mongodb.log"),
    }


@pytest.mark.parametrize(
    "value",
    [
        "admin",
        "My Console",  # a space splits an unquoted value
        "p@ss w'rd",  # a quote has to survive being quoted
        "back\\slash",  # systemd treats an unquoted backslash as an escape
        "$HOME",  # and would otherwise try to expand this
        "trailing ",
    ],
)
def test_a_seed_value_survives_the_environment_file(value):
    """What the unit reads back must equal what the container was given.

    The seed step gets these through the EnvironmentFile while the healthcheck
    reads them straight from the environment, so anything systemd's shell-like
    parser rewrites makes the two disagree — and that shows up as a container
    that never goes healthy, not as an error.

    shlex stands in for systemd's parser here: both implement POSIX-shell
    quoting, and no systemd is available to a unit test.
    """
    text = uos.seed_owner_env({"UOS_CONSOLE_NAME": value})
    line = next(ln for ln in text.splitlines() if ln.startswith("UOS_CONSOLE_NAME="))
    parsed = shlex.split(line)
    assert len(parsed) == 1, f"systemd would split this into {parsed}"
    assert parsed[0] == f"UOS_CONSOLE_NAME={value}"
