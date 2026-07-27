"""Fixtures for the container integration tests.

Booting a UniFi OS image is minutes of systemd, so the containers are
module-scoped: one boot serves a file's worth of assertions.
"""

import os

import pytest

from unifi_containers import testing


@pytest.fixture(scope="session", autouse=True)
def _docker_daemon():
    """Skip the directory when there is no daemon — but never in CI.

    A skip exits pytest 0, so in CI an unreachable daemon would turn the gate
    that proves an image boots into a green step that proved nothing, and the
    release would carry on. On a laptop with no daemon, skipping is the useful
    behaviour.
    """
    if testing.daemon_reachable():
        return
    if os.environ.get("CI"):
        raise RuntimeError(
            "no docker daemon reachable, and CI is set: this gate exists to boot "
            "real images, so skipping it would report success without doing so"
        )
    pytest.skip("no docker daemon reachable")


@pytest.fixture(scope="module")
def uos_seeded():
    """The seeded UOS variant: headless owner setup plus a minted X-API-KEY."""
    container = testing.uos_container(testing.UOS_SEEDED_IMAGE)
    with testing.booted(container, testing.UOS_STARTUP_TIMEOUT) as running:
        yield running


@pytest.fixture(
    scope="module",
    params=[testing.UOS_IMAGE, testing.UOS_SIM_IMAGE],
    ids=["base", "sim"],
)
def uos_without_seed(request):
    """Each UOS variant that leaves the seed flags unset."""
    container = testing.uos_container(request.param)
    with testing.booted(container, testing.UOS_STARTUP_TIMEOUT) as running:
        yield running


@pytest.fixture(scope="module")
def network_seeded():
    """The seeded variant: the first-run wizard already completed at build time."""
    container = testing.network_container(testing.NETWORK_SEEDED_IMAGE, testing.NETWORK_HTTPS_PORT)
    with testing.booted(container, testing.NETWORK_STARTUP_TIMEOUT) as running:
        yield running


@pytest.fixture(scope="module")
def network_base():
    """The base image: no demo fleet, no wizard — just the controller answering."""
    container = testing.network_container(testing.NETWORK_IMAGE, testing.NETWORK_HTTPS_PORT)
    with testing.booted(container, testing.NETWORK_STARTUP_TIMEOUT) as running:
        yield running


@pytest.fixture(scope="module")
def network_sim():
    """The standalone Network App in demo mode, with its HTTPS port published."""
    container = testing.network_container(testing.NETWORK_SIM_IMAGE, testing.NETWORK_HTTPS_PORT)
    with testing.booted(container, testing.NETWORK_STARTUP_TIMEOUT) as running:
        yield running
