"""The owner-seed state machine, against a real HTTP server.

Every transition here exists because of a specific failure, so each test is
named for the failure it prevents rather than for the function it calls. One
server serves both dialects: ucore's paths and ULP's do not overlap, and the
shared request log is what lets a test assert that `/api/setup` was *not*
POSTed. Where a path is deliberately left unregistered, `httpserver.check()`
is that assertion — an unexpected call is answered 500 and recorded.
"""

import json

import pytest
from pytest_httpserver import RequestMatcher

from unifi_runtime.seed import uos_owner
from unifi_runtime.unifi.ucore import CLASSIC_PROXY, Ucore
from unifi_runtime.unifi.ulp import Ulp

KEY_PROBE = CLASSIC_PROXY.format(site="default")
OWNER_ID = "b7e1c0de-0000-5000-8000-0123456789ab"
MINT_PATH = f"/api/v2/user/{OWNER_ID}/keys"

SETUP = RequestMatcher("/api/setup", method="POST")
MINT = RequestMatcher(MINT_PATH, method="POST")
INFO = RequestMatcher("/api/v2/info", method="GET")
KEY = RequestMatcher(KEY_PROBE, method="GET")


@pytest.fixture
def logged():
    """Recorded log lines. Pass `logged.append` as the `log` argument."""
    return []


@pytest.fixture
def nap():
    """Recorded sleep durations. Pass `nap.append` as the `sleep` argument."""
    return []


def clients(httpserver):
    return Ucore(httpserver.url_for("")), Ulp(httpserver.url_for(""))


def login(httpserver, status=200):
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_data(status=status)


def login_once(httpserver, status):
    """Answer the *next* login with `status`; later ones fall through."""
    httpserver.expect_oneshot_request("/api/auth/login", method="POST").respond_with_data(
        status=status
    )


def ulp_info(httpserver, is_setuped=False, owner=None):
    body = {"data": {"is_setuped": is_setuped}}
    if owner:
        body["data"]["owner"] = {"unique_id": owner}
    httpserver.expect_request("/api/v2/info", method="GET").respond_with_json(body)


DEFAULTS = {
    "username": "admin",
    "password": "admin",
    "country": 840,
    "timezone": "UTC",
    "console_name": "unifi-os-sim",
    "seed_api_key": False,
    "key_file": "/nonexistent/api-key",
    "key_name": "unifi-containers-seeded",
}


def config(**overrides):
    return uos_owner.Config(**{**DEFAULTS, **overrides})


# --- ensure_owner -----------------------------------------------------


def test_a_working_login_means_nothing_to_do(httpserver, logged, nap):
    login(httpserver)
    ucore, ulp = clients(httpserver)

    assert uos_owner.ensure_owner(ucore, ulp, config(), logged.append, sleep=nap.append)

    # Nothing registers /api/setup or /api/v2/info, so touching either is a
    # 500 the server records and check() raises.
    httpserver.check()
    assert "already seeded (login OK) — nothing to do" in logged


def test_rate_limited_login_never_re_runs_setup(httpserver, logged, nap):
    # The failure this prevents: UniFi answers a login burst with 429
    # AUTHENTICATION_FAILED_LIMIT_REACHED and a Retry-After of up to an
    # hour. Reading that as "no owner" makes this unit POST /api/setup at
    # an already-configured console, collect five 500s, and fail the seed.
    httpserver.expect_request("/api/auth/login", method="POST").respond_with_json(
        {"code": "AUTHENTICATION_FAILED_LIMIT_REACHED"},
        status=429,
        headers={"Retry-After": "3600"},
    )
    ulp_info(httpserver, is_setuped=True)
    ucore, ulp = clients(httpserver)

    assert uos_owner.ensure_owner(ucore, ulp, config(), logged.append, sleep=nap.append)

    httpserver.check()  # /api/setup is unregistered
    assert nap == []
    assert any("ULP reports setup complete" in line for line in logged)


def test_ulp_is_only_consulted_when_login_fails(httpserver, logged, nap):
    login(httpserver)
    ulp_info(httpserver, is_setuped=False)
    ucore, ulp = clients(httpserver)

    uos_owner.ensure_owner(ucore, ulp, config(), logged.append, sleep=nap.append)

    httpserver.assert_request_made(INFO, count=0)


