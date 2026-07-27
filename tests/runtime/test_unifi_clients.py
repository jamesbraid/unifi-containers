"""The three dialect clients, against a real server.

Focus is on shape-reading: every one of these returns a sane default rather
than raising when the console answers something unexpected, because a gate
that reports "expected 1, found 0" is more useful than a traceback.
"""

from unifi_runtime.unifi.network import NetworkApp, rc_of
from unifi_runtime.unifi.ucore import Ucore
from unifi_runtime.unifi.ulp import Ulp

OK = {"meta": {"rc": "ok"}, "data": []}
ERROR = {"meta": {"rc": "error", "msg": "api.err.Invalid"}, "data": []}


# --- Network Application ---


def test_login_ok_is_true_only_for_rc_ok(httpserver):
    httpserver.expect_request("/api/login", method="POST").respond_with_json(OK)
    assert NetworkApp(httpserver.url_for("")).login_ok("admin", "admin") is True
    httpserver.check()


def test_login_ok_is_false_for_an_error_rc(httpserver):
    httpserver.expect_request("/api/login", method="POST").respond_with_json(ERROR)
    assert NetworkApp(httpserver.url_for("")).login_ok("admin", "wrong") is False
    httpserver.check()


def test_login_ok_is_false_for_the_boot_time_html_placeholder(httpserver):
    # Early in boot the controller serves HTML with HTTP 200 on every path.
    # A status-only probe passes here; that is the bug this guards.
    httpserver.expect_request("/api/login", method="POST").respond_with_data(
        "<html>loading</html>", status=200
    )
    assert NetworkApp(httpserver.url_for("")).login_ok("admin", "admin") is False
    httpserver.check()


def test_login_ok_is_false_when_rate_limited(httpserver):
    httpserver.expect_request("/api/login", method="POST").respond_with_json(
        {"meta": {"rc": "error"}}, status=429
    )
    assert NetworkApp(httpserver.url_for("")).login_ok("admin", "admin") is False
    httpserver.check()


def test_login_ok_is_false_when_nothing_is_listening(dead_url):
    assert NetworkApp(dead_url).login_ok("admin", "admin") is False


def test_is_up_reads_the_meta_block(httpserver):
    httpserver.expect_request("/status", method="GET").respond_with_json(
        {"meta": {"rc": "ok", "up": True}}
    )
    assert NetworkApp(httpserver.url_for("")).is_up() is True
    httpserver.check()


def test_is_up_is_false_when_the_flag_is_absent(httpserver):
    httpserver.expect_request("/status", method="GET").respond_with_json({"meta": {"rc": "ok"}})
    assert NetworkApp(httpserver.url_for("")).is_up() is False
    httpserver.check()


def test_rc_of_reads_either_shape_and_tolerates_junk():
    assert rc_of({"meta": {"rc": "ok"}}) == "ok"
    assert rc_of({"rc": "ok"}) == "ok"
    assert rc_of({}) is None
    assert rc_of([1, 2, 3]) is None
    assert rc_of(None) is None


# --- unifi-core (UOS :443) ---


def test_is_api_answering_requires_the_real_payload(httpserver):
    httpserver.expect_request("/api/system", method="GET").respond_with_json(
        {"isSetup": False, "name": "x"}
    )
    assert Ucore(httpserver.url_for("")).is_api_answering() is True
    httpserver.check()


def test_is_api_answering_rejects_an_empty_200(httpserver):
    # nginx can be up while the API behind it is not.
    httpserver.expect_request("/api/system", method="GET").respond_with_json({}, status=200)
    assert Ucore(httpserver.url_for("")).is_api_answering() is False
    httpserver.check()


def test_login_status_returns_the_code_not_a_bool(httpserver):
    # 429 must stay distinguishable from 401: a rate-limited login is not
    # evidence that the owner is missing.
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_json({}, status=429)
    assert Ucore(httpserver.url_for("")).login_status("admin", "admin") == 429
    httpserver.check()


def test_login_status_is_zero_when_nothing_answers(dead_url):
    assert Ucore(dead_url).login_status("admin", "admin", timeout=2) == 0


def test_setup_posts_the_wizard_payload(httpserver):
    httpserver.expect_request(
        "/api/setup",
        method="POST",
        json={
            "name": "console",
            "username": "admin",
            "password": "admin",
            "country": 840,
            "timezone": "UTC",
            "updateFirmware": False,
            "sendDiagnostics": False,
        },
    ).respond_with_json({})
    Ucore(httpserver.url_for("")).setup("console", "admin", "admin", country=840, timezone="UTC")
    httpserver.check()


def test_api_key_status_sends_the_header_to_the_classic_dialect(httpserver):
    # The header goes out as `X-api-key` because urllib capitalizes it; the
    # matcher compares case-insensitively, as nginx does in front of the real
    # console.
    httpserver.expect_request(
        "/proxy/network/api/s/default/stat/device", method="GET", headers={"X-API-KEY": "k"}
    ).respond_with_json({"data": []})
    assert Ucore(httpserver.url_for("")).api_key_status("k") == 200
    httpserver.check()


# --- ULP (identity service :9080) ---


def info_body(owner_id=None, is_setup=False):
    data = {"is_setuped": is_setup}
    if owner_id:
        data["owner"] = {"unique_id": owner_id}
    return {"code": 1, "data": data}


def test_owner_id_reads_the_uuid(httpserver):
    httpserver.expect_request("/api/v2/info", method="GET").respond_with_json(
        info_body("abc-123", True)
    )
    assert Ulp(httpserver.url_for("")).owner_id() == "abc-123"
    httpserver.check()


def test_owner_id_is_none_before_ulp_learns_the_owner(httpserver):
    httpserver.expect_request("/api/v2/info", method="GET").respond_with_json(info_body())
    assert Ulp(httpserver.url_for("")).owner_id() is None
    httpserver.check()


def test_is_setup_is_the_second_opinion_on_a_429(httpserver):
    httpserver.expect_request("/api/v2/info", method="GET").respond_with_json(
        info_body("abc", True)
    )
    assert Ulp(httpserver.url_for("")).is_setup() is True
    httpserver.check()


def test_is_setup_is_false_on_a_fresh_console(httpserver):
    httpserver.expect_request("/api/v2/info", method="GET").respond_with_json(info_body())
    assert Ulp(httpserver.url_for("")).is_setup() is False
    httpserver.check()


def test_mint_key_returns_the_plaintext_value(httpserver):
    # The name is part of the request the mint has to make, so it is matched
    # rather than read back out of the log.
    httpserver.expect_request(
        "/api/v2/user/abc/keys", method="POST", json={"name": "seeded"}
    ).respond_with_json({"data": {"full_api_key": "0123456789abcdef"}})
    assert Ulp(httpserver.url_for("")).mint_key("abc", "seeded") == "0123456789abcdef"
    httpserver.check()


def test_mint_key_is_none_when_the_route_moved(httpserver):
    httpserver.expect_request("/api/v2/user/abc/keys", method="POST").respond_with_json(
        {}, status=404
    )
    assert Ulp(httpserver.url_for("")).mint_key("abc", "seeded") is None
    httpserver.check()


def test_mint_key_is_none_when_ulp_is_down(dead_url):
    assert Ulp(dead_url).mint_key("abc", "seeded", timeout=2) is None
