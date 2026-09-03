"""The release entry points: plan, cut, slide."""

import io

import pytest

from unifi_containers import cut_release as cut
from unifi_containers import gitops
from unifi_containers import release_plan as plan_script
from unifi_containers import slide_tags as slide

PINS = {"network": "10.4.57", "unifi-os": "5.1.21"}


def pinned(product):
    return PINS[product]


# --- release-plan ---


def emitted(tag, tags):
    out = io.StringIO()
    plan_script.emit(plan_script.plan(tag, tags, pinned_lookup=pinned), out)
    return dict(line.split("=", 1) for line in out.getvalue().strip().splitlines())


def test_plan_describes_a_stable_release():
    result = plan_script.plan("network/10.4.57-1", [], pinned_lookup=pinned)
    assert result.release.version == "10.4.57-1"
    assert (result.pinned, result.newest_build, result.top) == ("10.4.57", True, True)


def test_plan_refuses_a_tag_that_disagrees_with_the_pin():
    # The guard that stops a mistyped tag publishing an image built from a
    # different upstream version than the tag claims.
    with pytest.raises(ValueError, match="pins 10.4.57"):
        plan_script.plan("network/10.9.9-1", [], pinned_lookup=pinned)


def test_plan_refuses_a_legacy_tag_with_the_expected_format():
    with pytest.raises(ValueError, match="not a release tag"):
        plan_script.plan("v10.4.57-1", [], pinned_lookup=pinned)


def test_plan_emits_the_values_the_workflows_read():
    assert emitted("network/10.4.57-1", []) == {
        "publish": "true",
        "image": "ghcr.io/jamesbraid/unifi-network",
        "push_base": "10.4.57",
        "push_sim": "10.4.57-sim",
        "push_seeded": "10.4.57-seeded",
        "globals": "latest sim seeded",
        "build": "10.4.57-1",
    }


def test_no_published_name_carries_a_build_number():
    # `build` is reporting only. Every push target and every global name must be
    # free of the build suffix, or a registry tag becomes immutable-by-accident
    # and the sliding scheme stops meaning anything.
    values = emitted("network/10.4.57-7", [])
    published = [
        values["push_base"],
        values["push_sim"],
        values["push_seeded"],
        *values["globals"].split(),
    ]
    assert values["build"] == "10.4.57-7"
    assert all(not name.endswith("-7") for name in published), published


def test_a_rebuild_of_the_current_version_publishes_every_name():
    # The plan's worked example: build 2 with build 1 already tagged.
    values = emitted("network/10.4.57-2", ["network/10.4.57-1"])
    assert values["publish"] == "true"
    assert values["push_base"] == "10.4.57"
    assert values["globals"] == "latest sim seeded"


def test_rerunning_an_older_builds_workflow_publishes_nothing():
    # The guard: every name slides, so build 1 must not point 10.4.57-sim back
    # at its own older image once build 2 has shipped.
    values = emitted("network/10.4.57-1", ["network/10.4.57-1", "network/10.4.57-2"])
    assert values["publish"] == "false"
    # Nothing to push and nothing to point: an older build must not drag a name
    # back onto its own older image.
    assert [values[k] for k in ("push_base", "push_sim", "push_seeded", "globals")] == [
        "",
        "",
        "",
        "",
    ]


# --- cut-release ---


def test_a_pinned_version_with_no_tag_is_due():
    rel, reason = cut.decide("network", "10.4.57", [])
    assert rel.git_tag == "network/10.4.57-1"
    assert "no release tag yet" in reason


def test_an_already_released_version_is_not_due_again():
    # Idempotence is what lets this run on every push: the second run must cut
    # nothing rather than pile up build numbers.
    rel, reason = cut.decide("network", "10.4.57", ["network/10.4.57-1"])
    assert rel is None
    assert "already released" in reason


def test_a_bump_is_due_even_though_the_previous_version_has_builds():
    rel, _ = cut.decide("network", "10.4.58", ["network/10.4.57-3"])
    assert rel.git_tag == "network/10.4.58-1"


def test_an_unpinned_product_is_never_due():
    rel, reason = cut.decide("network", None, [])
    assert rel is None
    assert "no pinned version" in reason


def test_a_rebuild_advances_the_build_number():
    rel, reason = cut.decide_rebuild("network", "10.4.57", ["network/10.4.57-1"])
    assert rel.git_tag == "network/10.4.57-2"
    assert "rebuilding" in reason


