"""Lane -> decision. The Forgejo calls are deliberately not tested.

What can regress here is the mapping: which updater a lane runs, which files
its bump stages, which branch the PR targets, and whether "nothing changed"
is recognised. A wrong base branch merges an RC into main.
"""

import pytest

from unifi_containers import update


def test_stable_lane_targets_main_with_the_network_files():
    bump = update.plan("stable", "10.4.57")
    assert bump.version == "10.4.57"
    assert bump.semver == "10.4.57"
    assert bump.prefix == "network"
    assert bump.base == "main"
    assert bump.branch == "bump/network-10.4.57"
    assert bump.files == ("network/Dockerfile", "README.md")


def test_rc_lane_targets_the_rc_branch_and_strips_rc_from_the_semver():
    bump = update.plan("rc", "10.5.62-rc")
    # The tag keeps -rc; the deb URL and the rc-branch comparison must not.
    assert bump.version == "10.5.62-rc"
    assert bump.semver == "10.5.62"
    assert bump.base == "rc"
    assert bump.branch == "bump/network-10.5.62-rc"


def test_uos_lane_stages_pins_not_the_dockerfile():
    bump = update.plan("uos", "5.2.0")
    assert bump.prefix == "unifi-os"
    assert bump.base == "main"
    assert bump.files == ("unifi-os/pins.env", "README.md")
    assert bump.branch == "bump/unifi-os-5.2.0"


@pytest.mark.parametrize("lane", ["stable", "rc", "uos"])
def test_no_newer_version_short_circuits_every_lane(lane):
    # The updaters return None rather than printing a sentence for the caller to
    # recognise, so there is no phrasing to keep in step.
    assert update.plan(lane, None) is None


def test_an_unknown_lane_is_refused():
    with pytest.raises(ValueError):
        update.plan("nightly", "10.4.57")


class FakeForge:
    def __init__(self, rc_dockerfile=None):
        self.rc_dockerfile = rc_dockerfile
        self.calls = []

    def raw_file(self, ref, path):
        self.calls.append(("raw_file", ref, path))
        return self.rc_dockerfile

    def delete_branch(self, name):
        self.calls.append(("delete_branch", name))
        return True

    def create_branch(self, name, from_ref):
        self.calls.append(("create_branch", name, from_ref))


def test_the_rc_branch_is_recreated_from_main():
    forge = FakeForge("ARG PKGURL=https://dl.ui.com/unifi/10.4.57/x.deb\n")
    assert update.reset_rc_branch(forge, update.plan("rc", "10.5.62-rc")) is True
    assert ("delete_branch", "rc") in forge.calls
    assert ("create_branch", "rc", "main") in forge.calls


def test_an_rc_the_branch_already_carries_stands_down():
    forge = FakeForge("ARG PKGURL=https://dl.ui.com/unifi/10.5.62/x.deb\n")
    assert update.reset_rc_branch(forge, update.plan("rc", "10.5.62-rc")) is False
    assert [call[0] for call in forge.calls] == ["raw_file"]


def test_a_missing_rc_branch_is_not_mistaken_for_up_to_date():
    # raw_file returns None when the branch does not exist yet.
    forge = FakeForge(None)
    assert update.reset_rc_branch(forge, update.plan("rc", "10.5.62-rc")) is True