def test_already_setup_500_is_a_success_signal(httpserver, logged, nap):
    # unifi-core answers an already-complete setup with 500 "Device is
    # already setup". Retrying that five times just wastes ten minutes.
    login(httpserver, status=401)
    ulp_info(httpserver, is_setuped=False)
    httpserver.expect_request("/api/setup", method="POST").respond_with_json(
        {"message": "Device is already setup"}, status=500
    )
    ucore, ulp = clients(httpserver)

    assert uos_owner.ensure_owner(ucore, ulp, config(), logged.append, sleep=nap.append)

    httpserver.assert_request_made(SETUP, count=1)
    assert nap == []
    assert any("already complete" in line for line in logged)
    httpserver.check()


def test_setup_then_a_working_login_completes_the_seed(httpserver, logged, nap):
    login_once(httpserver, 401)
    login(httpserver)
    ulp_info(httpserver, is_setuped=False)
    httpserver.expect_request("/api/setup", method="POST").respond_with_json({"data": {}})
    ucore, ulp = clients(httpserver)

    assert uos_owner.ensure_owner(ucore, ulp, config(), logged.append, sleep=nap.append)
    httpserver.assert_request_made(SETUP, count=1)
    httpserver.check()


def test_setup_sends_country_as_a_json_number(httpserver, logged, nap):
    login(httpserver, status=401)
    ulp_info(httpserver, is_setuped=False)
    httpserver.expect_request("/api/setup", method="POST").respond_with_data(
        "already setup", status=500
    )
    ucore, ulp = clients(httpserver)

    uos_owner.ensure_owner(ucore, ulp, config(country=826), logged.append, sleep=nap.append)

    sent, _ = next(httpserver.iter_matching_requests(SETUP))
    body = json.loads(sent.data)
    # 826, not "826": the wire format is a JSON number.
    assert body["country"] == 826
    assert body["updateFirmware"] is False and body["sendDiagnostics"] is False
    httpserver.check()


def test_a_console_that_never_completes_setup_fails_after_five_tries(httpserver, logged, nap):
    login(httpserver, status=401)
    ulp_info(httpserver, is_setuped=False)
    httpserver.expect_request("/api/setup", method="POST").respond_with_data(
        "gateway is sulking", status=503
    )
    ucore, ulp = clients(httpserver)

    assert not uos_owner.ensure_owner(ucore, ulp, config(), logged.append, sleep=nap.append)

    httpserver.assert_request_made(SETUP, count=uos_owner.SETUP_ATTEMPTS)
    # Four gaps between five attempts; no pointless wait after the last.
    assert nap == [uos_owner.SETUP_RETRY_SECONDS] * 4
    assert any("FAILED to seed owner" in line for line in logged)
    httpserver.check()


# --- key_check --------------------------------------------------------


def test_key_check_accepts_a_200(httpserver, logged, nap):
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_json({"data": []})
    ucore, _ = clients(httpserver)
    assert uos_owner.key_check(ucore, "k", logged.append, sleep=nap.append) == uos_owner.KEY_OK
    httpserver.check()


@pytest.mark.parametrize("status", [401, 403])
def test_key_check_reports_a_rejection_immediately(httpserver, logged, nap, status):
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_data(status=status)
    ucore, _ = clients(httpserver)
    verdict = uos_owner.key_check(ucore, "k", logged.append, sleep=nap.append)
    assert verdict == uos_owner.KEY_REJECTED
    httpserver.assert_request_made(KEY, count=1)
    httpserver.check()


def test_key_check_keeps_waiting_through_a_slow_boot(httpserver, logged, nap):
    # 502 while the Network App comes up is not a verdict on the key.
    for _ in range(2):
        httpserver.expect_oneshot_request(KEY_PROBE, method="GET").respond_with_data(status=502)
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_json({"data": []})
    ucore, _ = clients(httpserver)
    assert uos_owner.key_check(ucore, "k", logged.append, sleep=nap.append) == uos_owner.KEY_OK
    assert nap == [uos_owner.POLL_SECONDS] * 2
    httpserver.check()


def test_key_check_running_out_of_patience_is_its_own_verdict(httpserver, logged, nap):
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_data(status=503)
    ucore, _ = clients(httpserver)
    assert (
        uos_owner.key_check(ucore, "k", logged.append, sleep=nap.append, attempts=4)
        == uos_owner.KEY_NO_VERDICT
    )
    httpserver.check()


def test_key_check_treats_a_dead_endpoint_as_no_verdict(dead_url, logged, nap):
    assert (
        uos_owner.key_check(Ucore(dead_url), "k", logged.append, sleep=nap.append, attempts=3)
        == uos_owner.KEY_NO_VERDICT
    )


# --- ensure_api_key ---------------------------------------------------


