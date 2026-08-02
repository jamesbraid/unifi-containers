"""The healthcheck marker gate and probe ordering.

The ordering assertions are the point. `expect_ordered_request` makes them on
the server side: the probes have to arrive in the registered order or the
server fails the request and `check()` raises. A probe that "works" but spends
a rate-limited login before a free file test is the regression this file
exists to catch.
"""

import pytest
from pytest_httpserver import RequestMatcher

from unifi_runtime import env, healthcheck
from unifi_runtime.seed import uos_owner
from unifi_runtime.unifi.ucore import CLASSIC_PROXY

KEY_PROBE = CLASSIC_PROXY.format(site="default")
LOGIN = RequestMatcher("/api/auth/login", method="POST")


# --- the marker gate --------------------------------------------------


def test_gate_runs_the_probe_when_there_is_no_marker(tmp_path):
    marker = tmp_path / "unifi-ready"
    calls = []

    assert healthcheck.marker_gate(lambda: calls.append(1) or True, marker=str(marker))
    assert calls == [1]
    assert marker.exists()


def test_gate_never_probes_again_once_ready(tmp_path):
    # UniFi rate-limits login globally: a 10-second login probe starves
    # itself and every real client, and the health column reads 0 0 0 0 1.
    marker = tmp_path / "unifi-ready"
    marker.write_text("")
    calls = []

    assert healthcheck.marker_gate(lambda: calls.append(1) or True, marker=str(marker))
    assert calls == []


def test_a_failing_probe_leaves_no_marker(tmp_path):
    marker = tmp_path / "unifi-ready"
    assert not healthcheck.marker_gate(lambda: False, marker=str(marker))
    assert not marker.exists()


def test_an_unwritable_marker_still_reports_ready(tmp_path):
    # Costs a repeated probe, not a failed healthcheck.
    marker = tmp_path / "no" / "such" / "dir" / "unifi-ready"
    assert healthcheck.marker_gate(lambda: True, marker=str(marker))


# --- base image -------------------------------------------------------


def test_uos_is_unhealthy_when_nothing_answers(dead_url):
    assert not healthcheck.uos_ready(dead_url)


# --- sim variant ------------------------------------------------------


def test_sim_sends_the_credentials_as_json(httpserver):
    httpserver.expect_request(
        "/api/login",
        method="POST",
        json={"username": "u", "password": "p"},
        headers={"Content-Type": "application/json"},
    ).respond_with_json({"meta": {"rc": "ok"}})
    assert healthcheck.sim_ready(httpserver.url_for(""), username="u", password="p")
    httpserver.check()


# --- seeded variant ---------------------------------------------------


def test_seeded_checks_cheapest_first_and_login_last(httpserver, tmp_path):
    keyfile = tmp_path / "api-key"
    keyfile.write_text("good-key\n")
    # Ordered: the free key probe, carrying the key, then the rate-limited
    # login. Either one out of order fails the server permanently.
    httpserver.expect_ordered_request(
        KEY_PROBE, method="GET", headers={"X-API-KEY": "good-key"}
    ).respond_with_json({"data": []})
    httpserver.expect_ordered_request("/api/auth/login", method="POST").respond_with_data("")

    assert healthcheck.seeded_ready(httpserver.url_for(""), key_file=str(keyfile))
    httpserver.check()


def test_seeded_spends_no_login_when_the_key_file_is_empty(httpserver, tmp_path):
    # The seed has not finished. Failing here is free; failing after a
    # login is not. The login handler is registered to prove the probe
    # declines to use it, not that it would have failed.
    keyfile = tmp_path / "api-key"
    keyfile.write_text("")
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_data("")

    assert not healthcheck.seeded_ready(httpserver.url_for(""), key_file=str(keyfile))
    assert httpserver.log == []


def test_seeded_spends_no_login_when_the_key_file_is_missing(httpserver, tmp_path):
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_data("")
    assert not healthcheck.seeded_ready(httpserver.url_for(""), key_file=str(tmp_path / "absent"))
    assert httpserver.log == []


def test_seeded_spends_no_login_when_the_key_is_rejected(httpserver, tmp_path):
    keyfile = tmp_path / "api-key"
    keyfile.write_text("stale\n")
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_data(status=401)
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_data("")

    assert not healthcheck.seeded_ready(httpserver.url_for(""), key_file=str(keyfile))
    httpserver.assert_request_made(LOGIN, count=0)
    httpserver.check()


