"""The handful of Forgejo API calls the CI lanes make.

`pyforgejo` raises on any non-2xx, so each method here decides which refusals are
answers and which are failures the lane cannot continue past.
"""

import os

from pyforgejo import PyforgejoApi
from pyforgejo.core.api_error import ApiError

TIMEOUT = 30


class ForgeError(RuntimeError):
    """The forge refused a call the lane cannot continue without."""


def env(name, environ=None):
    """A required environment variable, or a clear failure."""
    value = (environ if environ is not None else os.environ).get(name)
    if not value:
        raise ForgeError(f"{name} is not set")
    return value


class Forge:
    def __init__(self, forge_url, repo, token, timeout=TIMEOUT, httpx_client=None):
        self.api = forge_url.rstrip("/") + "/api/v1"
        self.repo = repo
        self.owner, _, self.name = repo.partition("/")
        if not self.owner or not self.name:
            raise ForgeError(f"expected an owner/name repository, got {repo!r}")
        self.client = PyforgejoApi(
            base_url=self.api, api_key=token, timeout=timeout, httpx_client=httpx_client
        )

    @classmethod
    def from_env(cls, environ=None):
        return cls(
            env("CI_FORGE_URL", environ), env("CI_REPO", environ), env("FORGEJO_TOKEN", environ)
        )

    @property
    def _repos(self):
        return self.client.repository

    def raw_file(self, ref, path):
        """A file's contents on a branch, or None if it is not there."""
        try:
            blob = b"".join(self._repos.repo_get_raw_file(self.owner, self.name, path, ref=ref))
        except ApiError:
            return None
        return blob.decode("utf-8", "replace")

    def delete_branch(self, name):
        """Delete a branch. False when it was not there to delete."""
        try:
            self._repos.repo_delete_branch(self.owner, self.name, name)
        except ApiError:
            return False
        return True

    def create_branch(self, name, from_ref):
        try:
            self._repos.repo_create_branch(
                self.owner, self.name, new_branch_name=name, old_ref_name=from_ref
            )
        except ApiError as exc:
            raise ForgeError(f"creating branch {name} from {from_ref} -> {exc}") from exc

    def create_pull(self, base, head, title, body):
        """Open a PR, returning its number. None when the forge refused."""
        try:
            pull = self._repos.repo_create_pull_request(
                self.owner, self.name, base=base, head=head, title=title, body=body
            )
        except ApiError:
            return None
        return pull.number if isinstance(pull.number, int) else None

    def find_pull(self, head):
        """The number of the open PR from `head`, or None."""
        try:
            pulls = self._repos.repo_list_pull_requests(self.owner, self.name, state="open")
        except ApiError:
            return None
        for pull in pulls:
            if pull.head is not None and pull.head.ref == head:
                return pull.number
        return None

    def merge_when_green(self, number, method="rebase"):
        """Queue an auto-merge once checks pass."""
        try:
            self._repos.repo_merge_pull_request(
                self.owner,
                self.name,
                number,
                do=method,
                merge_when_checks_succeed=True,
                delete_branch_after_merge=True,
            )
        except ApiError as exc:
            raise ForgeError(f"scheduling auto-merge of #{number} -> {exc}") from exc