def test_a_working_existing_key_is_reused_not_re_minted(httpserver, tmp_path, logged, nap):
    keyfile = tmp_path / "api-key"
    keyfile.write_text("already-good\n")
    # The key from the file is part of the matcher, so probing with anything
    # else — or minting at all, since the mint path is unregistered — is a
    # 500 that check() raises.
    httpserver.expect_request(
        KEY_PROBE, method="GET", headers={"X-API-KEY": "already-good"}
    ).respond_with_json({"data": []})
    ulp_info(httpserver, is_setuped=True, owner=OWNER_ID)
    ucore, ulp = clients(httpserver)

    assert uos_owner.ensure_api_key(
        ucore, ulp, config(key_file=str(keyfile)), logged.append, sleep=nap.append
    )

    httpserver.check()
    assert keyfile.read_text() == "already-good\n"
    assert any("reusing API key" in line for line in logged)


def test_a_rejected_existing_key_is_replaced(httpserver, tmp_path, logged, nap):
    keyfile = tmp_path / "api-key"
    keyfile.write_text("stale\n")
    httpserver.expect_oneshot_request(KEY_PROBE, method="GET").respond_with_data(status=401)
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_json({"data": []})
    ulp_info(httpserver, is_setuped=True, owner=OWNER_ID)
    httpserver.expect_request(MINT_PATH, method="POST").respond_with_json(
        {"data": {"full_api_key": "freshly-minted"}}
    )
    ucore, ulp = clients(httpserver)

    assert uos_owner.ensure_api_key(
        ucore, ulp, config(key_file=str(keyfile)), logged.append, sleep=nap.append
    )

    httpserver.assert_request_made(MINT, count=1)
    assert keyfile.read_text() == "freshly-minted\n"
    httpserver.check()


def test_no_verdict_on_an_existing_key_fails_without_minting(httpserver, tmp_path, logged, nap):
    # The regression this guards: collapsing "no verdict" into "rejected"
    # makes every slow boot mint a duplicate key and leak the old one. The
    # mint is registered and would succeed, which is the point.
    keyfile = tmp_path / "api-key"
    keyfile.write_text("perfectly-fine\n")
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_data(status=503)
    ulp_info(httpserver, is_setuped=True, owner=OWNER_ID)
    httpserver.expect_request(MINT_PATH, method="POST").respond_with_json(
        {"data": {"full_api_key": "should-never-exist"}}
    )
    ucore, ulp = clients(httpserver)

    assert not uos_owner.ensure_api_key(
        ucore, ulp, config(key_file=str(keyfile)), logged.append, sleep=nap.append
    )

    httpserver.assert_request_made(MINT, count=0)
    assert keyfile.read_text() == "perfectly-fine\n"
    httpserver.check()


def test_a_minted_key_is_proven_before_it_is_published(httpserver, tmp_path, logged, nap):
    # Publishing a credential the harness cannot use is worse than
    # publishing none: the failure surfaces in someone else's test run.
    keyfile = tmp_path / "api-key"
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_data(status=401)
    ulp_info(httpserver, is_setuped=True, owner=OWNER_ID)
    httpserver.expect_request(MINT_PATH, method="POST").respond_with_json(
        {"data": {"full_api_key": "born-dead"}}
    )
    ucore, ulp = clients(httpserver)

    assert not uos_owner.ensure_api_key(
        ucore, ulp, config(key_file=str(keyfile)), logged.append, sleep=nap.append
    )

    assert not keyfile.exists()
    assert any("does not authenticate" in line for line in logged)
    httpserver.check()


def test_a_missing_owner_id_fails_rather_than_minting_at_nobody(httpserver, tmp_path, logged, nap):
    ulp_info(httpserver, is_setuped=True)  # no owner block yet
    ucore, ulp = clients(httpserver)

    assert not uos_owner.ensure_api_key(
        ucore,
        ulp,
        config(key_file=str(tmp_path / "api-key")),
        logged.append,
        sleep=nap.append,
    )

    assert any("never reported an owner id" in line for line in logged)
    httpserver.check()  # the mint path is unregistered


def test_the_mint_names_the_key(httpserver, tmp_path, logged, nap):
    keyfile = tmp_path / "api-key"
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_json({"data": []})
    ulp_info(httpserver, is_setuped=True, owner=OWNER_ID)
    # The name is matched, so minting an unnamed key finds no handler.
    httpserver.expect_request(MINT_PATH, method="POST", json={"name": "harness"}).respond_with_json(
        {"data": {"full_api_key": "k-1"}}
    )
    ucore, ulp = clients(httpserver)

    cfg = config(key_file=str(keyfile), key_name="harness")
    uos_owner.ensure_api_key(ucore, ulp, cfg, logged.append, sleep=nap.append)

    httpserver.check()


