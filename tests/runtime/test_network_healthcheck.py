"""The network image's readiness probes.

The marker is the part worth testing. UniFi rate-limits login globally with
a Retry-After of up to an hour, so a login probe that keeps running after it
has succeeded starves itself and every real client with it. "Prove it once,
then stop asking" is a correctness requirement, not an optimisation.
"""

import pytest
from pytest_httpserver import RequestMatcher

from unifi_runtime import healthcheck
from unifi_runtime.unifi.network import NetworkApp

LOGIN = RequestMatcher("/api/login", method="POST")

V2_PATH = "/v2/api/site/default/firewall-policies"
DEVICES_PATH = "/api/s/default/stat/device"


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


def test_the_probe_follows_the_port_system_properties_pins(tmp_path):
    """The controller obeys system.properties, so the probe has to as well.

    write_system_properties supplies 8443 only when the key is absent, so a
    volume carried over from another deployment keeps its own port. The three
    deleted shell healthchecks read it; a probe fixed at 8443 polls a port
    nothing listens on and the container never goes healthy.
    """
    props = tmp_path / "system.properties"
    props.write_text("unifi.https.port=9443\nunifi.http.port=8080\n")
    assert healthcheck.network_url(str(props)) == "https://localhost:9443"


def test_the_probe_falls_back_to_the_documented_default(tmp_path):
    missing = tmp_path / "absent"
    assert healthcheck.network_url(str(missing)).endswith(":8443")
    empty = tmp_path / "system.properties"
    empty.write_text("unifi.http.port=8080\n")
    assert healthcheck.network_url(str(empty)).endswith(":8443")


# --- the surfaces that settle after login -----------------------------
#
# Login readiness is not the last fact about a booting controller. v2 5xxs
# while zone-based-firewall defaults materialize and the demo fleet arrives
# later still, so a consumer that starts work the moment login succeeds races
# both. Gating on them here is only correct if it stays free: the login is the
# rate-limited call, and waiting longer must not buy more of them.


def staged_probe(httpserver, tmp_path, fleet_ready=None):
    """A -sim style staged gate pointed at the test server."""
    app = NetworkApp(httpserver.url_for(""), cookie_jar=str(tmp_path / "cookies"))
    return lambda: healthcheck.staged(
        lambda: app.login_ok("admin", "admin"),
        app.v2_status,
        fleet_ready,
        login_marker=str(tmp_path / "login-ready"),
    )


def test_waiting_for_v2_costs_no_extra_logins(httpserver, tmp_path):
    httpserver.expect_request("/api/login", method="POST").respond_with_json({"meta": {"rc": "ok"}})
    httpserver.expect_request(V2_PATH).respond_with_data(status=500)
    probe = staged_probe(httpserver, tmp_path)

    assert not probe()
    assert not probe()
    assert not probe()
    httpserver.assert_request_made(LOGIN, count=1)
    httpserver.check()


def test_a_dead_session_buys_exactly_one_fresh_login(httpserver, tmp_path):
    """401 is numerically below 500, so it must be read before the range check.

    Left to the range check alone it would pass as ready, and the image would
    report healthy on a session it can no longer use.
    """
    httpserver.expect_request("/api/login", method="POST").respond_with_json({"meta": {"rc": "ok"}})
    httpserver.expect_request(V2_PATH).respond_with_data(status=401)
    probe = staged_probe(httpserver, tmp_path)

    assert not probe()
    assert not probe()
    httpserver.assert_request_made(LOGIN, count=2)
    httpserver.check()


def test_the_gate_opens_once_every_stage_answers(httpserver, tmp_path):
    httpserver.expect_request("/api/login", method="POST").respond_with_json({"meta": {"rc": "ok"}})
    httpserver.expect_request(V2_PATH).respond_with_json({"data": []})
    httpserver.expect_request(DEVICES_PATH).respond_with_json({"data": [{"mac": "a"}]})
    app = NetworkApp(httpserver.url_for(""), cookie_jar=str(tmp_path / "cookies"))

    assert healthcheck.staged(
        lambda: app.login_ok("admin", "admin"),
        app.v2_status,
        lambda: len(app.devices()) >= 1,
        login_marker=str(tmp_path / "login-ready"),
    )
    httpserver.check()


def test_a_half_populated_fleet_is_not_ready(httpserver, tmp_path):
    """The fleet arrives incrementally, so "at least one device" is not enough.

    That is the race a consumer polling stat/device was working around.
    """
    httpserver.expect_request("/api/login", method="POST").respond_with_json({"meta": {"rc": "ok"}})
    httpserver.expect_request(V2_PATH).respond_with_json({"data": []})
    httpserver.expect_request(DEVICES_PATH).respond_with_json({"data": [{"mac": "a"}]})
    app = NetworkApp(httpserver.url_for(""), cookie_jar=str(tmp_path / "cookies"))

    assert not healthcheck.staged(
        lambda: app.login_ok("admin", "admin"),
        app.v2_status,
        lambda: len(app.devices()) >= 9,
        login_marker=str(tmp_path / "login-ready"),
    )
    httpserver.check()


@pytest.mark.parametrize("status", [200, 204, 404])
def test_v2_has_settled_on_anything_that_is_not_a_server_error(status):
    """404 counts: a controller predating zone-based firewall has no v2 endpoint
    at all, and is nonetheless ready. Requiring 200 would wedge it shut."""
    assert healthcheck.v2_settled(status)


@pytest.mark.parametrize("status", [500, 502, 503, 0])
def test_v2_has_not_settled_while_it_errors_or_never_answers(status):
    assert not healthcheck.v2_settled(status)


# --- how many devices to wait for -------------------------------------


def test_the_expected_fleet_is_what_demo_mode_actually_seeds():
    """Read from demo_settings, not restated: a count that disagreed with the
    seed would wedge the gate shut or open it early."""
    assert healthcheck.expected_fleet({}) == 9
    assert healthcheck.expected_fleet({"DEMO_NUM_UAP": "2", "DEMO_NUM_USW": "0"}) == 3


def test_sim_expect_devices_overrides_the_total():
    assert healthcheck.expected_fleet({"SIM_EXPECT_DEVICES": "4"}) == 4


def test_a_garbled_count_opens_the_gate_rather_than_wedging_it():
    assert healthcheck.expected_fleet({"DEMO_NUM_UAP": "three"}) == 6
    assert healthcheck.expected_fleet({"SIM_EXPECT_DEVICES": "lots"}) == 9
