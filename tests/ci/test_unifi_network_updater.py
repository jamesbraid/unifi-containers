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


def test_parse_feed_versions():
    rels = updater.parse_feed(FEED)
    assert [r.version for r in rels] == ["10.5.62", "10.5.54", "10.4.57", "10.6.1", "10.3.58"]
    assert [r.is_beta for r in rels] == [False, False, False, True, False]


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


def test_select_stable_follows_the_api_not_the_newest_feed_entry():
    rel = updater.select_release(updater.parse_feed(FEED), "stable", "10.4.57")
    assert rel.version == "10.4.57" and not rel.is_rc
    assert rel.link == "https://community.ui.com/releases/stable-10-4-57"


def test_select_stable_synthesizes_when_the_ga_version_is_not_in_the_feed():
    rel = updater.select_release(updater.parse_feed(FEED), "stable", "10.4.99")
    assert rel.version == "10.4.99" and not rel.is_rc
    assert rel.link == updater.FALLBACK_LINK


def test_select_rc_newest_above_ga_skipping_beta():
    rel = updater.select_release(updater.parse_feed(FEED), "rc", "10.4.57")
    assert rel.version == "10.5.62" and rel.is_rc
    assert rel.tag_version == "10.5.62-rc"


def test_select_rc_none_when_feed_has_nothing_newer():
    rel = updater.select_release(updater.parse_feed(FEED), "rc", "10.5.62")
    assert rel is None


def test_select_rc_compares_versions_numerically():
    # 10.10.x beats 10.9.x, which string ordering gets backwards.
    feed = FEED.replace(b"10.5.62", b"10.10.2").replace(b"10.5.54", b"10.9.5")
    rel = updater.select_release(updater.parse_feed(feed), "rc", "10.4.57")
    assert rel.version == "10.10.2"


def test_a_feed_that_does_not_parse_fails_loudly():
    # Reporting "no rc release" for a broken feed would silently stop the lane.
    with pytest.raises(RuntimeError, match="did not parse"):
        updater.parse_feed(b"this is not a feed at all")


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
def hashed(monkeypatch):
    """Serve both feeds offline; record every URL the updater chose to hash."""
    urls = []

    def fake_fetch(url):
        if url == updater.FIRMWARE_API_URL:
            return FIRMWARE_API
        if url == updater.FEED_URL:
            return FEED
        raise AssertionError(f"unexpected fetch: {url}")

    def fake_sha256_of_url(url):
        urls.append(url)
        return RC_SHA256

    monkeypatch.setattr(updater, "fetch", fake_fetch)
    monkeypatch.setattr(updater, "sha256_of_url", fake_sha256_of_url)
    return urls


def test_bump_stable_takes_the_checksum_from_the_api(repo, hashed):
    assert updater.bump(channel="stable", write=True, repo_root=repo) == "10.4.57"
    _, sha, version = updater.read_pins((repo / updater.DOCKERFILE).read_text())
    assert version == "10.4.57"
    assert sha == GA_SHA256
    # The whole point of the API record: no 130MB stream per cron run.
    assert hashed == []


def test_bump_rc_hashes_the_deb_rather_than_reusing_the_ga_checksum(repo, hashed):
    assert updater.bump(channel="rc", write=True, repo_root=repo) == "10.5.62-rc"
    url, sha, version = updater.read_pins((repo / updater.DOCKERFILE).read_text())
    assert version == "10.5.62"
    # No API record exists for an RC, so a GA checksum here would pin a build
    # that cannot be downloaded.
    assert sha == RC_SHA256
    assert sha != GA_SHA256
    assert hashed == [url]
