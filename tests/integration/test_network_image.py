"""The standalone Network Application image, against a real container."""

import httpx
import pytest

from unifi_containers import testing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


def test_the_base_image_becomes_healthy_and_serves_https(network_base):
    # The variants are what CI used to boot, so the `network` probe — the only
    # HEALTHCHECK the base image ships, and the one most consumers rely on — was
    # never executed against a running container. Reaching this line means the
    # image's own healthcheck went green.
    port = network_base.get_exposed_port(testing.NETWORK_HTTPS_PORT)
    response = httpx.get(
        f"https://{network_base.get_container_host_ip()}:{port}/",
        verify=False,
        timeout=15,
        follow_redirects=False,
    )
    assert 200 <= response.status_code < 400


def test_the_sim_variant_answers_a_real_json_login(network_sim):
    # Early in boot the controller answers 200 with an HTML placeholder on every
    # path, so only a real JSON `rc: ok` means the API is up.
    port = network_sim.get_exposed_port(testing.NETWORK_HTTPS_PORT)
    response = httpx.post(
        f"https://{network_sim.get_container_host_ip()}:{port}/api/login",
        json={"username": "admin", "password": "admin"},
        verify=False,
        timeout=15,
    )

    assert response.status_code == 200
    assert response.json()["meta"]["rc"] == "ok"


def test_the_seeded_variant_logs_in_with_the_baked_credentials(network_seeded):
    # The seeded image's whole claim is that the wizard already ran, so the only
    # proof is the baked admin authenticating with no setup step.
    port = network_seeded.get_exposed_port(testing.NETWORK_HTTPS_PORT)
    response = httpx.post(
        f"https://{network_seeded.get_container_host_ip()}:{port}/api/login",
        json={"username": "admin", "password": testing.NETWORK_SEEDED_PASSWORD},
        verify=False,
        timeout=15,
    )

    assert response.status_code == 200
    assert response.json()["meta"]["rc"] == "ok"


def test_stopping_the_container_shuts_mongodb_down_cleanly():
    # Its own container: the assertion is about a stopped one. The exit status
    # cannot tell a clean stop from a hard kill — the old shutdown path exited 0
    # in 1.5s while killing the database — so the evidence is mongod's own log,
    # read back out of the container after it has stopped.
    container = testing.network_container(testing.NETWORK_SIM_IMAGE)
    with testing.booted(container, testing.NETWORK_STARTUP_TIMEOUT) as running:
        status = testing.stop(running)
        log = testing.archived_file(running, testing.MONGOD_LOG)

    assert testing.MONGOD_SHUTDOWN_MARKER in log, (
        f"mongod was hard-killed, not shut down (container exited {status}). "
        f"Tail of {testing.MONGOD_LOG}:\n{log[-2000:]}"
    )


def test_the_sim_variant_has_settled_v2_and_a_full_fleet_when_healthy(network_sim):
    """The gate's whole claim: nothing is still settling once it goes green.

    Both of these lag login readiness — v2 5xxs while zone-based-firewall
    defaults materialize, and the demo fleet populates incrementally — and
    consumers were hand-rolling a poll for each. Reaching this line without one
    is the evidence that they no longer have to.
    """
    base = f"https://{network_sim.get_container_host_ip()}:{network_sim.get_exposed_port(testing.NETWORK_HTTPS_PORT)}"
    session = httpx.Client(verify=False, timeout=15)
    login = session.post(base + "/api/login", json={"username": "admin", "password": "admin"})
    assert login.json()["meta"]["rc"] == "ok"

    assert session.get(base + "/v2/api/site/default/firewall-policies").status_code < 500

    devices = session.get(base + "/api/s/default/stat/device").json()["data"]
    assert len(devices) == 9, f"demo fleet incomplete at healthy: {len(devices)} devices"
