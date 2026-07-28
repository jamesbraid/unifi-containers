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
    assert bump.prefix == "network"
    assert bump.base == "main"
    assert bump.branch == "bump/network-10.4.57"
    assert bump.files == ("network/Dockerfile", "README.md")


def test_uos_lane_stages_pins_not_the_dockerfile():
    bump = update.plan("uos", "5.2.0")
    assert bump.prefix == "unifi-os"
    assert bump.base == "main"
    assert bump.files == ("unifi-os/pins.env", "README.md")
    assert bump.branch == "bump/unifi-os-5.2.0"


@pytest.mark.parametrize("lane", ["stable", "uos"])
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
