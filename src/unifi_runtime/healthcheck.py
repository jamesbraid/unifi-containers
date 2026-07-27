"""Container healthchecks, one probe per image variant.

    python3 -m unifi_runtime.healthcheck <probe>

Docker runs these every few seconds forever and UniFi rate-limits login
globally (429, Retry-After up to an hour), so `marker_gate` proves readiness
once and is a file test thereafter, and probe parts run rate-limited last.
"""

import os
import sys

from .env import is_enabled, setting
from .http import json_request
from .seed.network_wizard import SEED_PASS, SEED_USER
from .unifi.network import NetworkApp
from .unifi.ucore import Ucore

MARKER = "/tmp/unifi-ready"

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


# --- UniFi OS Server probes -------------------------------------------


def uos_ready(base_url="https://localhost", timeout=PROBE_TIMEOUT):
    """Base image: the ucore public API answers real JSON on :443. Not rate-limited."""
    return Ucore(base_url, timeout=timeout).is_api_answering()


def sim_ready(
    base_url="http://127.0.0.1:8081", username="admin", password="admin", timeout=PROBE_TIMEOUT
):
    """-sim variant: the bundled Network App can genuinely authenticate. Rate-limited."""
    return NetworkApp(base_url, timeout=timeout).login_ok(username, password)


def seeded_ready(
    base_url="https://127.0.0.1",
    key_file=None,
    username="admin",
    password="admin",
    timeout=PROBE_TIMEOUT,
):
    """-seeded variant: both seed steps finished. An unset `key_file` means that seed is off."""
    ucore = Ucore(base_url, timeout=timeout)
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

#: Fixed rather than read back out of system.properties: no image moves the
#: HTTPS port off the default.
NETWORK_URL = "https://localhost:8443"


def network_answering(base_url=NETWORK_URL, timeout=PROBE_TIMEOUT):
    """Base image: the HTTPS listener answers. Goes green 5-20s before the API can log in."""
    return 200 <= json_request(base_url, timeout=timeout).status < 400


def network_login_ready(username, password, base_url=NETWORK_URL, timeout=PROBE_TIMEOUT):
    """-sim and -seeded: the controller can genuinely authenticate."""
    return NetworkApp(base_url, timeout=timeout).login_ok(username, password)


def _network_sim():
    return marker_gate(lambda: network_login_ready("admin", "admin"))


def _network_seeded():
    return marker_gate(lambda: network_login_ready(SEED_USER, SEED_PASS))


def _sim():
    return marker_gate(sim_ready)


def _seeded():
    # Whether a key exists is UOS_SEED_API_KEY's answer, not the path variable's.
    # Keying it off the path meant dropping an apparently-redundant ENV from a
    # variant Dockerfile silently disabled this probe.
    env = os.environ
    seeding = is_enabled(env.get("UOS_SEED_API_KEY"))
    return marker_gate(
        lambda: seeded_ready(
            key_file=setting("UOS_API_KEY_FILE", env) if seeding else None,
            username=setting("UOS_ADMIN_USER", env),
            password=setting("UOS_ADMIN_PASS", env),
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
