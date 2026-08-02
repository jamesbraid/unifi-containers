"""The HTTP layer, against a real server on loopback."""

from unifi_runtime import http


def test_returns_the_status_and_parsed_body(httpserver):
    httpserver.expect_request("/api/system", method="GET").respond_with_json({"isSetup": True})
    response = http.json_request(httpserver.url_for("/api/system"))
    assert response.status == 200
    assert response.ok
    assert response.json() == {"isSetup": True}
    httpserver.check()


def test_posts_json_and_sets_the_content_type(httpserver):
    # The matcher is the assertion: a body or a Content-Type the controller
    # would not accept matches nothing, answers 500, and fails check().
    httpserver.expect_request(
        "/api/login",
        method="POST",
        json={"username": "admin", "password": "admin"},
        headers={"Content-Type": "application/json"},
    ).respond_with_json({"meta": {"rc": "ok"}})

    http.json_request(
        httpserver.url_for("/api/login"),
        method="POST",
        payload={"username": "admin", "password": "admin"},
    )
    httpserver.check()


def test_an_error_status_is_a_response_not_an_exception(httpserver):
    # urllib raises HTTPError for 4xx/5xx, which would make "rejected" and
    # "unreachable" look identical at the call site.
    httpserver.expect_request("/gone", method="GET").respond_with_json(
        {"error": "nope"}, status=401
    )
    response = http.json_request(httpserver.url_for("/gone"))
    assert response.status == 401
    assert not response.ok
    assert response.json() == {"error": "nope"}


def test_a_500_is_readable(httpserver):
    httpserver.expect_request("/api/setup", method="POST").respond_with_json(
        {"message": "Device is already setup"}, status=500
    )
    response = http.json_request(httpserver.url_for("/api/setup"), method="POST", payload={})
    assert response.status == 500
    assert "already setup" in response.text


def test_a_request_that_never_lands_is_status_zero(dead_url):
    response = http.json_request(dead_url + "/anything", timeout=2)
    assert response.status == http.NO_VERDICT
    assert not response.answered
    assert not response.ok


def test_unparseable_body_yields_an_empty_dict(httpserver):
    httpserver.expect_request("/html", method="GET").respond_with_data("<html>still booting</html>")
    assert http.json_request(httpserver.url_for("/html")).json() == {}


# --- the persisted session --------------------------------------------
#
# Docker runs every healthcheck as a fresh process. A session held in memory
# would be gone by the next tick, so the jar on disk is the only thing that
# lets one login serve the probes that follow it.


def _echo_cookie(request):
    from werkzeug.wrappers import Response as WerkzeugResponse

    return WerkzeugResponse(request.headers.get("Cookie", ""), status=200)


def test_a_session_saved_by_one_call_authenticates_the_next(httpserver, tmp_path):
    jar = str(tmp_path / "cookies")
    httpserver.expect_request("/api/login", method="POST").respond_with_data(
        "", headers={"Set-Cookie": "TOKEN=abc123; Path=/"}
    )
    httpserver.expect_request("/guarded", method="GET").respond_with_handler(_echo_cookie)

    http.json_request(httpserver.url_for("/api/login"), method="POST", payload={}, cookie_jar=jar)
    # A separate call, as a separate process would make it: the cookie can only
    # have come off disk.
    assert "TOKEN=abc123" in http.json_request(httpserver.url_for("/guarded"), cookie_jar=jar).text
    httpserver.check()


def test_a_session_cookie_survives_being_written_out(httpserver, tmp_path):
    """UniFi's token carries no expiry, so a default save() would discard the
    one cookie worth keeping and every tick would re-login."""
    jar = tmp_path / "cookies"
    httpserver.expect_request("/api/login", method="POST").respond_with_data(
        "", headers={"Set-Cookie": "TOKEN=abc123; Path=/"}
    )
    http.json_request(
        httpserver.url_for("/api/login"), method="POST", payload={}, cookie_jar=str(jar)
    )
    assert "TOKEN" in jar.read_text()
    httpserver.check()


def test_no_jar_means_no_cookies_are_sent(httpserver, tmp_path):
    """The default stays stateless: only callers that ask for a session get one."""
    jar = str(tmp_path / "cookies")
    httpserver.expect_request("/api/login", method="POST").respond_with_data(
        "", headers={"Set-Cookie": "TOKEN=abc123; Path=/"}
    )
    httpserver.expect_request("/guarded", method="GET").respond_with_handler(_echo_cookie)

    http.json_request(httpserver.url_for("/api/login"), method="POST", payload={}, cookie_jar=jar)
    assert "TOKEN" not in http.json_request(httpserver.url_for("/guarded")).text
    httpserver.check()
