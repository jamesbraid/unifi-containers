# UniFi OS Server as a test target. Built FROM the OCI image embedded in the
# official installer (`unifi-containers extract-uos <arch>` unpacks and loads
# it), layering a container-shaped entrypoint (single /unifi volume, persisted
# UUID, init hooks) over it.
#
# Built from the REPO ROOT, because the shipped Python package lives outside
# this directory and COPY cannot reach outside its context:
#   docker build -f unifi-os/Dockerfile --build-arg UOS_VERSION=5.1.21 \
#     -t unifi-os-server:local .
#
# Runtime contract (see examples/docker-compose.yml for the full list):
# systemd is PID 1 — needs the documented cap list (no privileged mode),
# host cgroupns with /sys/fs/cgroup rw, and the tmpfs set. Entrypoint and
# volume layout adapted from toquanghieu/unifi-os-server-docker (MIT).
ARG BASE_IMAGE=uos-base:local
FROM ${BASE_IMAGE}

ARG UOS_VERSION=dev
ARG REVISION=dev
LABEL org.opencontainers.image.source="https://github.com/jamesbraid/unifi-containers" \
      org.opencontainers.image.title="unifi-os-server" \
      org.opencontainers.image.description="UniFi OS Server as a test target" \
      org.opencontainers.image.version="${UOS_VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="MIT"

# The stock image's own entrypoint expects these; FIRMWARE_PLATFORM is
# stamped per-arch by our entrypoint at runtime.
#
# PYTHONPATH is the contract the derived variants inherit: the entrypoint,
# the healthchecks and the owner seed are all `python3 -m` invocations of
# the package below. Debian 11 ships python 3.9.2 with no pip, so the
# package is stdlib-only by construction.
ENV APP_MODEL=UOSSERVER \
    PRODUCT_NAME=uosserver \
    APP_VERSION=${UOS_VERSION} \
    PYTHONPATH=/usr/local/lib/unifi-containers \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /usr/local/uos/init.d
COPY src/unifi_runtime /usr/local/lib/unifi-containers/unifi_runtime

# Shipped disabled. Each is enabled either by its variant's Dockerfile or, on
# this image, by the entrypoint when the matching UOS_* env flag is set.
# `systemd-analyze verify` turns a malformed unit or a moved ExecStart binary
# into a build failure instead of a silent no-start at boot.
COPY unifi-os/units/ /etc/systemd/system/
RUN systemd-analyze verify /etc/systemd/system/uos-network-direct.socket \
      /etc/systemd/system/uos-network-direct.service \
      /etc/systemd/system/uos-seed-owner.service

# systemd shutdown signal
STOPSIGNAL SIGRTMIN+3

VOLUME ["/unifi"]

# 443 GUI/API (ucore via nginx), 8080 device inform, 7443 the opt-in
# direct (SSO-free) Network API port (UOS_NETWORK_DIRECT=true; the sim
# variant enables it), 3478/udp STUN, 10003/udp discovery.
EXPOSE 443/tcp 8080/tcp 7443/tcp 3478/udp 10003/udp

HEALTHCHECK --interval=10s --timeout=8s --start-period=10m --retries=6 \
  CMD ["/usr/bin/python3", "-m", "unifi_runtime.healthcheck", "uos"]

ENTRYPOINT ["/usr/bin/python3", "-m", "unifi_runtime.entrypoint.uos"]
