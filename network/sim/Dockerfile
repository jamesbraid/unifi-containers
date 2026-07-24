# Simulation-mode variant for integration testing: boots straight into a
# demo controller with an admin/admin account and seeded demo devices (no
# setup wizard), and a healthcheck that only reports healthy once the API
# answers a real JSON login. Early in boot the controller serves an HTML
# placeholder with HTTP 200 on every path, so the base image's plain
# port/status healthcheck reports ready ~20 seconds too early; the login
# probe does not.
#
# With this image a test harness in any language reduces to "run the
# container, wait for healthy" — e.g. `docker compose up --wait`, or a
# testcontainers healthy-wait strategy.
#
# Build against a published base tag:
#   docker build --build-arg BASE_IMAGE=ghcr.io/jamesbraid/unifi-network:10.4.57 \
#     -t unifi-network:10.4.57-sim network/sim/
ARG BASE_IMAGE=ghcr.io/jamesbraid/unifi-network:latest
FROM ${BASE_IMAGE}

# /usr/local/unifi/init.d, not /unifi/init.d: the base image declares
# /unifi as a VOLUME, so anything COPY'd under it after that declaration
# is silently discarded at runtime. The entrypoint run-parts both dirs.
COPY demo-mode /usr/local/unifi/init.d/demo-mode
COPY docker-healthcheck-sim.sh /usr/local/bin/docker-healthcheck-sim.sh
RUN chmod 755 /usr/local/unifi/init.d/demo-mode /usr/local/bin/docker-healthcheck-sim.sh

HEALTHCHECK --interval=10s --timeout=6s --start-period=5m --retries=5 \
  CMD /usr/local/bin/docker-healthcheck-sim.sh || exit 1
