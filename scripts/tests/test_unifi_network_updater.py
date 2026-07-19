"""Tests for scripts/unifi-network-updater.py (imported via importlib —
the filename has dashes)."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "updater", Path(__file__).resolve().parents[1] / "unifi-network-updater.py"
)
updater = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(updater)


# Real-world shape (verified 2026-07-19): feed titles carry NO channel
# markers — RCs appear with bare version titles. Only the apt repo tells
# stable from RC.
FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>UniFi Network Application 10.5.62</title>
    <link>https://community.ui.com/releases/rc-10-5-62</link></item>
  <item><title>UniFi Network Application 10.5.54</title>
    <link>https://community.ui.com/releases/rc-10-5-54</link></item>
  <item><title>UniFi Network Application 10.4.57</title>
    <link>https://community.ui.com/releases/stable-10-4-57</link></item>
  <item><title>UniFi Network Application 10.6.1 Beta</title>
    <link>https://community.ui.com/releases/beta-10-6-1</link></item>
  <item><title>UniFi Network Application 10.3.58</title>
    <link>https://community.ui.com/releases/stable-10-3-58</link></item>
  <item><title>Some post with no version number</title>
    <link>https://community.ui.com/releases/misc</link></item>
</channel></rss>
"""

APT_PACKAGES = """Package: unifi
Architecture: all
Version: 10.4.57-34628-1
Depends: java17-runtime-headless
Filename: pool/ubiquiti/u/unifi/unifi_10.4.57-34628-1_all.deb
"""

DOCKERFILE = """FROM ubuntu:20.04
ARG PKGURL=https://dl.ui.com/unifi/10.0.162/unifi_sysvinit_all.deb
ARG PKGSHA256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"""

README = """# unifi-containers
| Image | Version | Release notes |
|---|---|---|
| `ghcr.io/jamesbraid/unifi-network` | 10.0.162 | [Release notes](https://old) |
"""


def test_parse_feed_versions():
    rels = updater.parse_feed(FEED)
    assert [r.version for r in rels] == ["10.5.62", "10.5.54", "10.4.57", "10.6.1", "10.3.58"]
    assert [r.is_beta for r in rels] == [False, False, False, True, False]


def test_parse_apt_stable():
    assert updater.parse_apt_stable(APT_PACKAGES) == "10.4.57"


def test_parse_apt_stable_raises_without_unifi():
    with pytest.raises(RuntimeError):
        updater.parse_apt_stable("Package: other\nVersion: 1.0-1\n")


def test_select_stable_follows_apt_not_newest_feed_entry():
    rel = updater.select_release(updater.parse_feed(FEED), "stable", "10.4.57")
    assert rel.version == "10.4.57" and not rel.is_rc
    assert rel.link == "https://community.ui.com/releases/stable-10-4-57"


def test_select_stable_synthesizes_when_apt_version_not_in_feed():
    rel = updater.select_release(updater.parse_feed(FEED), "stable", "10.4.99")
    assert rel.version == "10.4.99" and not rel.is_rc
    assert rel.link == updater.FALLBACK_LINK


def test_select_rc_newest_above_apt_stable_skipping_beta():
    rel = updater.select_release(updater.parse_feed(FEED), "rc", "10.4.57")
    assert rel.version == "10.5.62" and rel.is_rc
    assert rel.tag_version == "10.5.62-rc"


def test_select_rc_none_when_feed_has_nothing_newer():
    rel = updater.select_release(updater.parse_feed(FEED), "rc", "10.5.62")
    assert rel is None


def test_version_key_orders_numerically():
    assert updater.version_key("10.10.2") > updater.version_key("10.9.5")
    assert updater.version_key("10.0.162") < updater.version_key("10.4.57")


def test_read_pins():
    url, sha, version = updater.read_pins(DOCKERFILE)
    assert url == "https://dl.ui.com/unifi/10.0.162/unifi_sysvinit_all.deb"
    assert sha == "a" * 64
    assert version == "10.0.162"


def test_rewrite_dockerfile_roundtrip():
    new = updater.rewrite_dockerfile(
        DOCKERFILE, updater.build_pkgurl("10.4.57"), "b" * 64
    )
    url, sha, version = updater.read_pins(new)
    assert (url, sha, version) == (
        "https://dl.ui.com/unifi/10.4.57/unifi_sysvinit_all.deb", "b" * 64, "10.4.57"
    )


def test_rewrite_dockerfile_raises_without_pins():
    with pytest.raises(RuntimeError):
        updater.rewrite_dockerfile("FROM scratch\n", "u", "s")


def test_rewrite_readme_row():
    new = updater.rewrite_readme(README, "10.4.57", "https://new-notes")
    assert "| `ghcr.io/jamesbraid/unifi-network` | 10.4.57 | [Release notes](https://new-notes) |" in new
    assert "https://old" not in new


def test_rewrite_readme_raises_without_row():
    with pytest.raises(RuntimeError):
        updater.rewrite_readme("# empty\n", "1.2.3", "x")
