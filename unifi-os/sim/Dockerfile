# Simulation-mode variant of the UniFi OS Server test target: the bundled
# Network Application boots in demo mode — admin/admin account, seeded
# demo devices, no setup wizard — and the healthcheck only reports healthy
# once that app answers a real JSON login. The direct (SSO-free) API port
# is enabled by default: publish 7443 and a test harness needs no UOS SSO
# dance at all:
#   docker run -d -p 7443:7443 [contract flags] \
#     ghcr.io/jamesbraid/unifi-os-server:sim
#   curl -X POST -H 'Content-Type: application/json' \
#     -d '{"username":"admin","password":"admin"}' http://localhost:7443/api/login
#
# Small context, unlike the base image: this layer adds one init hook and
# inherits PYTHONPATH and the shipped package from the base.
#
# Build against a published base tag:
#   docker build --build-arg BASE_IMAGE=ghcr.io/jamesbraid/unifi-os-server:5.1.21 \
#     -t unifi-os-server:5.1.21-sim unifi-os/sim/
ARG BASE_IMAGE=ghcr.io/jamesbraid/unifi-os-server:latest
FROM ${BASE_IMAGE}

# This variant always wants the direct port, so it is enabled here rather than
# left to the entrypoint. The env flag stays set so `docker inspect` still shows
# which knob this variant has turned on.
ENV UOS_NETWORK_DIRECT=true
RUN systemctl enable uos-network-direct.socket

COPY demo-mode /usr/local/uos/init.d/demo-mode
RUN chmod 755 /usr/local/uos/init.d/demo-mode

EXPOSE 7443/tcp

HEALTHCHECK --interval=10s --timeout=20s --start-period=10m --retries=6 \
  CMD ["/usr/bin/python3", "-m", "unifi_runtime.healthcheck", "sim"]
