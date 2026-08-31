"""Prove the vendor's own tooling can read what the entrypoint stamps.

    python3 -m unifi_runtime.vendorcheck

Runs inside the freshly built image at CI time, before anything boots. The
entrypoint feeds an undocumented vendor interface — the files ubnt-tools
reads — and Ubiquiti moves it between releases: 5.1.21 parsed the model out
of /usr/lib/version, 5.1.37 reads /usr/lib/app_model, and nothing announces
the next move. When it drifts again this fails the build in seconds and names
the file, instead of an integration boot timing out half an hour later with
nothing in the logs.

Two checks, both against the image's own /sbin/ubnt-tools:

  1. Every path the script reads out of /usr/lib exists and is non-empty
     after the entrypoint's stamp pass.
  2. `ubnt-tools id` runs clean and reports the model the environment names.
"""

import os
import re
import subprocess
import sys

from .entrypoint import uos

UBNT_TOOLS = "/sbin/ubnt-tools"

#: `$(cat /usr/lib/app_model)` and `$(cut -d. -f1 /usr/lib/version)` alike:
#: any command substitution whose last word is an absolute /usr/lib path.
#: /data/uos_uuid is deliberately out of scope — it belongs to the volume
#: layout, which the integration suite already proves.
STAMP_READ_RE = re.compile(r"\$\([^)]*?\s(/usr/lib/[A-Za-z0-9_./-]+)\s*\)")

SHORTNAME_RE = re.compile(r"^board\.shortname=(.*)$", re.MULTILINE)


def stamp_reads(script_text):
    """Every /usr/lib path the script reads through a command substitution."""
    return sorted(set(STAMP_READ_RE.findall(script_text)))


def missing_stamps(paths, root="/"):
    """The subset of `paths` that is absent or empty under `root`."""
    prefix = "" if root == "/" else str(root).rstrip("/")
    missing = []
    for path in paths:
        real = prefix + path
        try:
            with open(real) as handle:
                if not handle.read().strip():
                    missing.append(path)
        except OSError:
            missing.append(path)
    return missing


def reported_shortname(output):
    """The board.shortname value in `ubnt-tools id` output, or None."""
    match = SHORTNAME_RE.search(output)
    return match.group(1).strip() if match else None


def main(env=None):
    env = os.environ if env is None else env
    model = env.get("APP_MODEL", "")
    if not model:
        print("vendorcheck: APP_MODEL is not set in the image environment", file=sys.stderr)
        return 1

    # The same writes the entrypoint performs, minus the volume relayout:
    # ubnt-tools also reads /data/uos_uuid, which in the real boot exists via
    # the /unifi symlink plan. Here the stamp file is enough.
    uos.write_stamps(env)
    os.makedirs("/data", exist_ok=True)
    if not os.path.exists("/data/uos_uuid"):
        uos.ensure_uuid(env, path="/data/uos_uuid")

    try:
        with open(UBNT_TOOLS) as handle:
            script = handle.read()
    except OSError as exc:
        print(f"vendorcheck: cannot read {UBNT_TOOLS}: {exc}", file=sys.stderr)
        return 1

    failures = []
    for path in missing_stamps(stamp_reads(script)):
        failures.append(f"ubnt-tools reads {path}, which nothing stamps")

    result = subprocess.run([UBNT_TOOLS, "id"], capture_output=True, text=True)
    if result.returncode != 0:
        failures.append(
            f"ubnt-tools id exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[:200]}"
        )
    shortname = reported_shortname(result.stdout)
    if shortname != model:
        failures.append(f"ubnt-tools id reports board.shortname={shortname!r}, expected {model!r}")

    for failure in failures:
        print(f"vendorcheck: {failure}", file=sys.stderr)
    if not failures:
        print(f"vendorcheck passed: model {model}, stamps {stamp_reads(script)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
