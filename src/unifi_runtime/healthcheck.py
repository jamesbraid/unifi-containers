"""Container healthchecks, one probe per image variant.

    python3 -m unifi_runtime.healthcheck <probe>

Docker runs these every few seconds forever and UniFi rate-limits login
globally (429, Retry-After up to an hour), so `marker_gate` proves readiness
once and is a file test thereafter, and probe parts run rate-limited last.

Logging in is not the last thing to come up. On the variants that have an
admin, two surfaces settle *after* it: the v2 API, which 5xxs while
zone-based-firewall defaults materialize, and the demo fleet, which populates
a few seconds later still. Both are gated here so "wait for healthy" is the
whole contract and no consumer has to hand-roll those waits — which is what
every consumer was otherwise doing.
"""

import os
import sys

from . import sysprops
from .entrypoint import demo
from .env import is_enabled, setting
from .http import json_request
from .seed.network_wizard import SEED_PASS, SEED_USER
from .unifi.network import NetworkApp
from .unifi.ucore import Ucore

MARKER = "/tmp/unifi-ready"

#: The login is proven separately from, and earlier than, full readiness: the
#: stages after it can take a while, and re-logging in meanwhile is precisely
#: what the rate limiter punishes.
LOGIN_MARKER = "/tmp/unifi-login-ready"

#: Where the proven session lives. Docker runs every healthcheck as a fresh
#: process, so an in-memory session would be gone by the next tick — only a jar
#: on disk lets one login serve the later stages.
COOKIE_JAR = "/tmp/unifi-cookies"

DEFAULT_SITE = "default"

PROBE_TIMEOUT = 5


def marker_gate(probe, marker=MARKER):
    """Run `probe` until it first succeeds, then never again."""
    if os.path.exists(marker):
        return True
    if not probe():
        return False
    try:
        with open(marker, "w"):
            pass
    except OSError:
        # An unwritable /tmp costs a repeated probe, not a failed
        # healthcheck; the container is genuinely ready either way.
        pass
    return True


def _forget(marker):
    """Drop a marker so its stage is proven again."""
    try:
        os.remove(marker)
    except OSError:
        pass


def v2_settled(status):
    """True once the v2 surface has stopped failing.

    Deliberately not `== 200`: a controller predating zone-based firewall has
    no such endpoint and 404s, which is a ready controller with nothing left to
    wait for. A request that never landed is 0, which this rejects.
    """
    return 200 <= status < 500


def expected_fleet(env=None):
    """How many demo devices this image seeds.

    Derived from the settings `demo-mode` actually writes rather than restated
    here: a count that disagreed with the seed would either wedge the gate
    forever or let it pass early. SIM_EXPECT_DEVICES overrides the total.
    """
    env = os.environ if env is None else env
    override = env.get("SIM_EXPECT_DEVICES")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    settings = demo.demo_settings(env)
    total = 0
    for key in ("demo.num_uap", "demo.num_ugw", "demo.num_usw"):
        try:
            total += int(settings[key])
        except (KeyError, TypeError, ValueError):
            # A garbled DEMO_NUM_* undercounts, which opens the gate early.
            # Wedging the container shut over a typo would be worse.
            pass
    return total


def staged(login_probe, v2_status, fleet_ready=None, login_marker=LOGIN_MARKER):
    """Prove the login once, then the surfaces that settle after it.

    The login carries its own marker so it runs exactly once however long the
    later stages take. That is the point: extending readiness must cost zero
    extra logins.
    """
    if not marker_gate(login_probe, login_marker):
        return False
    status = v2_status()
    if status in (401, 403):
        # The session died. This must be tested before v2_settled, because 401
        # is numerically below 500 and would otherwise read as ready. Dropping
        # the marker makes the next tick authenticate again.
        _forget(login_marker)
        return False
    if not v2_settled(status):
        return False
    return fleet_ready is None or fleet_ready()


# --- UniFi OS Server probes -------------------------------------------


def uos_ready(base_url="https://localhost", timeout=PROBE_TIMEOUT):
    """Base image: the ucore public API answers real JSON on :443. Not rate-limited."""
    return Ucore(base_url, timeout=timeout).is_api_answering()


#: The bundled Network App, reached directly rather than through the UOS proxy.
UOS_NETWORK_URL = "http://127.0.0.1:8081"


def sim_ready(
    base_url=UOS_NETWORK_URL,
    username="admin",
    password="admin",
    timeout=PROBE_TIMEOUT,
    cookie_jar=None,
):
    """-sim variant: the bundled Network App can genuinely authenticate. Rate-limited."""
    return NetworkApp(base_url, timeout=timeout, cookie_jar=cookie_jar).login_ok(username, password)