def test_seeded_without_a_key_file_is_the_login_alone(httpserver):
    # An unset UOS_API_KEY_FILE means the key seed is off, so the login is
    # the whole gate rather than a skipped check.
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_data("")
    assert healthcheck.seeded_ready(httpserver.url_for(""), key_file=None)
    httpserver.assert_request_made(LOGIN, count=1)
    httpserver.check()


def test_seeded_is_unhealthy_when_the_login_is_rejected(httpserver, tmp_path):
    keyfile = tmp_path / "api-key"
    keyfile.write_text("good-key\n")
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_json({"data": []})
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_data(status=401)

    assert not healthcheck.seeded_ready(httpserver.url_for(""), key_file=str(keyfile))
    httpserver.check()


# --- the CLI ----------------------------------------------------------


def test_the_registry_is_exactly_the_shipped_variants():
    # Every image's HEALTHCHECK names one of these, so a rename here is a
    # broken image that still builds; an orphan is a probe nothing runs.
    assert set(healthcheck.PROBES) == {
        "uos",
        "sim",
        "seeded",
        "network",
        "network-sim",
        "network-seeded",
    }


def test_an_unknown_probe_is_a_usage_error(capsys):
    assert healthcheck.main(["nonesuch"]) == 2
    assert healthcheck.main([]) == 2


def test_the_exit_code_is_the_verdict(monkeypatch):
    monkeypatch.setitem(healthcheck.PROBES, "uos", lambda: True)
    assert healthcheck.main(["uos"]) == 0
    monkeypatch.setitem(healthcheck.PROBES, "uos", lambda: False)
    assert healthcheck.main(["uos"]) == 1


def test_the_key_probe_follows_the_seed_flag_not_the_path(monkeypatch):
    """UOS_SEED_API_KEY decides whether a key is expected; the path only says where.

    Keying it off UOS_API_KEY_FILE meant a variant Dockerfile that dropped an
    apparently-redundant ENV silently lost this probe, leaving the login check as
    the whole gate.
    """
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return True

    monkeypatch.setattr(healthcheck, "seeded_ready", spy)
    monkeypatch.setattr(healthcheck.os.path, "exists", lambda _p: False)
    monkeypatch.setattr(healthcheck, "MARKER", "/nonexistent-marker")
    # Only the login stage is under test here. Collapsing `staged` to it keeps
    # the later stages from reaching for a controller that is not running.
    monkeypatch.setattr(healthcheck, "staged", lambda login_probe, *_a, **_kw: login_probe())

    monkeypatch.delenv("UOS_API_KEY_FILE", raising=False)
    monkeypatch.setenv("UOS_SEED_API_KEY", "true")
    healthcheck.PROBES["seeded"]()
    assert seen["key_file"] == env.KEY_FILE

    monkeypatch.setenv("UOS_SEED_API_KEY", "false")
    healthcheck.PROBES["seeded"]()
    assert seen["key_file"] is None


@pytest.mark.parametrize("value", ["true", "1", "TRUE", "True", "yes", "on", "", "0", "false"])
def test_the_seed_step_and_the_probe_read_the_flag_the_same_way(value, monkeypatch):
    """Whoever mints the key and whoever probes for it must agree on every spelling.

    They did not. The probe lowercased and the seed step did not, so
    `UOS_SEED_API_KEY=True` left the probe waiting for a key the seed step had
    decided not to mint — unhealthy for the life of the container.
    """
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return True

    monkeypatch.setattr(healthcheck, "seeded_ready", spy)
    # marker_gate's default marker is bound at def time, so patching MARKER does
    # not reach it. Bypass the gate rather than depend on /tmp.
    monkeypatch.setattr(healthcheck, "marker_gate", lambda probe, *_a: probe())
    # Only the login stage is under test here. Collapsing `staged` to it keeps
    # the later stages from reaching for a controller that is not running.
    monkeypatch.setattr(healthcheck, "staged", lambda login_probe, *_a, **_kw: login_probe())
    monkeypatch.setenv("UOS_SEED_API_KEY", value)

    healthcheck.PROBES["seeded"]()
    probe_expects_a_key = seen["key_file"] is not None
    seed_mints_a_key = uos_owner.config_from_env({"UOS_SEED_API_KEY": value}).seed_api_key

    assert probe_expects_a_key == seed_mints_a_key
