"""One HTTP call, one Response. Stdlib only — this ships inside the images.

An HTTP error status is a *response*, not an exception: a 401 comes back as
`Response(status=401)`, not a raised `HTTPError`. A request that never
completed is `status == 0`, a distinct verdict from a rejection.
"""

import json
import socket
import ssl
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 10

NO_VERDICT = 0


class Response:
    """An HTTP answer, or the absence of one (`status == 0`)."""

    __slots__ = ("status", "body")

    def __init__(self, status, body=b""):
        self.status = status
        self.body = body or b""

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def answered(self):
        """False when the request never completed."""
        return self.status != NO_VERDICT

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        """Parsed body, or {} if it is not JSON."""
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def __repr__(self):
        return f"Response(status={self.status}, {len(self.body)} bytes)"


def _unverified_context():
    # Every target is a self-signed container on loopback, so verification
    # would only ever produce false failures.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def json_request(url, method="GET", payload=None, headers=None, timeout=DEFAULT_TIMEOUT):
    """Never raises for an HTTP status."""
    data = None
    sent_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        sent_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=data, headers=sent_headers, method=method)
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_unverified_context()
        ) as response:
            return Response(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.read())
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError):
        return Response(NO_VERDICT)
