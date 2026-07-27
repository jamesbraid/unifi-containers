"""The UniFi OS seed knobs: their names, their defaults, and how a flag reads.

Three readers have to agree on these. The entrypoint resolves them into the
seed unit's EnvironmentFile, the seed step reads them back out, and the
healthcheck reads them to decide what to probe. Disagreement is not a partial
failure: a healthcheck that demands a key the seed step declined to mint leaves
the container unhealthy for its whole life.
"""

from collections import OrderedDict

#: On tmpfs, so a container restart re-proves readiness from scratch.
KEY_FILE = "/unifi/api-key"

#: The seed settings and their defaults, in the order the EnvironmentFile lists them.
SEED_DEFAULTS = OrderedDict(
    (
        ("UOS_ADMIN_USER", "admin"),
        ("UOS_ADMIN_PASS", "admin"),
        ("UOS_COUNTRY", "840"),
        ("UOS_TIMEZONE", "UTC"),
        ("UOS_CONSOLE_NAME", "unifi-os-sim"),
        ("UOS_SEED_API_KEY", "0"),
        ("UOS_API_KEY_NAME", "unifi-containers-seeded"),
        ("UOS_API_KEY_FILE", KEY_FILE),
    )
)


def is_enabled(value):
    """True for exactly `true` or `1`. "yes", "TRUE", "on" and None are all off."""
    return value in ("true", "1")


def setting(key, env):
    """A seed setting as its readers see it: the environment's value, or the default."""
    return env.get(key) or SEED_DEFAULTS[key]
