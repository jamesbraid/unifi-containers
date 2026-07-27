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
