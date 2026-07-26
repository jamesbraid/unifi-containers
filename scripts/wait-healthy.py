#!/usr/bin/env python3
"""Poll a container's healthcheck; exit 0 on healthy, 1 on stop or timeout.

    wait-healthy.py <container> [timeout-seconds]

Replaces .github/scripts/wait-healthy.sh, argv contract unchanged. The logic
lives in ci_docker so the image gates can wait for health mid-run (see
assert-uos-api-key.py) instead of shelling back out to a second copy of it.

stdlib only.
"""
import argparse
import sys

import ci_docker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container")
    parser.add_argument("timeout", nargs="?", type=int,
                        default=ci_docker.DEFAULT_TIMEOUT,
                        help=f"seconds (default {ci_docker.DEFAULT_TIMEOUT})")
    args = parser.parse_args(argv)
    return 0 if ci_docker.wait_healthy(args.container, args.timeout) else 1


if __name__ == "__main__":
    sys.exit(main())
