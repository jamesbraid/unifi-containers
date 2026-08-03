"""The pin readers, against the repo's real files.

These are guards in the release lanes: cut-release refuses to run when a
product's pin has moved past its highest tag, because that situation calls
for a version bump and cutting a rebuild instead would publish the old
version under a new revision.
"""

import re

import pytest
from conftest import REPO_ROOT

from unifi_containers import pins

VERSION = re.compile(r"^\d+\.\d+\.\d+$")


# The two tests below assert shape and provenance, never the version itself.
# Asserting the literal made every bump PR red by construction: the update lane
# exists to move these pins, nothing keeps a hardcoded copy in step, and the
# lane's own PR then failed the suite until someone hand-edited this file.
# Resist restoring the literal. What these guards owe the release lanes is that
# the reader finds a real version in the right file — `cut-release` reads None
# as "nothing to release" and skips in silence, so None is the failure that
# matters. `verify-pins` is what checks the value.


def test_reads_the_real_network_pin():
    version = pins.pinned_version("network", REPO_ROOT)
    assert version and VERSION.match(version)
    # From the Network Dockerfile's PKGURL, not some other product's pin.
    assert f"/unifi/{version}/" in (REPO_ROOT / pins.NETWORK_DOCKERFILE).read_text()


def test_reads_the_real_uos_pin():
    version = pins.pinned_version("unifi-os", REPO_ROOT)
    assert version and VERSION.match(version)
    assert f"{pins.UOS_KEY}={version}" in (REPO_ROOT / pins.UOS_PINS).read_text()


def test_an_unknown_product_raises_rather_than_returning_none():
    # A guard that reads a typo as "nothing to check" is worse than no guard.
    with pytest.raises(ValueError, match="unknown product"):
        pins.pinned_version("nonesuch", REPO_ROOT)


def test_pkgurl_version_ignores_a_dockerfile_without_a_pin():
    assert pins.pkgurl_version("FROM ubuntu:24.04\n") is None
    assert pins.pkgurl_version("ARG PKGSHA256=deadbeef\n") is None
    assert pins.pkgurl_version("") is None
    assert pins.pkgurl_version(None) is None


def test_pkgurl_version_does_not_match_a_commented_pin():
    # A commented-out pin is not the pin. Matching it would make the guard
    # compare against a stale version.
    assert (
        pins.pkgurl_version(
            "# ARG PKGURL=https://dl.ui.com/unifi/9.0.1/unifi_sysvinit_all.deb\n"
            "ARG PKGURL=https://dl.ui.com/unifi/10.4.57/unifi_sysvinit_all.deb\n"
        )
        == "10.4.57"
    )


def test_env_values_skips_comments_and_blanks_and_strips_whitespace():
    text = "# a comment\n\nUOS_VERSION=5.1.21\n  UOS_URL_AMD64=https://x/y \n"
    assert pins.env_values(text) == {"UOS_VERSION": "5.1.21", "UOS_URL_AMD64": "https://x/y"}


def test_an_empty_uos_pin_reads_as_absent(tmp_path):
    # An empty pin must not compare equal to an empty version string and
    # silently satisfy the guard.
    (tmp_path / "unifi-os").mkdir()
    (tmp_path / pins.UOS_PINS).write_text("UOS_VERSION=\n")
    assert pins.pinned_version("unifi-os", tmp_path) is None
