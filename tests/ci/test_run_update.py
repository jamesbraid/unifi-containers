"""Lane -> decision. The Forgejo calls are deliberately not tested.

What can regress here is the mapping: which updater a lane runs, which files
its bump stages, which branch the PR targets, and whether "nothing changed"
is recognised. A wrong base branch merges an RC into main.
"""

from datetime import datetime, timezone

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


# --- the stalled-lane assertion ---------------------------------------
#
# A lane that cannot land its bump reports green on every run outcome, which
# is how a broken credential froze this machinery for two days unnoticed.


class _Client:
    def __init__(self, opened_at):
        self._opened_at = opened_at

    def pull_opened_at(self, head):
        return self._opened_at


BUMP = update.Bump(
    lane="uos",
    version="5.1.42",
    prefix="unifi-os",
    files=(),
    base="main",
    branch="bump/unifi-os-5.1.42",
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "opened,expected",
    [
        ("2026-09-13T11:00:00Z", 1.0),
        ("2026-09-11T12:00:00Z", 48.0),
        (datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc), 6.0),
        # A forge that answers without a timezone still means UTC.
        (datetime(2026, 9, 13, 6, 0), 6.0),
    ],
)
def test_hours_open_reads_every_shape_the_forge_returns(opened, expected):
    assert update.hours_open(opened, now=NOW) == pytest.approx(expected)


def test_a_fresh_bump_pr_is_not_a_stall():
    # The normal path opens, checks and merges within minutes.
    update.check_not_stalled(_Client(NOW.isoformat()), BUMP)


def test_a_bump_that_has_not_landed_in_a_day_fails_the_lane():
    with pytest.raises(update.forge.ForgeError, match="stalled rather than up to date"):
        update.check_not_stalled(_Client("2026-09-11T12:00:00Z"), BUMP)


def test_the_error_names_the_lane_and_the_version_it_is_stuck_below():
    with pytest.raises(update.forge.ForgeError) as excinfo:
        update.check_not_stalled(_Client("2026-09-11T12:00:00Z"), BUMP)
    assert "uos" in str(excinfo.value) and "5.1.42" in str(excinfo.value)


def test_an_unknown_open_time_is_not_a_stall():
    # A missing field must not invent a failure; only a measured age can.
    update.check_not_stalled(_Client(None), BUMP)
