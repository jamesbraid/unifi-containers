"""The UniFi OS runtime contract, as the daemon received it.

Written down is not the same as delivered: these keywords travel through
testcontainers to docker-py to the daemon, and a term that silently fails to
arrive produces a container that boots and then misbehaves somewhere unrelated.
The entrypoint is replaced with `sleep`, so this costs seconds not a systemd boot.
"""

import pytest

from unifi_containers import testing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.fixture(scope="module")
def host_config():
    container = testing.uos_container(testing.UOS_SIM_IMAGE, entrypoint=["/bin/sleep", "60"])
    with container as running:
        yield running.get_wrapped_container().attrs["HostConfig"]


def test_every_capability_is_added_and_everything_else_dropped(host_config):
    assert host_config["CapAdd"] == list(testing.UOS_CAPS)
    assert host_config["CapDrop"] == ["ALL"]


def test_the_container_is_not_privileged(host_config):
    # A capability list is only a contract while --privileged is off; with it on,
    # dropping and adding capabilities means nothing.
    assert host_config["Privileged"] is False


def test_systemd_gets_the_host_cgroup_namespace_and_a_writable_cgroupfs(host_config):
    assert host_config["CgroupnsMode"] == "host"
    assert host_config["Binds"] == [f"{testing.CGROUPFS}:{testing.CGROUPFS}:rw"]


def test_the_tmpfs_set_arrives_with_its_mount_options(host_config):
    assert host_config["Tmpfs"] == dict(testing.UOS_TMPFS)
