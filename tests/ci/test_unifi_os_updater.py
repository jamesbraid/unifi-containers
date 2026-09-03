"""The UniFi OS Server pin updater: firmware API parsing and pin rewriting."""

import json

import pytest

from unifi_containers import pins as pinfile
from unifi_containers.updaters import uos as updater


def record(version, url, sha, platform, channel="release"):
    return {
        "version": version,
        "platform": platform,
        "channel": channel,
        "sha256_checksum": sha,
        "_links": {"data": {"href": url}},
    }


def api_response(version, url, sha, platform="linux-x64"):
    """One real record, wrapped in decoys.

    The API answers a single `product` filter with every platform and channel it
    has, and returns them in no useful order — so a reader that takes entry zero
    picks up whatever happens to be first. The decoys make that fail here rather
    than in a release.
    """
    other = "linux-arm64" if platform == "linux-x64" else "linux-x64"
    return json.dumps(
        {
            "_embedded": {
                "firmware": [
                    record("v9.9.9", "https://x/beta", "f" * 64, platform, "beta-public"),
                    record("v9.9.9", "https://x/other-platform", "e" * 64, other),
                    record(version, url, sha, platform),
                    record("v9.9.9", "https://x/doc", "d" * 64, "document"),
                ]
            }
        }
    ).encode()


AMD64 = api_response(
    "v5.2.0", "https://fw-download.ubnt.com/data/unifi-os-server/x64-520", "a" * 64
)
ARM64 = api_response(
    "v5.2.0",
    "https://fw-download.ubnt.com/data/unifi-os-server/arm64-520",
    "b" * 64,
    platform="linux-arm64",
)
ARM64_OLD = api_response(
    "v5.1.21",
    "https://fw-download.ubnt.com/data/unifi-os-server/arm64-old",
    "c" * 64,
    platform="linux-arm64",
)

PINS = """# comment
UOS_VERSION=5.1.21
UOS_URL_AMD64=https://fw-download.ubnt.com/data/unifi-os-server/old-x64
UOS_SHA256_AMD64=1111111111111111111111111111111111111111111111111111111111111111
UOS_URL_ARM64=https://fw-download.ubnt.com/data/unifi-os-server/old-arm64
UOS_SHA256_ARM64=2222222222222222222222222222222222222222222222222222222222222222
"""

README = """# unifi-containers
| Image | Version | Release notes |
|---|---|---|
| `ghcr.io/jamesbraid/unifi-network` | 10.4.57 | [Release notes](https://x) |
| `ghcr.io/jamesbraid/unifi-os-server` | 5.1.21 | [Release notes](https://old) |
"""


def test_parse_release():
    rel = updater.parse_release(AMD64, "linux-x64")
    assert rel.version == "5.2.0"  # v-prefix stripped
    assert rel.url == "https://fw-download.ubnt.com/data/unifi-os-server/x64-520"
    assert rel.sha256 == "a" * 64


def test_rewrite_pins_roundtrip():
    new = updater.rewrite_pins(
        PINS,
        version="5.2.0",
        amd64=updater.parse_release(AMD64, "linux-x64"),
        arm64=updater.parse_release(ARM64, "linux-arm64"),
    )
    pins = pinfile.env_values(new)
    assert pins["UOS_VERSION"] == "5.2.0"
    assert pins["UOS_URL_AMD64"].endswith("x64-520")
    assert pins["UOS_SHA256_AMD64"] == "a" * 64
    assert pins["UOS_SHA256_ARM64"] == "b" * 64
    assert new.startswith("# comment")  # comments preserved


def test_version_mismatch_raises():
    with pytest.raises(RuntimeError, match="mismatch"):
        updater.check_versions_match(
            updater.parse_release(AMD64, "linux-x64"),
            updater.parse_release(ARM64_OLD, "linux-arm64"),
        )


def test_rewrite_readme_row_targets_os_row_only():
    new = updater.rewrite_readme(README, "5.2.0", "https://new")
    assert "| `ghcr.io/jamesbraid/unifi-os-server` | 5.2.0 | [Release notes](https://new) |" in new
    assert "| `ghcr.io/jamesbraid/unifi-network` | 10.4.57 | [Release notes](https://x) |" in new


def test_parse_release_names_what_it_did_find():
    # The old reader took entry zero on faith. When the asked-for build is absent
    # the error has to say what was there, or an empty channel reads as "no
    # update" — which is how the emptied apt index cost an afternoon.
    payload = json.dumps(
        {"_embedded": {"firmware": [record("v1.0.0", "https://x", "a" * 64, "document")]}}
    ).encode()
    with pytest.raises(RuntimeError, match="release/linux-x64"):
        updater.parse_release(payload, "linux-x64")


def test_parse_release_will_not_take_a_beta_build():
    payload = json.dumps(
        {
            "_embedded": {
                "firmware": [
                    record("v9.9.9", "https://x/beta", "f" * 64, "linux-x64", "beta-public")
                ]
            }
        }
    ).encode()
    with pytest.raises(RuntimeError, match="saw: beta-public/linux-x64"):
        updater.parse_release(payload, "linux-x64")


APP_PAYLOAD = json.dumps(
    {
        "_embedded": {
            "firmware": [
                record("v10.9.9", "https://x/beta", "f" * 64, "uos-deb11-amd64", "beta-public"),
                record("v10.6.101-35991-1", "https://x/amd", "a" * 64, "uos-deb11-amd64"),
                record("v10.6.101-35991-1", "https://x/arm", "a" * 64, "uos-deb11-arm64"),
                record("v10.6.101-35991-1", "https://x/deb13", "b" * 64, "uos-deb13-amd64"),
            ]
        }
    }
).encode()


def test_parse_app_version_reads_the_uos_deb11_pair():
    assert updater.parse_app_version(APP_PAYLOAD) == "10.6.101-35991-1"


def test_parse_app_version_requires_both_platforms():
    one_sided = json.dumps(
        {
            "_embedded": {
                "firmware": [record("v10.6.101-35991-1", "https://x", "a" * 64, "uos-deb11-amd64")]
            }
        }
    ).encode()
    with pytest.raises(RuntimeError, match="uos-deb11-arm64"):
        updater.parse_app_version(one_sided)


def test_parse_app_version_rejects_a_platform_disagreement():
    torn = json.dumps(
        {
            "_embedded": {
                "firmware": [
                    record("v10.6.101-35991-1", "https://x/a", "a" * 64, "uos-deb11-amd64"),
                    record("v10.6.102-36000-1", "https://x/b", "b" * 64, "uos-deb11-arm64"),
                ]
            }
        }
    ).encode()
    with pytest.raises(RuntimeError, match="disagree"):
        updater.parse_app_version(torn)


def test_app_version_moved():
    assert not updater.app_version_moved("10.6.101-35991-1", "10.6.101-35991-1")
    assert updater.app_version_moved("10.5.67-35187-1", "10.6.101-35991-1")
    assert updater.app_version_moved("", "10.6.101-35991-1")  # pin not yet present
    # a repackage of the same upstream is forward motion, like a console sees it
    assert updater.app_version_moved("10.6.101-35991-1", "10.6.101-35992-1")
    # the release channel never goes backwards; a lower upstream is a torn read
    assert not updater.app_version_moved("10.6.101-35991-1", "10.5.67-35187-1")