def test_a_rebuild_of_something_never_released_is_refused():
    rel, reason = cut.decide_rebuild("network", "10.4.57", [])
    assert rel is None
    assert "never been released" in reason


def test_the_push_target_uses_ci_credentials_when_present():
    target = gitops.push_target(
        {
            "FORGEJO_TOKEN": "t0ken",
            "CI_REPO_CLONE_URL": "https://git.example.dev/infra/repo.git",
        }
    )
    # The token travels beside the URL, not inside it: libgit2 asks for
    # credentials through a callback, so there is nothing to redact from a log.
    assert target.token == "t0ken"
    assert target.url == "https://git.example.dev/infra/repo.git"


def test_the_push_target_falls_back_to_the_configured_remote():
    target = gitops.push_target({})
    assert (target.url, target.token) == ("origin", None)


# --- slide-tags ---


class Recorder:
    def __init__(self):
        self.calls = []
        self.lines = []

    def create(self, image, tag, digest):
        self.calls.append((image, tag, digest))

    def out(self, line):
        self.lines.append(line)


@pytest.mark.parametrize(
    "names",
    [["latest", "sim", "seeded"], ["10.4.57", "10.4.57-sim", "10.4.57-seeded"]],
    ids=["global", "version-level"],
)
def test_each_sliding_name_tracks_its_own_variant(names):
    rec = Recorder()
    digests = ["sha256:a", "sha256:b", "sha256:c"]
    moved = slide.slide(
        "img", names, dict(zip(("base", "sim", "seeded"), digests)), create=rec.create, out=rec.out
    )
    assert moved == names
    assert rec.calls == [("img", name, d) for name, d in zip(names, digests)]


def test_a_requested_name_with_no_digest_fails_the_run():
    # Pointing `seeded` at the base digest would publish an image that is not
    # seeded, so it must not be substituted. Skipping it is not enough either:
    # release_plan only asks for variants it built, so a missing digest is a lost
    # workflow output, and a silent skip leaves :seeded on the previous image
    # while the run goes green.
    rec = Recorder()
    with pytest.raises(ValueError, match="no seeded digest"):
        slide.slide(
            "img", ["latest", "seeded"], {"base": "sha256:a"}, create=rec.create, out=rec.out
        )
    assert ("img", "seeded", "sha256:a") not in rec.calls


def test_a_missing_digest_is_a_non_zero_exit_not_a_partial_success():
    # `seeded` first, so this refuses before reaching the real imagetools call:
    # the exit code is the assertion, not whether a docker binary is installed.
    assert slide.apply("img", "seeded latest", base="sha256:a") == 1


@pytest.mark.parametrize(
    "name",
    [
        "newest",
        "10.4.57-2",  # a build number, which never reaches a registry
        "10.4.57-2-sim",
        "10.4-sim",
        "sim-10.4.57",
    ],
)
def test_an_unknown_sliding_tag_is_an_error(name):
    with pytest.raises(ValueError, match="unknown sliding tag"):
        slide.slide("img", [name], {"base": "sha256:a"}, create=lambda *a: None, out=lambda _: None)


def test_the_plan_refuses_a_retired_rc_tag():
    # RC support is gone, so `-rc` no longer parses as a release at all. A tag
    # left over from that era must be refused rather than read as build 1.
    with pytest.raises(ValueError, match="not a release tag"):
        plan_script.plan("network/10.5.67-rc-1", [], pinned_lookup=pinned)


def test_changed_pins_cut_the_next_build_without_a_rebuild_flag():
    # The bundled app moved under an unchanged UOS version: the pins differ
    # from what the newest release tag was cut from, so the next build is due.
    rel, reason = cut.decide(
        "unifi-os", "5.1.37", ["unifi-os/5.1.37-3"], pins_changed_since=lambda tag: True
    )
    assert rel.git_tag == "unifi-os/5.1.37-4"
    assert "changed since unifi-os/5.1.37-3" in reason


def test_unchanged_pins_still_require_a_deliberate_rebuild():
    rel, reason = cut.decide(
        "unifi-os", "5.1.37", ["unifi-os/5.1.37-3"], pins_changed_since=lambda tag: False
    )
    assert rel is None
    assert "--rebuild" in reason


def test_pin_file_is_the_env_file_for_uos_and_nothing_for_network():
    from unifi_containers import pins

    assert pins.pin_file("unifi-os") == "unifi-os/pins.env"
    assert pins.pin_file("network") is None
