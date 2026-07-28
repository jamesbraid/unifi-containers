"""git plumbing for the release and update lanes, on libgit2.

Credentials arrive through a callback, so no token is ever spliced into a URL.
"""

import os
from collections import namedtuple

import pygit2

#: Who the release and update lanes commit and tag as. A CI container has no
#: identity of its own, and both lanes must use the same one.
IDENTITY = ("unifi-containers updater", "noreply@loreland.org")

PushTarget = namedtuple("PushTarget", "url token")


class GitError(RuntimeError):
    """A git operation failed."""


def trust_workdir():
    """Accept a checkout owned by another uid, which a CI container's usually is."""
    pygit2.settings.owner_validation = False


def push_target(env=None):
    """Where to push; in CI the clone URL carries no auth, so a token comes with it.

    Outside Woodpecker, set both FORGEJO_TOKEN and CI_REPO_CLONE_URL to an
    https:// URL — that is the only combination that authenticates, because the
    credential is a username/password pair. The GIT_REMOTE fallback resolves a
    configured remote's URL but supplies no credentials, so it reaches a public
    remote and nothing else; against an ssh:// remote libgit2 wants a key, which
    nothing here offers.
    """
    environ = os.environ if env is None else env
    token = environ.get("FORGEJO_TOKEN")
    clone_url = environ.get("CI_REPO_CLONE_URL")
    if token and clone_url:
        return PushTarget(clone_url, token)
    return PushTarget(environ.get("GIT_REMOTE", "origin"), None)


def repository(path="."):
    """The repository containing `path`."""
    found = pygit2.discover_repository(str(path))
    if found is None:
        raise GitError(f"no git repository at {path}")
    return pygit2.Repository(found)


def tags(repo=None):
    """Every tag name in the repository. Needs full history."""
    prefix = "refs/tags/"
    references = (repo or repository()).references
    return [name[len(prefix) :] for name in references if name.startswith(prefix)]


def signature(name, email):
    """The committer/tagger identity. A CI container has none of its own."""
    return pygit2.Signature(name, email)


def create_tag(repo, name, message, author):
    """Annotate HEAD with `name`. Raises rather than moving an existing tag."""
    if f"refs/tags/{name}" in repo.references:
        raise GitError(f"tag {name} already exists")
    repo.create_tag(name, repo.head.target, pygit2.enums.ObjectType.COMMIT, author, message)


def checkout_new_branch(repo, name):
    """Create `name` at HEAD and switch to it, replacing any branch of that name."""
    branch = repo.branches.local.create(name, repo[repo.head.target], True)
    repo.checkout(branch)
    return branch


def commit(repo, paths, message, author):
    """Stage `paths` and commit them on the current branch, returning the commit id."""
    index = repo.index
    for path in paths:
        index.add(path)
    index.write()
    return repo.create_commit(
        repo.head.name, author, author, message, index.write_tree(), [repo.head.target]
    )


class _PushReport(pygit2.RemoteCallbacks):
    """Carries the credentials up and what the remote said about each ref back."""

    def __init__(self, credentials=None):
        super().__init__(credentials=credentials)
        self.accepted = []
        self.rejected = []

    def push_update_reference(self, refname, message):
        if message is None:
            self.accepted.append(refname)
        else:
            self.rejected.append(f"{refname}: {message}")


def _anonymous_remote(repo, url):
    """A remote for `url`, which may be a configured remote's name instead.

    libgit2 only takes URLs, so `push_target`'s local fallback of `origin` has to
    be resolved here rather than handed straight to it.
    """
    try:
        url = repo.remotes[url].url
    except (KeyError, ValueError):
        # Not a configured remote. ValueError covers anything libgit2 will not
        # even consider a remote *name*, such as a path or a URL.
        pass
    return repo.remotes.create_anonymous(url)


def reset_to_remote_branch(url, branch, token=None, repo=None):
    """Put the worktree on `branch` exactly as the remote has it, discarding local work.

    The updater lanes run as consecutive steps in one shared workspace, so
    without this each lane starts from whatever the previous lane committed and
    its PR carries the other lane's bump.
    """
    repo = repo or repository()
    remote = _anonymous_remote(repo, url)
    callbacks = pygit2.RemoteCallbacks(pygit2.UserPass("oauth2", token) if token else None)
    try:
        remote.fetch([f"+refs/heads/{branch}:refs/remotes/lane/{branch}"], callbacks=callbacks)
        commit = repo.revparse_single(f"refs/remotes/lane/{branch}")
        repo.reset(commit.id, pygit2.GIT_RESET_HARD)
        repo.set_head(commit.id)
    except (pygit2.GitError, KeyError) as exc:
        raise GitError(f"could not reset to {branch} -> {exc}") from exc
    return str(commit.id)


def fetch_tags(url, token=None, repo=None):
    """Fetch every tag from `url` into this repository.

    In-process rather than `git fetch --tags`: the CI image carries no git, and
    the shell form failed silently there, so the release cutter saw no tags at
    all and computed build 1 for a version that was already released.
    """
    repo = repo or repository()
    remote = _anonymous_remote(repo, url)
    callbacks = pygit2.RemoteCallbacks(pygit2.UserPass("oauth2", token) if token else None)
    try:
        remote.fetch(
            ["+refs/tags/*:refs/tags/*"], callbacks=callbacks, prune=pygit2.GIT_FETCH_NO_PRUNE
        )
    except pygit2.GitError as exc:
        raise GitError(f"git fetch --tags {url} -> {exc}") from exc


def push(url, refspec, token=None, force=False, repo=None):
    """Push one refspec, raising unless the remote confirms it took the update."""
    repo = repo or repository()
    report = _PushReport(pygit2.UserPass("oauth2", token) if token else None)
    remote = _anonymous_remote(repo, url)
    try:
        remote.push([f"+{refspec}" if force else refspec], callbacks=report)
    except pygit2.GitError as exc:
        raise GitError(f"git push {refspec} -> {exc}") from exc
    if report.rejected:
        raise GitError(f"git push {refspec} rejected -> {'; '.join(report.rejected)}")
    if not report.accepted:
        raise GitError(
            f"git push {refspec} -> the remote reported no update for it, so "
            f"there is no evidence the ref moved"
        )
