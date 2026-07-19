"""Tests for scripts/unifi-os-updater.py (imported via importlib —
the filename has dashes)."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "os_updater", Path(__file__).resolve().parents[1] / "unifi-os-updater.py"
)
updater = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(updater)


def api_response(version, url, sha):
    return json.dumps({
        "_embedded": {"firmware": [{
            "version": version,
            "platform": "linux-x64",
            "sha256_checksum": sha,
            "_links": {"data": {"href": url}},
        }]}
    }).encode()


AMD64 = api_response("v5.2.0", "https://fw-download.ubnt.com/data/unifi-os-server/x64-520", "a" * 64)
ARM64 = api_response("v5.2.0", "https://fw-download.ubnt.com/data/unifi-os-server/arm64-520", "b" * 64)
ARM64_OLD = api_response("v5.1.21", "https://fw-download.ubnt.com/data/unifi-os-server/arm64-old", "c" * 64)

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
    rel = updater.parse_release(AMD64)
    assert rel.version == "5.2.0"  # v-prefix stripped
    assert rel.url == "https://fw-download.ubnt.com/data/unifi-os-server/x64-520"
    assert rel.sha256 == "a" * 64


def test_read_pins():
    pins = updater.read_pins(PINS)
    assert pins["UOS_VERSION"] == "5.1.21"
    assert pins["UOS_SHA256_ARM64"] == "2" * 64


def test_rewrite_pins_roundtrip():
    new = updater.rewrite_pins(
        PINS,
        version="5.2.0",
        amd64=updater.parse_release(AMD64),
        arm64=updater.parse_release(ARM64),
    )
    pins = updater.read_pins(new)
    assert pins["UOS_VERSION"] == "5.2.0"
    assert pins["UOS_URL_AMD64"].endswith("x64-520")
    assert pins["UOS_SHA256_AMD64"] == "a" * 64
    assert pins["UOS_SHA256_ARM64"] == "b" * 64
    assert new.startswith("# comment")  # comments preserved


def test_version_mismatch_raises():
    with pytest.raises(RuntimeError, match="mismatch"):
        updater.check_versions_match(
            updater.parse_release(AMD64), updater.parse_release(ARM64_OLD)
        )


def test_rewrite_readme_row_targets_os_row_only():
    new = updater.rewrite_readme(README, "5.2.0", "https://new")
    assert "| `ghcr.io/jamesbraid/unifi-os-server` | 5.2.0 | [Release notes](https://new) |" in new
    assert "| `ghcr.io/jamesbraid/unifi-network` | 10.4.57 | [Release notes](https://x) |" in new


def test_version_key():
    assert updater.version_key("5.10.1") > updater.version_key("5.9.9")
