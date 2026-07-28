"""The Network Application pin updater: channel selection and pin rewriting."""

import pytest

from unifi_containers.updaters import network as updater

# Real-world shape (verified 2026-07-19): feed titles carry NO channel
# markers — RCs appear with bare version titles. Only the firmware API tells
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

GA_SHA256 = "fc378cf8cd2bec3d334bf7b72eabfcd1861e5fae67b9c16735471132105b2072"

# Real-world shape (verified 2026-07-27): one record per platform on the
# release channel, plus a long-stale beta-public one. Only release+debian
# describes the .deb this repo pins; the decoys are here so a broken filter
# fails the test rather than the cron run.
FIRMWARE_API = b"""{"_embedded": {"firmware": [
  {"channel": "beta-public", "platform": "document",
   "version": "v5.11.10+atag-5.11.10-12337",
   "version_major": 5, "version_minor": 11, "version_patch": 10,
   "sha256_checksum": "1791685039ea795970bcc7a61eec854058e3e6fc13c52770e31e20f3beb622eb"},
  {"channel": "release", "platform": "windows",
   "version": "v10.4.57+atag-10.4.57-34628",
   "version_major": 10, "version_minor": 4, "version_patch": 57,
   "sha256_checksum": "877458ef776a8dbcf2b605e63b3bd69b7f1cc84c8069b650ce97ee619a30cfcb"},
  {"channel": "release", "platform": "debian",
   "version": "v10.4.57+atag-10.4.57-34628",
   "version_major": 10, "version_minor": 4, "version_patch": 57,
   "md5": "68e0494402fd99b319d0d6573fdb80a3", "file_size": 130327194,
   "sha256_checksum": "fc378cf8cd2bec3d334bf7b72eabfcd1861e5fae67b9c16735471132105b2072",
   "_links": {"data": {"href": "https://fw-download.ubnt.com/data/unifi-controller/x.deb"}}},
  {"channel": "release", "platform": "macos",
   "version": "v10.4.57+atag-10.4.57-34628",
   "version_major": 10, "version_minor": 4, "version_patch": 57,
   "sha256_checksum": "9d6a698ce10643f9fb27c6ca29af346b0791179fba86def41498943162d686ca"}
]}}"""

DOCKERFILE = """FROM ubuntu:20.04
ARG PKGURL=https://dl.ui.com/unifi/10.0.162/unifi_sysvinit_all.deb
ARG PKGSHA256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"""

README = """# unifi-containers
| Image | Version | Release notes |
|---|---|---|
| `ghcr.io/jamesbraid/unifi-network` | 10.0.162 | [Release notes](https://old) |
"""


def test_feed_links_maps_versions_to_release_notes():
    links = updater.feed_links(FEED)
    assert links["10.4.57"] == "https://community.ui.com/releases/stable-10-4-57"


def test_the_release_notes_link_is_never_worth_failing_a_bump(monkeypatch):
    """The feed is documentation now — the version comes from the firmware API.

    It carries no channel field, so it cannot say which versions are GA; all it
    still supplies is the README's deep link. A feed that is down or reshaped
    must therefore degrade to the index page, not stop the pin advancing.
    """
    monkeypatch.setattr(updater, "fetch", lambda _url: FEED)
    assert updater.release_notes_link("10.4.57").endswith("stable-10-4-57")
    # absent from the feed
    assert updater.release_notes_link("11.0.0") == updater.FALLBACK_LINK

    def explode(_url):
        raise RuntimeError("feed is down")

    monkeypatch.setattr(updater, "fetch", explode)
    assert updater.release_notes_link("10.4.57") == updater.FALLBACK_LINK


def test_parse_ga_build_takes_the_release_debian_record():
    ga = updater.parse_ga_build(FIRMWARE_API)
    assert ga.version == "10.4.57"
    assert ga.sha256 == GA_SHA256


def test_parse_ga_build_raises_when_the_debian_record_is_absent():
    # An API that answers with everything except the .deb is not "no update
    # available", and the error has to say so — the apt index this replaced
    # went empty and reported itself as a missing package.
    payload = FIRMWARE_API.replace(b'"platform": "debian"', b'"platform": "unix"')
    with pytest.raises(RuntimeError, match=r"no release/debian build"):
        updater.parse_ga_build(payload)


def test_parse_ga_build_names_what_did_come_back():
    with pytest.raises(RuntimeError, match=r"seen: none"):
        updater.parse_ga_build(b'{"_embedded": {"firmware": []}}')


def test_read_pins():
    url, sha, version = updater.read_pins(DOCKERFILE)
    assert url == "https://dl.ui.com/unifi/10.0.162/unifi_sysvinit_all.deb"
    assert sha == "a" * 64
    assert version == "10.0.162"


def test_rewrite_dockerfile_roundtrip():
    new = updater.rewrite_dockerfile(DOCKERFILE, updater.build_pkgurl("10.4.57"), "b" * 64)
    url, sha, version = updater.read_pins(new)
    assert (url, sha, version) == (
        "https://dl.ui.com/unifi/10.4.57/unifi_sysvinit_all.deb",
        "b" * 64,
        "10.4.57",
    )


def test_rewrite_dockerfile_raises_without_pins():
    with pytest.raises(RuntimeError):
        updater.rewrite_dockerfile("FROM scratch\n", "u", "s")


def test_rewrite_readme_row():
    new = updater.rewrite_readme(README, "10.4.57", "https://new-notes")
    assert (
        "| `ghcr.io/jamesbraid/unifi-network` | 10.4.57 | [Release notes](https://new-notes) |"
        in new
    )
    assert "https://old" not in new


def test_rewrite_readme_raises_without_row():
    with pytest.raises(RuntimeError):
        updater.rewrite_readme("# empty\n", "1.2.3", "x")


RC_SHA256 = "c" * 64


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "network").mkdir()
    (tmp_path / updater.DOCKERFILE).write_text(DOCKERFILE)
    (tmp_path / updater.README).write_text(README)
    return tmp_path


@pytest.fixture
def offline(monkeypatch):
    """Serve both sources offline; record anything the updater chose to hash."""
    hashed = []

    def fake_fetch(url):
        if url == updater.FIRMWARE_API_URL:
            return FIRMWARE_API
        if url == updater.FEED_URL:
            return FEED
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(updater, "fetch", fake_fetch)
    monkeypatch.setattr(
        updater, "sha256_of_url", lambda url: hashed.append(url) or RC_SHA256, raising=False
    )
    return hashed


def test_bump_pins_the_ga_version_and_takes_its_checksum_from_the_api(repo, offline):
    assert updater.bump(write=True, repo_root=repo) == "10.4.57"
    url, sha, version = updater.read_pins((repo / updater.DOCKERFILE).read_text())
    assert version == "10.4.57"
    assert sha == GA_SHA256
    # The whole point of the API record: no ~130MB stream per cron run.
    assert offline == []
    # 10.5.62 is newer in the feed, and is deliberately NOT chosen: the feed
    # cannot say whether it is GA, and this repo releases GA only.
    assert "10.5.62" not in url


def test_bump_is_none_when_the_pin_already_holds_the_ga_version(repo, offline):
    (repo / updater.DOCKERFILE).write_text(
        DOCKERFILE.replace("10.0.162", "10.4.57").replace(
            "PKGSHA256=" + "0" * 64, "PKGSHA256=" + GA_SHA256
        )
    )
    assert updater.bump(write=True, repo_root=repo) is None
