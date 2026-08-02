"""One HTTP call, one Response. Stdlib only — this ships inside the images.

An HTTP error status is a *response*, not an exception: a 401 comes back as
`Response(status=401)`, not a raised `HTTPError`. A request that never
completed is `status == 0`, a distinct verdict from a rejection.

Calls are stateless unless given a `cookie_jar` path, which persists the
session to disk. Docker runs each healthcheck as a *fresh process*, so an
in-memory session would buy nothing: the only way to authenticate once and
reuse it on the next tick is a jar that outlives the process.
"""

import http.cookiejar
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


def _load_jar(path):
    """A cookie jar backed by `path`, empty when there is nothing to load yet."""
    jar = http.cookiejar.MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError):
        # No jar yet, or an unreadable one. The request then goes out
        # unauthenticated and the caller sees the 401 — which is the signal
        # to log in again, not an error to raise here.
        pass
    return jar


def _save_jar(jar):
    if jar is None:
        return
    try:
        # ignore_discard matters: UniFi's session cookie carries no expiry, so
        # a plain save() drops the only cookie worth keeping.
        jar.save(ignore_discard=True, ignore_expires=True)
    except OSError:
        # An unwritable /tmp costs a re-login next tick, not a failed probe.
        pass


def _opener(jar):
    https = urllib.request.HTTPSHandler(context=_unverified_context())
    if jar is None:
        return urllib.request.build_opener(https)
    return urllib.request.build_opener(https, urllib.request.HTTPCookieProcessor(jar))


def json_request(
    url, method="GET", payload=None, headers=None, timeout=DEFAULT_TIMEOUT, cookie_jar=None
):
    """Never raises for an HTTP status.

    `cookie_jar` is a path. Cookies are read from it before the request and
    written back after, so a later process authenticates as this one did.
    """
    data = None
    sent_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        sent_headers.setdefault("Content-Type", "application/json")

    jar = _load_jar(cookie_jar) if cookie_jar else None
    request = urllib.request.Request(url, data=data, headers=sent_headers, method=method)
    try:
        with _opener(jar).open(request, timeout=timeout) as response:
            return Response(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.read())
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError):
        return Response(NO_VERDICT)
    finally:
        _save_jar(jar)
