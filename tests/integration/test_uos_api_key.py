"""The X-API-KEY the seeded UniFi OS image publishes, proven against a real console.

`test_a_bogus_key_is_rejected` is what makes the rest mean anything: a console
answering 200 to everything would pass every positive check here.

Probes run inside the container: ULP only trusts localhost, and 127.0.0.1 inside
the container is the one address that works both in CI and in a local run.
"""

import json

import pytest

from unifi_containers import testing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

#: The production `unifi-os` dialect: the classic Network API and the modern
#: integration API, both behind unifi-core's proxy on :443.
CLASSIC = "https://127.0.0.1/proxy/network/api/s/default/stat/device"
INTEGRATION = "https://127.0.0.1/proxy/network/integration/v1/sites"
ULP_KEYS = "http://127.0.0.1:9080/api/v2/keys"

BOGUS_KEY = "bogus-key-000000000000000000000"  # gitleaks:allow


def published_key(container):
    key = (testing.read_file(container, testing.UOS_API_KEY_FILE) or "").strip()
    assert key, f"{testing.UOS_API_KEY_FILE} is missing or empty: the seed published no key"
    return key


def keys_named(container, name):
    body = json.loads(testing.exec_in(container, ["curl", "-s", "--max-time", "10", ULP_KEYS]))
    return [key for key in body["data"] if key.get("name") == name]


def test_the_published_key_authenticates_the_classic_dialect(uos_seeded):
    key = published_key(uos_seeded)

    assert testing.http_status(uos_seeded, CLASSIC, {"X-API-KEY": key}) == "200"


def test_the_published_key_authenticates_the_modern_integration_api(uos_seeded):
    key = published_key(uos_seeded)

    assert testing.http_status(uos_seeded, INTEGRATION, {"X-API-KEY": key}) == "200"


def test_a_bogus_key_is_rejected(uos_seeded):
    status = testing.http_status(uos_seeded, CLASSIC, {"X-API-KEY": BOGUS_KEY})

    assert status in ("401", "403"), (
        f"a bogus key got HTTP {status}: the console is not enforcing X-API-KEY, "
        "so the other checks here prove nothing"
    )


def test_the_seed_mints_exactly_one_key_under_its_own_name(uos_seeded):
    assert len(keys_named(uos_seeded, testing.UOS_API_KEY_NAME)) == 1


def test_the_key_survives_a_restart_and_no_duplicate_is_minted(uos_seeded):
    # Last on purpose: it restarts the container the module shares.
    before = published_key(uos_seeded)

    testing.restart(uos_seeded, testing.UOS_STARTUP_TIMEOUT)

    assert published_key(uos_seeded) == before, (
        "the key changed across a restart: the seed is re-minting every boot "
        "instead of reusing the published key"
    )
    assert len(keys_named(uos_seeded, testing.UOS_API_KEY_NAME)) == 1
    assert testing.http_status(uos_seeded, CLASSIC, {"X-API-KEY": before}) == "200"
