"""Simulation-mode seed for the bundled Network Application.

Runs from the entrypoint's init-hook dir before the systemd handoff. Targets the
Network App API on 127.0.0.1:8081 and leaves the UOS ucore API unconfigured: the
demo Network App and unifi-core's /api/setup are mutually exclusive.
"""

import os
import subprocess
import sys

from .. import sysprops

SYSPROPS = "/var/lib/unifi/system.properties"
OWNER = "unifi:unifi"


def demo_settings(env):
    """The keys simulation mode needs."""
    return {
        "is_simulation": "true",
        "demo.num_uap": env.get("DEMO_NUM_UAP") or "3",
        "demo.num_ugw": env.get("DEMO_NUM_UGW") or "1",
        "demo.num_usw": env.get("DEMO_NUM_USW") or "5",
    }


def apply(path=SYSPROPS, env=None):
    """Add the demo keys to system.properties; the Network App rewrites this file itself."""
    env = os.environ if env is None else env
    os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
    try:
        with open(path) as handle:
            existing = handle.read()
    except OSError:
        existing = ""
    with open(path, "w") as handle:
        handle.write(sysprops.merge(existing, demo_settings(env), only_if_absent=True))


def main(env=None):
    apply(env=env)
    # The account may not exist in a stripped image; the file is still
    # readable by the app either way.
    subprocess.call(["chown", OWNER, SYSPROPS], stderr=subprocess.DEVNULL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
