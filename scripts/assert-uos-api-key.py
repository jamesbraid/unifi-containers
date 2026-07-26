#!/usr/bin/env python3
"""Prove a seeded UniFi OS container's published X-API-KEY actually works.

    assert-uos-api-key.py <container> [--key-name NAME] [--key-file PATH]
    assert-uos-api-key.py <container> --expect-absent

This is a release gate: it runs after the healthcheck goes green and before
images are pushed, so a bump that breaks the mint blocks publication instead
of shipping a console no harness can authenticate against.

Checks, in order, failing at the first one:

  1. the key file is non-empty;
  2. the key authenticates the classic dialect (stat/device) — the path
     ubitofu's production `unifi-os` dialect actually calls;
  3. the key authenticates the modern integration API;
  4. a bogus key is REJECTED — without this, "everything returns 200"
     regressions pass the three checks above;
  5. a restart reuses the key rather than minting another: the file is
     unchanged and ULP still lists exactly one key of this name. A re-mint
     per boot would leak a dead key every restart and quietly break any
     harness holding the old value.

--expect-absent inverts the first check for images where the key seed is off
(the base and -sim variants), proving the entrypoint stays inert.

Requests are issued from inside the container because run-uos.sh publishes no
ports. stdlib only.
"""
import argparse
import json
import sys

import ci_docker

CLASSIC = "https://127.0.0.1/proxy/network/api/s/{site}/stat/device"
INTEGRATION = "https://127.0.0.1/proxy/network/integration/v1/sites"
ULP_KEYS = "http://127.0.0.1:9080/api/v2/keys"
# Deliberately invalid: check 4 asserts the console rejects it.
BOGUS_KEY = "bogus-key-000000000000000000000"  # gitleaks:allow


def count_keys_named(payload, name):
    """Count ULP-listed API keys with this name. Pure.

    A body that is missing, unparseable, or shaped unexpectedly counts zero
    rather than raising: the caller reports "expected 1, found 0", which is
    the more useful failure message.
    """
    try:
        parsed = json.loads(payload or "")
    except (json.JSONDecodeError, TypeError):
        return 0
    if not isinstance(parsed, dict):
        return 0
    data = parsed.get("data")
    if not isinstance(data, list):
        return 0
    return sum(1 for item in data
               if isinstance(item, dict) and item.get("name") == name)


def explain_status(actual, expected, what):
    """Message for a status mismatch. Pure.

    Curl's "000" means the request never completed, which is a different
    problem from the console answering with the wrong code — say so.
    """
    if actual == "000":
        return (f"{what}: request never completed (curl failed) — the "
                f"endpoint was unreachable from inside the container")
    return f"{what}: got HTTP {actual}, expected {expected}"


class Gate:
    """Runs checks in order, reporting the first failure."""

    def __init__(self, out=print):
        self.out = out

    def ok(self, message):
        self.out(f"ok   {message}")

    def fail(self, message):
        self.out(f"FAIL {message}")
        return False


def check_absent(container, key_file, gate):
    if ci_docker.read_file(container, key_file) is None:
        gate.ok(f"{key_file} absent — key seed is inert in this image")
        return True
    return gate.fail(f"{key_file} exists, but this image should not seed a key")


def run_checks(container, key_file, key_name, site, timeout, skip_restart,
               gate):
    raw = ci_docker.read_file(container, key_file)
    key = (raw or "").strip()
    if not key:
        return gate.fail(f"{key_file} is missing or empty — the seed never "
                         f"published a key")
    gate.ok(f"{key_file} holds a key ({len(key)} chars)")

    classic = CLASSIC.format(site=site)
    status = ci_docker.http_status(container, classic, {"X-API-KEY": key})
    if status != "200":
        return gate.fail(explain_status(status, "200", "classic dialect"))
    gate.ok("classic dialect (stat/device) authenticates")

    status = ci_docker.http_status(container, INTEGRATION, {"X-API-KEY": key})
    if status != "200":
        return gate.fail(explain_status(status, "200", "integration API"))
    gate.ok("integration API (v1/sites) authenticates")

    status = ci_docker.http_status(container, classic, {"X-API-KEY": BOGUS_KEY})
    if status not in ("401", "403"):
        return gate.fail(f"bogus key got HTTP {status}, expected 401 — the "
                         f"console is not enforcing X-API-KEY, so the checks "
                         f"above prove nothing")
    gate.ok(f"bogus key rejected (HTTP {status})")

    listed = count_keys_named(ci_docker.exec_in(
        container, ["curl", "-s", "--max-time", "10", ULP_KEYS]), key_name)
    if listed != 1:
        return gate.fail(f"ULP lists {listed} keys named {key_name!r}, "
                         f"expected exactly 1")
    gate.ok(f"ULP lists exactly one key named {key_name!r}")

    if skip_restart:
        gate.ok("restart check skipped by request")
        return True

    gate.out(f"...restarting {container} to prove the key is reused")
    ci_docker.restart(container)
    if not ci_docker.wait_healthy(container, timeout, out=gate.out):
        return gate.fail("container did not become healthy again after restart")

    after = (ci_docker.read_file(container, key_file) or "").strip()
    if after != key:
        return gate.fail("the key changed across a restart — the seed is "
                         "re-minting every boot instead of reusing the "
                         "published key")
    gate.ok("key unchanged across restart")

    listed = count_keys_named(ci_docker.exec_in(
        container, ["curl", "-s", "--max-time", "10", ULP_KEYS]), key_name)
    if listed != 1:
        return gate.fail(f"after restart ULP lists {listed} keys named "
                         f"{key_name!r}, expected 1 — a duplicate was minted")
    gate.ok("no duplicate key minted across restart")

    status = ci_docker.http_status(container, classic, {"X-API-KEY": key})
    if status != "200":
        return gate.fail(explain_status(
            status, "200", "classic dialect after restart"))
    gate.ok("key still authenticates after restart")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container")
    parser.add_argument("--key-file", default="/unifi/api-key")
    parser.add_argument("--key-name", default="unifi-containers-seeded")
    parser.add_argument("--site", default="default")
    parser.add_argument("--timeout", type=int, default=900,
                        help="seconds to wait for health after the restart")
    parser.add_argument("--expect-absent", action="store_true",
                        help="assert no key was seeded (base and -sim images)")
    parser.add_argument("--skip-restart", action="store_true",
                        help="skip the restart-reuse check")
    args = parser.parse_args(argv)

    gate = Gate()
    try:
        if args.expect_absent:
            passed = check_absent(args.container, args.key_file, gate)
        else:
            passed = run_checks(args.container, args.key_file, args.key_name,
                                args.site, args.timeout, args.skip_restart,
                                gate)
    except ci_docker.DockerError as exc:
        gate.fail(str(exc))
        return 1

    if not passed:
        return 1
    print(f"API key gate passed for {args.container}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
