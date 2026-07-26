#!/usr/bin/env python3
"""Trigger the Forgejo -> GitHub push-mirror sync.

The mirror is manual-sync so local iteration stays local; a release has to ask
for the sync explicitly. Reads FORGEJO_TOKEN, CI_FORGE_URL and CI_REPO from
the environment.

Deliberately a script rather than inline pipeline commands: Woodpecker
substitutes ${...} in a step's commands before the shell runs, so an inline
`-H "Authorization: token ${FORGEJO_TOKEN}"` ships an EMPTY token and the API
answers 404. Reading the value from the environment here sidesteps that, and
the missing-value check below names the problem instead of leaving a bare 404.

stdlib only.
"""
import os
import sys
import urllib.error
import urllib.request

REQUIRED = ("FORGEJO_TOKEN", "CI_FORGE_URL", "CI_REPO")


def missing_env(env):
    """Return the required variables that are absent or empty. Pure."""
    return [name for name in REQUIRED if not env.get(name)]


def sync_url(forge_url, repo):
    """Build the push-mirror sync endpoint. Pure."""
    return f"{forge_url.rstrip('/')}/api/v1/repos/{repo}/push_mirrors-sync"


def main(env=None):
    env = os.environ if env is None else env
    absent = missing_env(env)
    if absent:
        print(f"missing or empty: {', '.join(absent)} — a secret that does not "
              f"reach the step yields an empty token and a 404",
              file=sys.stderr)
        return 1

    url = sync_url(env["CI_FORGE_URL"], env["CI_REPO"])
    request = urllib.request.Request(
        url, method="POST",
        headers={"Authorization": f"token {env['FORGEJO_TOKEN']}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(f"mirror sync triggered ({response.status})")
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", "replace")
        print(f"mirror sync failed: HTTP {exc.code} {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"mirror sync failed: {exc.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