# --- publish ----------------------------------------------------------


def test_publish_is_atomic_and_world_readable(tmp_path):
    target = tmp_path / "api-key"
    assert uos_owner.publish_key(str(target), "secret")
    assert target.read_text() == "secret\n"
    assert oct(target.stat().st_mode)[-3:] == "644"
    assert list(tmp_path.iterdir()) == [target]  # no temp file left behind


def test_publish_reports_an_unwritable_target(tmp_path):
    assert not uos_owner.publish_key(str(tmp_path / "no" / "such" / "dir"), "s")


# --- the whole run ----------------------------------------------------


def test_a_fresh_console_gets_an_owner_and_a_proven_key(httpserver, tmp_path, logged, nap):
    keyfile = tmp_path / "api-key"
    httpserver.expect_request("/api/system", method="GET").respond_with_json({"isSetup": False})
    login_once(httpserver, 401)
    login(httpserver)
    ulp_info(httpserver, is_setuped=True, owner=OWNER_ID)
    httpserver.expect_request("/api/setup", method="POST").respond_with_json({"data": {}})
    httpserver.expect_request(MINT_PATH, method="POST").respond_with_json(
        {"data": {"full_api_key": "end-to-end"}}
    )
    httpserver.expect_request(KEY_PROBE, method="GET").respond_with_json({"data": []})
    ucore, ulp = clients(httpserver)

    rc = uos_owner.run(
        config(key_file=str(keyfile), seed_api_key=True),
        ucore=ucore,
        ulp=ulp,
        log=logged.append,
        sleep=nap.append,
    )

    assert rc == 0
    assert keyfile.read_text() == "end-to-end\n"
    httpserver.check()


def test_the_key_seed_is_skipped_when_it_is_off(httpserver, tmp_path, logged, nap):
    keyfile = tmp_path / "api-key"
    httpserver.expect_request("/api/system", method="GET").respond_with_json({"isSetup": True})
    login(httpserver)
    ucore, ulp = clients(httpserver)

    rc = uos_owner.run(
        config(key_file=str(keyfile), seed_api_key=False),
        ucore=ucore,
        ulp=ulp,
        log=logged.append,
        sleep=nap.append,
    )

    assert rc == 0
    assert not keyfile.exists()
    # Neither the mint path nor the key probe is registered.
    httpserver.check()


def test_a_failed_owner_seed_exits_nonzero(httpserver, logged, nap):
    httpserver.expect_request("/api/system", method="GET").respond_with_json({"isSetup": False})
    login(httpserver, status=401)
    ulp_info(httpserver, is_setuped=False)
    httpserver.expect_request("/api/setup", method="POST").respond_with_data("nope", status=503)
    ucore, ulp = clients(httpserver)

    assert uos_owner.run(config(), ucore=ucore, ulp=ulp, log=logged.append, sleep=nap.append) == 1
    httpserver.check()


# --- logging ----------------------------------------------------------


def test_log_goes_to_stdout_and_to_the_file(tmp_path):
    # journald is unreliable in this image; a failed seed must stay
    # diagnosable with `docker exec ... cat`.
    printed = []
    path = tmp_path / "uos-seed-owner.log"
    log = uos_owner.make_log(str(path), out=printed.append)

    log("first")
    log("second")

    assert printed == ["uos-seed-owner: first", "uos-seed-owner: second"]
    assert path.read_text() == "uos-seed-owner: first\nuos-seed-owner: second\n"


def test_an_unwritable_log_file_does_not_abort_the_seed(tmp_path):
    printed = []
    log = uos_owner.make_log(str(tmp_path / "no" / "dir" / "x.log"), out=printed.append)
    log("still says it")
    assert printed == ["uos-seed-owner: still says it"]


def test_config_from_env_reads_the_seeded_variant_settings():
    cfg = uos_owner.config_from_env(
        {
            "UOS_ADMIN_USER": "operator",
            "UOS_SEED_API_KEY": "true",
            "UOS_API_KEY_FILE": "/unifi/api-key",
            "UOS_COUNTRY": "826",
        }
    )
    assert cfg.username == "operator"
    assert cfg.password == "admin"
    assert cfg.seed_api_key is True
    assert cfg.key_file == "/unifi/api-key"
    # An int, not "826": the wire format is a JSON number.
    assert cfg.country == 826
    assert uos_owner.config_from_env({}).country == 840


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("1", True),
        ("0", False),
        ("yes", False),
        ("", False),
    ],
)
def test_the_key_seed_flag_spellings(value, expected):
    assert uos_owner.config_from_env({"UOS_SEED_API_KEY": value}).seed_api_key is expected
