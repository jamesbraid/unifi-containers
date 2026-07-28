"""The release model.

One regex now answers every question about a tag, so these tests are the
whole contract: what a tag means, which build comes next, and which sliding
GHCR tags move.
"""

import pytest

from unifi_containers import release

STABLE = "network/10.4.57-1"
RETIRED_RC = "network/10.5.66-rc-1"
UOS = "unifi-os/5.1.21-3"


def test_a_tag_reads_left_to_right():
    rel = release.parse(STABLE)
    assert (rel.product, rel.upstream, rel.revision) == ("network", "10.4.57", 1)
    assert rel.version == "10.4.57-1"
    assert rel.image == "ghcr.io/jamesbraid/unifi-network"


def test_a_retired_rc_tag_is_not_a_release():
    # This repo releases GA only, and no source states which upstream versions
    # are candidates. Tags from when it tried are left in history, and must not
    # register as build 1 of anything.
    assert release.parse(RETIRED_RC) is None
    assert release.releases_for([RETIRED_RC, STABLE], "network") == [release.parse(STABLE)]


def test_the_git_tag_round_trips():
    for tag in (STABLE, UOS):
        assert release.parse(tag).git_tag == tag


@pytest.mark.parametrize(
    "tag",
    [
        "v10.4.57-1",  # the old scheme
        "unifi-os-v5.1.21-2",  # the old scheme
        "network/10.4.57",  # no build number
        "network/10.5.66-rc",  # no build number
        "legacy/v10.4.57-1",  # archived
        "network/10.4-1",  # not three components
        "other/10.4.57-1",  # unknown product
        "network/10.4.57-1-extra",
        "",
    ],
)
def test_anything_else_is_not_a_release(tag):
    assert release.parse(tag) is None


def test_products_do_not_bleed_into_each_other():
    # The old bare `v` prefix was a prefix of nothing in particular, so every
    # parser needed a digit guard to keep unifi-os tags out of the network set.
    tags = [STABLE, UOS]
    assert [r.git_tag for r in release.releases_for(tags, "network")] == [STABLE]
    assert [r.git_tag for r in release.releases_for(tags, "unifi-os")] == [UOS]


def test_archived_tags_are_ignored_without_being_filtered():
    tags = [STABLE, "legacy/v10.4.57", "legacy/v10.4.57-1", "legacy/v10.0.162"]
    assert [r.git_tag for r in release.releases_for(tags, "network")] == [STABLE]


# --- ordering ---


def test_a_later_build_of_the_same_version_wins():
    tags = ["network/10.4.57-1", "network/10.4.57-2"]
    assert release.highest(tags, "network").version == "10.4.57-2"


def test_build_numbers_compare_as_integers():
    # -10 beats -9. String ordering would pick -9 and mint a duplicate -10.
    tags = [f"network/10.4.57-{n}" for n in (1, 2, 9, 10)]
    assert release.highest(tags, "network").revision == 10


def test_a_newer_upstream_beats_more_builds_of_an_older_one():
    tags = ["network/10.4.57-9", "network/10.5.0-1"]
    assert release.highest(tags, "network").upstream == "10.5.0"


def test_a_retired_rc_tag_cannot_win_highest():
    # It would otherwise drag `latest` onto a pre-release.
    tags = ["network/10.5.66-rc-3", "network/10.4.57-1"]
    assert release.highest(tags, "network").upstream == "10.4.57"
    assert release.highest(["network/10.9.9-rc-1"], "network") is None


def test_highest_is_none_when_the_product_has_no_releases():
    assert release.highest([UOS], "network") is None


# --- what comes next ---


def test_a_version_never_tagged_starts_at_build_one():
    assert release.next_release([], "network", "10.4.58").revision == 1


def test_a_version_already_tagged_gets_the_next_build():
    tags = ["network/10.4.57-1", "network/10.4.57-2"]
    assert release.next_release(tags, "network", "10.4.57").revision == 3


def test_builds_of_a_different_version_do_not_advance_the_count():
    tags = ["network/10.4.57-7"]
    assert release.next_release(tags, "network", "10.5.0").revision == 1


def test_the_next_build_never_collides_with_an_existing_tag():
    # Guards the -9/-10 ordering bug in next_release itself, not just in highest().
    tags = [f"network/10.4.57-{n}" for n in range(1, 12)]
    assert release.next_release(tags, "network", "10.4.57").git_tag not in tags


# --- which build is newest ---


def test_a_first_release_is_the_newest_build_of_its_version():
    assert release.is_highest_build([], release.parse(STABLE)) is True


def test_a_rebuild_supersedes_the_build_before_it():
    rel = release.parse("network/10.4.57-2")
    assert release.is_highest_build(["network/10.4.57-1"], rel) is True


def test_an_older_build_is_not_the_newest_one():
    rel = release.parse("network/10.4.57-1")
    assert release.is_highest_build(["network/10.4.57-2"], rel) is False


def test_the_tag_being_released_may_already_be_in_the_tag_list():
    # CI reads `git tag -l` after the tag that triggered it was pushed.
    rel = release.parse("network/10.4.57-2")
    tags = ["network/10.4.57-1", "network/10.4.57-2"]
    assert release.is_highest_build(tags, rel) is True


def test_build_ten_supersedes_build_nine():
    # String ordering would put -10 below -9 and let an older image win.
    ten, nine = release.parse("network/10.4.57-10"), release.parse("network/10.4.57-9")
    assert release.is_highest_build([nine.git_tag], ten) is True
    assert release.is_highest_build([ten.git_tag], nine) is False


def test_builds_of_another_version_do_not_supersede_this_one():
    rel = release.parse("network/10.4.57-1")
    assert release.is_highest_build(["network/10.5.0-7"], rel) is True


def test_another_products_builds_are_invisible():
    assert release.is_highest_build(["unifi-os/10.4.57-9"], release.parse(STABLE)) is True


# --- published tags ---


def test_the_highest_stable_release_carries_all_six_names():
    rel = release.parse(STABLE)
    assert release.sliding_tags(rel, True, True) == [
        "10.4.57",
        "10.4.57-sim",
        "10.4.57-seeded",
        "latest",
        "sim",
        "seeded",
    ]


def test_no_published_tag_carries_a_build_number():
    # Build numbers are a git concept. A `-2` tag would be a weaker promise
    # than the digest that already exists.
    rel = release.parse("network/10.4.57-2")
    assert all("-2" not in name for name in release.sliding_tags(rel, True, True))


def test_a_superseded_upstream_keeps_its_own_names_but_not_the_global_ones():
    # Re-releasing 10.4.57 while 10.5.0 is out must refresh 10.4.57* and leave
    # `latest` where it is.
    rel = release.parse(STABLE)
    tags = ["network/10.5.0-1"]
    assert release.is_highest_stable(tags, rel) is False
    assert release.sliding_tags(rel, True, False) == [
        "10.4.57",
        "10.4.57-sim",
        "10.4.57-seeded",
    ]


def test_an_older_build_publishes_nothing_at_all():
    # Every name slides now, so re-running build 1's workflow after build 2
    # shipped would point 10.4.57-sim back at the older image.
    rel = release.parse("network/10.4.57-1")
    tags = ["network/10.4.57-2"]
    assert release.is_highest_build(tags, rel) is False
    assert release.sliding_tags(rel, False, False) == []
    assert release.sliding_tags(rel, False, True) == []


def test_one_products_release_does_not_move_the_others_pointers():
    rel = release.parse(UOS)
    assert release.is_highest_stable(["network/99.9.9-1"], rel) is True
