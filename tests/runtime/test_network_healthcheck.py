"""The network image's readiness probes.

The marker is the part worth testing. UniFi rate-limits login globally with
a Retry-After of up to an hour, so a login probe that keeps running after it
has succeeded starves itself and every real client with it. "Prove it once,
then stop asking" is a correctness requirement, not an optimisation.
"""

import pytest
from pytest_httpserver import RequestMatcher

from unifi_runtime import healthcheck

LOGIN = RequestMatcher("/api/login", method="POST")


def test_the_base_probe_accepts_any_answer_below_400(httpserver):
    httpserver.expect_request("/", method="GET").respond_with_data(
        status=302, headers={"Location": "/manage"}
    )
    httpserver.expect_request("/manage", method="GET").respond_with_data("<html>")
    assert healthcheck.network_answering(httpserver.url_for(""))
    httpserver.check()


def test_the_base_probe_fails_on_a_server_error(httpserver):
    httpserver.expect_request("/", method="GET").respond_with_data(status=503)
    assert not healthcheck.network_answering(httpserver.url_for(""))
    httpserver.check()


def test_the_base_probe_fails_when_nothing_is_listening(dead_url):
    assert not healthcheck.network_answering(dead_url, timeout=1)


def test_login_ready_needs_a_real_json_rc_ok(httpserver):
    # The credentials are matched, not read back: a probe that logs in as
    # somebody else is not evidence about this image's admin.
    httpserver.expect_request(
        "/api/login", method="POST", json={"username": "admin", "password": "admin"}
    ).respond_with_json({"meta": {"rc": "ok"}})
    assert healthcheck.network_login_ready("admin", "admin", httpserver.url_for(""))
    httpserver.check()


@pytest.fixture
def login_probe(httpserver):
    return lambda: healthcheck.network_login_ready("admin", "admin", httpserver.url_for(""))


def test_the_marker_stops_the_login_after_the_first_success(httpserver, tmp_path, login_probe):
    marker = tmp_path / "unifi-ready"
    httpserver.expect_request("/api/login", method="POST").respond_with_json({"meta": {"rc": "ok"}})

    assert healthcheck.marker_gate(login_probe, str(marker))
    assert marker.exists()
    assert healthcheck.marker_gate(login_probe, str(marker))
    httpserver.assert_request_made(LOGIN, count=1)
    httpserver.check()


def test_a_failed_probe_keeps_probing(httpserver, tmp_path, login_probe):
    marker = tmp_path / "unifi-ready"
    httpserver.expect_request("/api/login", method="POST").respond_with_data(status=401)

    assert not healthcheck.marker_gate(login_probe, str(marker))
    assert not marker.exists()
    assert not healthcheck.marker_gate(login_probe, str(marker))
    httpserver.assert_request_made(LOGIN, count=2)
    httpserver.check()
