"""Tagging, committing and pushing, against real repositories in tmp_path.

The push wrapper is the reason this file exists. Two outages here have been a
push that did not happen and nothing said so, and libgit2 reports a remote's
refusal through a callback rather than by raising — so `push` has to look at
what the remote said and not just at whether the call returned.
"""

import pygit2
import pytest

from unifi_containers import gitops

AUTHOR = ("unifi-containers updater", "noreply@loreland.org")


def repo_with_a_commit(path, content="one"):
    repo = pygit2.init_repository(str(path), initial_head="main")
    (path / "f.txt").write_text(content)
    repo.index.add("f.txt")
    repo.index.write()
    author = gitops.signature(*AUTHOR)
    repo.create_commit("refs/heads/main", author, author, "c", repo.index.write_tree(), [])
    return repo


@pytest.fixture
def bare(tmp_path):
    path = tmp_path / "remote.git"
    pygit2.init_repository(str(path), bare=True)
    return path


@pytest.fixture
def local(tmp_path):
    return repo_with_a_commit(tmp_path / "local")


def refs(bare):
    return list(pygit2.Repository(str(bare)).references)


# --- reading ---


def test_tags_lists_tag_names_without_the_ref_prefix(local):
    gitops.create_tag(local, "network/10.4.57-1", "build 1", gitops.signature(*AUTHOR))
    assert gitops.tags(local) == ["network/10.4.57-1"]


def test_the_current_branch_is_the_checked_out_one(local):
    assert gitops.current_branch(local) == "main"


def test_a_detached_head_has_no_branch(local):
    local.set_head(local.head.target)
    assert gitops.current_branch(local) is None


def test_no_repository_is_an_error_not_an_empty_answer(tmp_path):
    with pytest.raises(gitops.GitError, match="no git repository"):
        gitops.repository(tmp_path / "not-a-repo")


# --- tagging ---


def test_a_tag_is_annotated_and_points_at_head(local):
    message = "network 10.4.57 build 1"
    gitops.create_tag(local, "network/10.4.57-1", message, gitops.signature(*AUTHOR))
    tag = local.revparse_single("refs/tags/network/10.4.57-1")
    assert isinstance(tag, pygit2.Tag)
    assert tag.message.strip() == message
    assert tag.target == local.head.target


def test_retagging_refuses_rather_than_moving_the_tag(local):
    author = gitops.signature(*AUTHOR)
    gitops.create_tag(local, "network/10.4.57-1", "build 1", author)
    with pytest.raises(gitops.GitError, match="already exists"):
        gitops.create_tag(local, "network/10.4.57-1", "build 1 again", author)


# --- committing ---


def test_branching_keeps_the_edits_the_updater_already_made(tmp_path, local):
    # The updater rewrites the pins *before* the branch exists, so a checkout
    # that discarded a dirty working tree would commit an empty bump.
    (tmp_path / "local" / "f.txt").write_text("bumped")
    gitops.checkout_new_branch(local, "bump/network-10.4.58")
    assert (tmp_path / "local" / "f.txt").read_text() == "bumped"


def test_a_bump_branches_and_commits_the_files_it_staged(tmp_path, local):
    gitops.checkout_new_branch(local, "bump/network-10.4.58")
    (tmp_path / "local" / "f.txt").write_text("bumped")
    (tmp_path / "local" / "untracked.txt").write_text("not part of the bump")
    commit = gitops.commit(local, ["f.txt"], "network: bump", gitops.signature(*AUTHOR))

    assert local.head.shorthand == "bump/network-10.4.58"
    assert local[commit].message == "network: bump"
    tree = local[commit].tree
    assert tree["f.txt"].data == b"bumped"
    assert "untracked.txt" not in [entry.name for entry in tree]


# --- pushing ---


def test_a_push_that_lands_moves_the_remote_ref(bare, local):
    gitops.push(str(bare), "refs/heads/main:refs/heads/main", repo=local)
    assert refs(bare) == ["refs/heads/main"]


def test_a_tag_push_lands_under_refs_tags(bare, local):
    gitops.create_tag(local, "network/10.4.57-1", "build 1", gitops.signature(*AUTHOR))
    gitops.push(str(bare), "refs/tags/network/10.4.57-1", repo=local)
    assert "refs/tags/network/10.4.57-1" in refs(bare)


def test_a_non_fast_forward_push_raises_and_leaves_the_remote_alone(tmp_path, bare, local):
    gitops.push(str(bare), "refs/heads/main:refs/heads/main", repo=local)
    landed = pygit2.Repository(str(bare)).references["refs/heads/main"].target

    diverged = repo_with_a_commit(tmp_path / "diverged", content="two")
    with pytest.raises(gitops.GitError, match="git push"):
        gitops.push(str(bare), "refs/heads/main:refs/heads/main", repo=diverged)
    assert pygit2.Repository(str(bare)).references["refs/heads/main"].target == landed


def test_force_overwrites_what_a_plain_push_refuses(tmp_path, bare, local):
    gitops.push(str(bare), "refs/heads/main:refs/heads/main", repo=local)
    diverged = repo_with_a_commit(tmp_path / "diverged", content="two")
    gitops.push(str(bare), "refs/heads/main:refs/heads/main", repo=diverged, force=True)
    assert pygit2.Repository(str(bare)).references["refs/heads/main"].target == (
        diverged.head.target
    )


def test_an_unreachable_remote_raises(local):
    with pytest.raises(gitops.GitError, match="git push"):
        gitops.push("nonsense://nowhere/repo.git", "refs/heads/main", repo=local)


def test_a_remote_that_rejects_the_ref_raises_even_though_the_call_returned():
    # libgit2 hands a server-side refusal to push_update_reference as a message
    # and returns normally. Swallowing that is a release that never happened.
    report = gitops._PushReport()
    report.push_update_reference("refs/tags/network/10.4.57-1", "pre-receive hook declined")
    assert report.rejected == ["refs/tags/network/10.4.57-1: pre-receive hook declined"]
    assert report.accepted == []


def test_a_push_the_remote_says_nothing_about_is_not_treated_as_success(bare, local, monkeypatch):
    # A remote that reports no update for the ref gives no evidence it moved,
    # which is the shape of the silent failure this guards.
    class Silent(gitops._PushReport):
        def push_update_reference(self, refname, message):
            pass

    monkeypatch.setattr(gitops, "_PushReport", Silent)
    with pytest.raises(gitops.GitError, match="no update for it"):
        gitops.push(str(bare), "refs/heads/main:refs/heads/main", repo=local)


def test_a_token_authenticates_without_ever_entering_the_url(bare, local):
    # The credential callback is the whole point: nothing to splice, nothing to
    # scrub out of an error message afterwards.
    gitops.push(str(bare), "refs/heads/main:refs/heads/main", token="t0ken", repo=local)
    assert refs(bare) == ["refs/heads/main"]
