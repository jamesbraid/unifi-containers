"""Tests for scripts/rebuild-tag.py (imported via importlib — the filename
has dashes)."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "rebuild_tag", Path(__file__).resolve().parents[1] / "rebuild-tag.py"
)
rebuild = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rebuild)

UOS = "unifi-os-v"
NET = "v"


def test_next_revision_follows_the_highest_tag():
    tags = ["unifi-os-v5.1.21", "unifi-os-v5.1.21-1"]
    assert rebuild.next_rebuild_tag(tags, UOS) == ("unifi-os-v5.1.21-2",
                                                   "5.1.21", 2)


def test_revisions_compare_numerically_not_lexically():
    # -10 beats -9; string ordering would pick -9 and mint a duplicate -10.
    tags = [f"unifi-os-v5.1.21-{n}" for n in (1, 2, 9, 10)]
    assert rebuild.next_rebuild_tag(tags, UOS)[0] == "unifi-os-v5.1.21-11"


def test_versions_compare_numerically_too():
    tags = ["unifi-os-v5.1.9-3", "unifi-os-v5.1.21-1"]
    assert rebuild.next_rebuild_tag(tags, UOS) == ("unifi-os-v5.1.21-2",
                                                   "5.1.21", 2)


def test_rc_tags_never_win():
    # RCs are ephemeral and carry no revision; rebuilding one is meaningless.
    tags = ["v10.4.57-1", "v10.5.67-rc"]
    assert rebuild.next_rebuild_tag(tags, NET) == ("v10.4.57-2", "10.4.57", 2)


def test_other_products_tags_are_ignored():
    # The network prefix is a bare "v", so unifi-os tags must not match it.
    tags = ["unifi-os-v5.1.21-1", "v10.4.57-1"]
    assert rebuild.next_rebuild_tag(tags, NET)[0] == "v10.4.57-2"
    assert rebuild.next_rebuild_tag(tags, UOS)[0] == "unifi-os-v5.1.21-2"


def test_pre_revision_tags_count_as_revision_one():
    assert rebuild.next_rebuild_tag(["unifi-os-v5.1.21"], UOS)[2] == 2


@pytest.mark.parametrize("tags", [
    [],
    ["v10.5.67-rc"],
    ["not-a-tag", "unifi-os-vNOPE"],
])
def test_no_release_tag_is_an_error(tags):
    with pytest.raises(RuntimeError, match="nothing to rebuild"):
        rebuild.next_rebuild_tag(tags, UOS)


def test_parse_skips_junk_but_keeps_releases():
    parsed = rebuild.parse_release_tags(
        ["unifi-os-v5.1.21-1", "unifi-os-v5.1.21-rc", "unifi-os-vX", "other"],
        UOS)
    assert [t for _, _, t in parsed] == ["unifi-os-v5.1.21-1"]