def seeded_ready(
    base_url="https://127.0.0.1",
    key_file=None,
    username="admin",
    password="admin",
    timeout=PROBE_TIMEOUT,
    cookie_jar=None,
):
    """-seeded variant: both seed steps finished. An unset `key_file` means that seed is off."""
    ucore = Ucore(base_url, timeout=timeout, cookie_jar=cookie_jar)
    if key_file:
        key = _read(key_file)
        if not key:
            return False
        if ucore.api_key_status(key, timeout=timeout) != 200:
            return False
    return ucore.login_status(username, password, timeout=timeout) == 200


def _read(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return ""


# --- UniFi Network Application probes ---------------------------------

#: What the images write when the key is absent. An existing /unifi volume may
#: carry a different one, and the controller obeys the file, not this.
DEFAULT_HTTPS_PORT = "8443"
SYSPROPS_PATH = "/unifi/data/system.properties"


def network_url(path=SYSPROPS_PATH, host="localhost"):
    """Where the controller serves HTTPS, according to system.properties.

    The port is the file's to decide: `write_system_properties` only supplies a
    default when the key is absent, so a volume that pins another one keeps it.
    A probe fixed at 8443 would then poll a port nothing listens on and the
    container could never go healthy.
    """
    try:
        with open(path) as handle:
            port = sysprops.parse(handle.read()).get("unifi.https.port")
    except OSError:
        port = None
    return f"https://{host}:{port or DEFAULT_HTTPS_PORT}"


def network_answering(base_url=None, timeout=PROBE_TIMEOUT):
    """Base image: the HTTPS listener answers. Goes green 5-20s before the API can log in."""
    return 200 <= json_request(base_url or network_url(), timeout=timeout).status < 400


def network_login_ready(username, password, base_url=None, timeout=PROBE_TIMEOUT, cookie_jar=None):
    """-sim and -seeded: the controller can genuinely authenticate."""
    return NetworkApp(base_url or network_url(), timeout=timeout, cookie_jar=cookie_jar).login_ok(
        username, password
    )


def _network_app(cookie_jar=COOKIE_JAR):
    return NetworkApp(network_url(), timeout=PROBE_TIMEOUT, cookie_jar=cookie_jar)


def _fleet_ready(app, site=DEFAULT_SITE):
    """Every seeded demo device is present, not merely the first one.

    The fleet populates incrementally, so "at least one device" still hands out
    a half-built controller — which is exactly the race a consumer polling
    stat/device was working around.
    """
    return len(app.devices(site)) >= expected_fleet()


def _network_sim():
    app = _network_app()
    return marker_gate(
        lambda: staged(
            lambda: app.login_ok("admin", "admin"), app.v2_status, lambda: _fleet_ready(app)
        )
    )


def _network_seeded():
    # No fleet stage: -seeded is a real empty site, so waiting for devices
    # would wedge it shut forever.
    app = _network_app()
    return marker_gate(lambda: staged(lambda: app.login_ok(SEED_USER, SEED_PASS), app.v2_status))


def _sim():
    app = NetworkApp(UOS_NETWORK_URL, timeout=PROBE_TIMEOUT, cookie_jar=COOKIE_JAR)
    return marker_gate(
        lambda: staged(
            lambda: sim_ready(cookie_jar=COOKIE_JAR), app.v2_status, lambda: _fleet_ready(app)
        )
    )


def _seeded():
    # Whether a key exists is UOS_SEED_API_KEY's answer, not the path variable's.
    # Keying it off the path meant dropping an apparently-redundant ENV from a
    # variant Dockerfile silently disabled this probe.
    env = os.environ
    seeding = is_enabled(env.get("UOS_SEED_API_KEY"))
    # No fleet stage: -seeded seeds an owner, not devices.
    return marker_gate(
        lambda: staged(
            lambda: seeded_ready(
                key_file=setting("UOS_API_KEY_FILE", env) if seeding else None,
                username=setting("UOS_ADMIN_USER", env),
                password=setting("UOS_ADMIN_PASS", env),
                cookie_jar=COOKIE_JAR,
            ),
            Ucore(timeout=PROBE_TIMEOUT, cookie_jar=COOKIE_JAR).v2_status,
        )
    )


#: Each image variant's HEALTHCHECK names one of these.
PROBES = {
    "uos": uos_ready,
    "sim": _sim,
    "seeded": _seeded,
    "network": network_answering,
    "network-sim": _network_sim,
    "network-seeded": _network_seeded,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in PROBES:
        print("usage: healthcheck <{}>".format("|".join(sorted(PROBES))), file=sys.stderr)
        return 2
    return 0 if PROBES[argv[0]]() else 1


if __name__ == "__main__":
    sys.exit(main())
