"""The first-run wizard, against a real HTTP server.

The bodies matter: `add-default-admin` with a mistyped field name returns 200
and creates nothing, and the seeded image then ships with credentials that do
not work. So `happy()` matches on the exact bodies rather than on the paths
alone — a wrong body matches no handler, is answered 500, and fails `check()`.
"""

import json

import pytest
from pytest_httpserver import RequestMatcher

from unifi_runtime.seed import network_wizard

USER = "admin"
PASSWORD = "unifi-containers-seeded"
OK = {"meta": {"rc": "ok"}}

IDENTITY = RequestMatcher("/api/set/setting/super_identity", method="POST")
SYSTEM = RequestMatcher("/api/cmd/system", method="POST")
LOGIN = RequestMatcher("/api/login", method="POST")


def happy(httpserver, rc="ok", up=True, admin_status=200, identity_status=200):
    """Register a controller that accepts every wizard step, with one failure
    dialled in where a test needs it."""
    httpserver.expect_request("/status", method="GET").respond_with_json({"meta": {"up": up}})
    httpserver.expect_request(
        "/api/cmd/sitemgr",
        method="POST",
        json={
            "cmd": "add-default-admin",
            "name": USER,
            "email": "admin@example.invalid",
            "x_password": PASSWORD,
        },
    ).respond_with_json({"x": 1} if admin_status != 200 else OK, status=admin_status)
    httpserver.expect_request(
        "/api/set/setting/super_identity", method="POST", json={"name": PASSWORD}
    ).respond_with_json(OK, status=identity_status)
    httpserver.expect_request(
        "/api/cmd/system", method="POST", json={"cmd": "set-installed"}
    ).respond_with_json(OK)
    httpserver.expect_request(
        "/api/login", method="POST", json={"username": USER, "password": PASSWORD}
    ).respond_with_json({"meta": {"rc": rc}})


def test_the_wizard_posts_the_bodies_the_controller_expects(httpserver):
    happy(httpserver)
    network_wizard.seed(httpserver.url_for(""), USER, PASSWORD, out=lambda _: None)
    # Every step's body is part of its matcher, so reaching the end of the
    # wizard with no unmatched request is the assertion.
    httpserver.check()


def test_a_login_the_controller_rejects_fails_the_seed(httpserver):
    # HTTP 200 with rc:error is the shape that made the shell version grep for
    # "ok" rather than trust the status code.
    happy(httpserver, rc="error")
    with pytest.raises(network_wizard.WizardError) as caught:
        network_wizard.seed(httpserver.url_for(""), USER, PASSWORD, out=lambda _: None)
    assert "cannot log in" in str(caught.value)
    httpserver.check()


def test_a_refused_admin_creation_stops_before_marking_setup_complete(httpserver):
    happy(httpserver, admin_status=500)
    with pytest.raises(network_wizard.WizardError):
        network_wizard.seed(httpserver.url_for(""), USER, PASSWORD, out=lambda _: None)
    httpserver.assert_request_made(SYSTEM, count=0)
    httpserver.check()


def test_a_failed_rename_is_only_cosmetic(httpserver):
    happy(httpserver, identity_status=400)
    lines = []
    network_wizard.seed(httpserver.url_for(""), USER, PASSWORD, out=lines.append)
    assert any("could not set the installation name" in line for line in lines)
    httpserver.check()


def test_the_wizard_runs_even_if_status_never_says_up(httpserver):
    # /status reports up several seconds before the API can authenticate, so its
    # silence is a hint rather than a verdict.
    happy(httpserver, up=False)
    lines = []
    network_wizard.seed(
        httpserver.url_for(""), USER, PASSWORD, out=lines.append, sleep=lambda _: None
    )
    assert any("never reported up" in line for line in lines)
    httpserver.assert_request_made(LOGIN, count=1)
    httpserver.check()


def test_the_installation_name_is_the_seed_identity(httpserver):
    happy(httpserver)
    network_wizard.seed(httpserver.url_for(""), USER, PASSWORD, out=lambda _: None)
    posted, _ = next(httpserver.iter_matching_requests(IDENTITY))
    assert json.loads(posted.data) == {"name": network_wizard.SEED_IDENTITY}
    httpserver.check()
