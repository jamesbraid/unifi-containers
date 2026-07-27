"""The Forgejo endpoint shapes, pinned.

A typo in one of these paths is a 404 at release time, which is exactly the
failure mode this repo has already hit twice. `pyforgejo` owns the paths now, so
what these assert is that the client is pointed at the right API root, sends the
token the way Forgejo wants it, and reaches the endpoint each lane needs.

The transport is a fake, not a socket: it records the request every call makes
so the URL and the body can be asserted exactly.
"""

import httpx
import pytest

from unifi_containers.forge import Forge, ForgeError

BASE = "https://git.example.dev"


class Recorder:
    """An httpx transport that answers from a table and logs what it was asked."""

    def __init__(self, replies=None):
        self.requests = []
        self.replies = replies or {}

    def transport(self):
        return httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        for (method, suffix), reply in self.replies.items():
            if request.method == method and request.url.path.endswith(suffix):
                return reply() if callable(reply) else reply
        return httpx.Response(404, json={"message": "not found"})

    @property
    def last(self):
        return self.requests[-1]

    def path_and_query(self):
        url = self.last.url
        return str(url.path) + (f"?{url.query.decode()}" if url.query else "")


def forge(recorder, repo="o/r", url=BASE):
    return Forge(url, repo, "t0ken", httpx_client=httpx.Client(transport=recorder.transport()))


@pytest.mark.parametrize("url", [BASE, BASE + "/"], ids=["bare", "trailing-slash"])
def test_the_api_root_is_v1_however_the_forge_url_is_spelled(url):
    assert forge(Recorder(), url=url).api == "https://git.example.dev/api/v1"


def test_a_repository_without_an_owner_is_refused():
    # `CI_REPO` is owner/name; anything else would build a URL with a hole in it.
    with pytest.raises(ForgeError, match="owner/name"):
        forge(Recorder(), repo="unifi-containers")


def test_the_token_is_sent_as_a_forgejo_token_header():
    rec = Recorder({("GET", "/pulls"): httpx.Response(200, json=[])})
    forge(rec).find_pull("some-branch")
    assert rec.last.headers["authorization"] == "token t0ken"


def test_find_pull_asks_only_for_open_pulls():
    rec = Recorder({("GET", "/pulls"): httpx.Response(200, json=[])})
    forge(rec).find_pull("bump/network-10.4.57")
    assert rec.path_and_query() == "/api/v1/repos/o/r/pulls?state=open"


def test_find_pull_matches_on_the_head_branch():
    rec = Recorder(
        {
            ("GET", "/pulls"): httpx.Response(
                200,
                json=[
                    {"number": 3, "head": {"ref": "bump/other"}},
                    {"number": 7, "head": {"ref": "bump/mine"}},
                ],
            )
        }
    )
    assert forge(rec).find_pull("bump/mine") == 7
    assert forge(rec).find_pull("bump/nothing") is None


def test_create_pull_posts_the_body_forgejo_expects():
    rec = Recorder({("POST", "/pulls"): httpx.Response(201, json={"number": 12})})
    assert forge(rec).create_pull("main", "bump/x", "title", "body") == 12
    assert rec.path_and_query() == "/api/v1/repos/o/r/pulls"
    assert b'"base":"main"' in rec.last.content.replace(b" ", b"")


def test_a_refused_create_pull_is_an_answer_not_a_failure():
    # A 409 means one is already open, which `find_pull` then picks up.
    rec = Recorder({("POST", "/pulls"): httpx.Response(409, json={"message": "already exists"})})
    assert forge(rec).create_pull("main", "bump/x", "t", "b") is None


def test_raw_file_reads_a_path_on_a_branch():
    body = b"ARG PKGURL=https://dl.ui.com/unifi/10.4.57/unifi_sysvinit_all.deb\n"
    rec = Recorder({("GET", "/network/Dockerfile"): httpx.Response(200, content=body)})
    assert forge(rec).raw_file("rc", "network/Dockerfile") == body.decode()
    assert rec.path_and_query() == "/api/v1/repos/o/r/raw/network/Dockerfile?ref=rc"


def test_a_missing_raw_file_is_none_rather_than_an_error():
    # The rc lane reads a branch that may not exist yet.
    assert forge(Recorder()).raw_file("rc", "network/Dockerfile") is None


def test_deleting_a_branch_that_is_not_there_is_false_not_fatal():
    assert forge(Recorder()).delete_branch("rc") is False


def test_deleting_a_branch_targets_it_by_name():
    rec = Recorder({("DELETE", "/branches/rc"): httpx.Response(204)})
    assert forge(rec).delete_branch("rc") is True
    assert rec.path_and_query() == "/api/v1/repos/o/r/branches/rc"


def test_creating_a_branch_names_the_ref_it_forks_from():
    rec = Recorder({("POST", "/branches"): httpx.Response(201, json={"name": "rc"})})
    forge(rec).create_branch("rc", "main")
    assert rec.path_and_query() == "/api/v1/repos/o/r/branches"
    body = rec.last.content.replace(b" ", b"")
    assert b'"new_branch_name":"rc"' in body
    assert b'"old_ref_name":"main"' in body


def test_a_refused_branch_creation_stops_the_lane():
    with pytest.raises(ForgeError, match="creating branch rc from main"):
        forge(Recorder()).create_branch("rc", "main")


def test_merge_when_green_queues_the_auto_merge_and_the_branch_deletion():
    rec = Recorder({("POST", "/merge"): httpx.Response(200)})
    forge(rec).merge_when_green(9)
    assert rec.path_and_query() == "/api/v1/repos/o/r/pulls/9/merge"
    body = rec.last.content.replace(b" ", b"")
    # Forgejo spells the strategy field with a capital D.
    assert b'"Do":"rebase"' in body
    assert b'"merge_when_checks_succeed":true' in body
    assert b'"delete_branch_after_merge":true' in body


def test_a_refused_merge_is_a_failure_the_lane_cannot_ignore():
    with pytest.raises(ForgeError, match="scheduling auto-merge of #9"):
        forge(Recorder()).merge_when_green(9)
