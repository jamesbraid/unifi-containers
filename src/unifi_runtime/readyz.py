"""Publish readiness over HTTP, for consumers that cannot ask Docker.

    python3 -m unifi_runtime.readyz

A harness handed only a URL — a CI service container, a Kubernetes
readinessProbe, a curl loop — cannot read this container's HEALTHCHECK. Docker
holds that verdict and will not serve it over the network, so a URL-mode caller
is left reimplementing readiness against an API it does not own. That is how
consumers end up hand-rolling a login poll that proves less than the image
already knows.

So serve the same verdict on a port of its own: 200 once ready, 503 until then.

This *proves* readiness rather than reporting it. Reporting would mean trusting
something else to run the healthcheck on our behalf, and an orchestrator that
starts a service container without health-gating it (Woodpecker does exactly
that) would leave the endpoint answering 503 for the container's whole life.
Running the same marker-gated probe means whoever asks first proves it and
everyone after reads the marker.
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .env import is_enabled
from .healthcheck import PROBES

PATH = "/readyz"

#: Clear of every port the images already publish: 6789/8080/8443/8880/8843 on
#: the Network app, 443/8080/7443 on UOS, and UOS's internal 9080 identity API.
DEFAULT_PORT = 9099

#: Never probe more often than this. A caller polling every 100ms must not
#: become a login every 100ms — the rate limiter counts them all.
MIN_INTERVAL = 2.0

PROBE_VAR = "READYZ_PROBE"
PORT_VAR = "READYZ_PORT"
DISABLE_VAR = "READYZ_DISABLE"

READY = (200, b"ready\n")
NOT_READY = (503, b"not ready\n")
NOT_FOUND = (404, b"not found\n")
PROBE_ERROR = (500, b"probe failed\n")


class Gate:
    """Runs `probe` one at a time, and no more often than `interval`.

    Concurrency is the whole point. The probe can take seconds and its first
    stage is a login, so N overlapping requests must not become N logins. A
    request arriving while a probe is in flight, or sooner than `interval` after
    the last one, is answered from the verdict already in hand.

    The verdict is cached, not latched. A marker-gated probe carries its own
    latch and stays true from a file test; a live probe like `network_answering`
    keeps reflecting the controller, which is what a readiness endpoint owes a
    caller that may be watching for longer than one boot.
    """

    def __init__(self, probe, interval=MIN_INTERVAL, clock=time.monotonic):
        self._probe = probe
        self._interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        self._verdict = False
        self._last = None

    def ready(self):
        if not self._lock.acquire(blocking=False):
            return self._verdict
        try:
            now = self._clock()
            if self._last is not None and now - self._last < self._interval:
                return self._verdict
            self._last = now
            self._verdict = bool(self._probe())
            return self._verdict
        finally:
            self._lock.release()


def handler_for(gate):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 semantics: answer and close. A poller reconnects anyway, and
        # keep-alive would hold a thread per caller for nothing.
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path != PATH:
                status, body = NOT_FOUND
            else:
                try:
                    status, body = READY if gate.ready() else NOT_READY
                except Exception:  # noqa: BLE001 — a broken probe is still an answer
                    # 500 reads as "endpoint up, verdict unknown" and a poller
                    # retries it. Letting the exception escape drops the
                    # connection, which reads as "wrong host or port" instead.
                    status, body = PROBE_ERROR
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 — the base class names it this
            """Silence. A readiness poller is a request a second for the life of
            the container, and `docker logs` is where an operator looks for the
            controller, not for us."""

    return Handler


def server(probe, port=DEFAULT_PORT, host=""):
    """A bound server. The caller decides which thread runs it."""
    return ThreadingHTTPServer((host, port), handler_for(Gate(probe)))


def start(probe, port=DEFAULT_PORT, host=""):
    """Serve on a daemon thread, for an entrypoint that stays PID 1."""
    running = server(probe, port, host)
    threading.Thread(target=running.serve_forever, name="readyz", daemon=True).start()
    return running


def configured(env=None):
    """(probe, port) for this image, or None when it serves no endpoint.

    The probe name is explicit rather than inferred. The HEALTHCHECK already
    names one per variant and nothing inside the container can read that line,
    so a variant Dockerfile states it twice on purpose.

    An unknown name raises instead of disabling quietly: a typo that silently
    served nothing would hang every caller waiting on the endpoint, which is a
    worse failure than refusing to start.
    """
    env = os.environ if env is None else env
    if is_enabled(env.get(DISABLE_VAR)):
        return None
    name = env.get(PROBE_VAR)
    if not name:
        return None
    if name not in PROBES:
        raise ValueError(f"unknown {PROBE_VAR}: {name!r}")
    try:
        port = int(env.get(PORT_VAR) or DEFAULT_PORT)
    except ValueError:
        raise ValueError(f"{PORT_VAR} is not a number: {env.get(PORT_VAR)!r}") from None
    return PROBES[name], port


def start_configured(env=None):
    """Start the endpoint if this image asks for one. Returns the server, or None."""
    found = configured(env)
    if found is None:
        return None
    probe, port = found
    return start(probe, port)


def main(argv=None, env=None):
    found = configured(env)
    if found is None:
        print(f"readyz: no {PROBE_VAR} set; nothing to serve", file=sys.stderr)
        return 0
    probe, port = found
    print(f"readyz: serving {PATH} on :{port}", file=sys.stderr)
    server(probe, port).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
