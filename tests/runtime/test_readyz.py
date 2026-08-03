"""The readiness endpoint.

The rate limiting is the part worth testing. This endpoint exists to be polled
— that is its whole job — and its probe's first stage is a login that UniFi
rate-limits globally. An endpoint that ran the probe per request would turn a
caller's one-second poll into a login a second and lock the controller out.
"""

import threading
import urllib.error
import urllib.request

import pytest

from unifi_runtime import readyz


class Clock:
    """A hand-wound monotonic clock, so the interval is tested without sleeping."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# --- the gate ---------------------------------------------------------


def test_the_probe_runs_at_most_once_per_interval():
    calls = []
    clock = Clock()
    gate = readyz.Gate(lambda: calls.append(1) or False, interval=2.0, clock=clock)

    for _ in range(20):
        gate.ready()
    assert len(calls) == 1, "a burst of callers must not become a burst of logins"

    clock.now += 1.9
    gate.ready()
    assert len(calls) == 1, "still inside the interval"

    clock.now += 0.2
    gate.ready()
    assert len(calls) == 2


def test_a_caller_arriving_mid_probe_is_answered_not_queued():
    """The lock is held, so the second caller must get the known verdict now.

    Queueing would serialize callers behind a probe that can take seconds, and
    a poller with a short timeout would see a hang rather than a 503.
    """
    running = threading.Event()
    release = threading.Event()

    def slow():
        running.set()
        release.wait(5)
        return True

    gate = readyz.Gate(slow, interval=0)
    worker = threading.Thread(target=gate.ready, daemon=True)
    worker.start()
    assert running.wait(5)

    assert gate.ready() is False  # answered from what we know, without blocking
    release.set()
    worker.join(5)
    assert gate.ready() is True


def test_the_verdict_is_cached_not_latched():
    """A live probe keeps reflecting the controller.

    The marker-gated probes carry their own latch; this one must not add a
    second, or an endpoint would keep claiming ready for a controller that has
    since gone.
    """
    verdict = {"value": True}
    clock = Clock()
    gate = readyz.Gate(lambda: verdict["value"], interval=2.0, clock=clock)

    assert gate.ready() is True
    verdict["value"] = False
    assert gate.ready() is True, "cached until the interval is up"
    clock.now += 3
    assert gate.ready() is False


# --- the endpoint -----------------------------------------------------


@pytest.fixture
def serving():
    servers = []

    def start(probe):
        server = readyz.start(probe, port=0)
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()


def get(port, path=readyz.PATH):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_serves_503_until_the_probe_passes(serving):
    assert get(serving(lambda: False))[0] == 503


def test_serves_200_once_the_probe_passes(serving):
    status, body = get(serving(lambda: True))
    assert status == 200
    assert body == b"ready\n"


def test_a_query_string_still_reaches_the_endpoint(serving):
    assert get(serving(lambda: True), readyz.PATH + "?from=probe")[0] == 200


def test_any_other_path_is_a_404(serving):
    assert get(serving(lambda: True), "/")[0] == 404
    assert get(serving(lambda: True), "/healthz")[0] == 404


def test_a_probe_that_raises_does_not_take_the_endpoint_down(serving):
    """A caller must get an answer even when the probe is broken.

    A 500 with no body reads as "endpoint is up, verdict unknown", which a
    poller retries; a dead listener reads as "wrong host or port".
    """
    port = serving(_raising)
    assert get(port)[0] == 500
    # Still listening afterwards: a refused connection would read as the wrong
    # host or port, sending a caller off debugging the network instead.
    assert get(port, "/nope")[0] == 404


def _raising():
    raise RuntimeError("probe exploded")


# --- configuration ----------------------------------------------------


def test_no_probe_named_means_no_endpoint():
    assert readyz.configured({}) is None


def test_the_endpoint_can_be_turned_off():
    assert readyz.configured({readyz.PROBE_VAR: "network", readyz.DISABLE_VAR: "true"}) is None


def test_an_unknown_probe_name_raises_rather_than_serving_nothing():
    # Disabling quietly would hang every caller waiting on the endpoint, which
    # is a worse failure than refusing to start.
    with pytest.raises(ValueError, match=readyz.PROBE_VAR):
        readyz.configured({readyz.PROBE_VAR: "netwrok-sim"})


def test_a_non_numeric_port_raises():
    with pytest.raises(ValueError, match=readyz.PORT_VAR):
        readyz.configured({readyz.PROBE_VAR: "network", readyz.PORT_VAR: "http"})


def test_resolves_the_named_probe_and_the_default_port():
    from unifi_runtime import healthcheck

    probe, port = readyz.configured({readyz.PROBE_VAR: "network-sim"})
    assert probe is healthcheck.PROBES["network-sim"]
    assert port == readyz.DEFAULT_PORT


def test_the_port_is_overridable():
    _, port = readyz.configured({readyz.PROBE_VAR: "network", readyz.PORT_VAR: "9191"})
    assert port == 9191


@pytest.mark.parametrize("name", sorted(readyz.PROBES))
def test_every_healthcheck_probe_is_nameable_here(name):
    """The two registries must not drift: a variant whose HEALTHCHECK names a
    probe the endpoint cannot serve would report health two different ways."""
    probe, _ = readyz.configured({readyz.PROBE_VAR: name})
    assert callable(probe)
