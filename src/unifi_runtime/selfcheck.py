"""Prove the shipped package works on the interpreter it actually ships with.

    python3 -m unifi_runtime.selfcheck

CI runs the test suite under uv's python-build-standalone 3.9, not Debian's
3.9.2. This runs in the built image, on the vendor interpreter, and only checks
that every shipped module imports at all.
"""

import importlib
import sys

MINIMUM = (3, 9)

MODULES = (
    "unifi_runtime.env",
    "unifi_runtime.http",
    "unifi_runtime._vendor.javaproperties",
    "unifi_runtime.sysprops",
    "unifi_runtime.unifi.network",
    "unifi_runtime.unifi.ucore",
    "unifi_runtime.unifi.ulp",
    "unifi_runtime.healthcheck",
    "unifi_runtime.entrypoint.uos",
    "unifi_runtime.entrypoint.demo",
    "unifi_runtime.seed.uos_owner",
    "unifi_runtime.seed.network_wizard",
    "unifi_runtime.entrypoint.network",
)


def check():
    """Import every shipped module. Returns the failures."""
    failures = []
    if sys.version_info < MINIMUM:
        failures.append(
            "interpreter is {}, need >= {}".format(
                ".".join(str(part) for part in sys.version_info[:3]),
                ".".join(str(part) for part in MINIMUM),
            )
        )

    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # broad on purpose: report every failure kind
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    return failures


def main():
    print(f"python {sys.version.split()[0]} at {sys.executable}")
    failures = check()
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        print(f"selfcheck FAILED ({len(failures)} problems)")
        return 1
    print("selfcheck passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
