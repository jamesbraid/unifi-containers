"""Shared fixtures.

Client tests use `pytest-httpserver`'s `httpserver` against a real loopback
socket rather than a mock. Two of its properties they rely on: header matching
is case-insensitive, so an assertion cannot end up testing urllib's
capitalization of `X-API-KEY`; and an unregistered path is answered 500 and
raised by `httpserver.check()`, which turns "the code called something nobody
expected" into a failing test.
"""

import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def dead_url():
    """A URL nothing is listening on, for the no-verdict path."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"
