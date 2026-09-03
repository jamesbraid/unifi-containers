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
    PYTHONDONTWRITEBYTECODE=1 \
    UOS_READYZ=true \
    READYZ_PROBE=uos

RUN mkdir -p /usr/local/uos/init.d
COPY src/unifi_runtime /usr/local/lib/unifi-containers/unifi_runtime

# Bring the bundled Network application up to the pinned current release using
# the product's own updater — the same OTA a real console performs (uid-agent
# queries the firmware API for platform uos-deb11-<arch>; the installer ships
# whatever was current at its build, which trails GA by weeks). Three
# accommodations for running it in a build step instead of a booted system:
# vendorcheck writes the identity stamps the tooling reads (and proves them),
# the /data dirs the agent expects don't exist yet, and the vendor's dpkg
# hooks pipe through systemd-cat, which has no journald to talk to here — so
# they step aside for the install and come back after. The uuid vendorcheck
# minted is removed: baked into the image it would become every container's
# identity.
ARG UOS_APP_VERSION
LABEL org.unifi-containers.network-version="${UOS_APP_VERSION}"
RUN set -e \
 && python3 -m unifi_runtime.vendorcheck \
 && mkdir -p /data/uos/tmp /data/unifi-core/config /data/unifi-core/logs /tmp/dpkg-hooks \
 && for f in /etc/dpkg/dpkg.cfg.d/*; do grep -q systemd-cat "$f" && mv "$f" /tmp/dpkg-hooks/ || true; done \
 && uos runnable install unifi --version "${UOS_APP_VERSION}" \
 && for f in /tmp/dpkg-hooks/*; do [ -e "$f" ] && mv "$f" /etc/dpkg/dpkg.cfg.d/; done \
 && rmdir /tmp/dpkg-hooks \
 && test "$(dpkg-query -W -f '${Version}' unifi)" = "${UOS_APP_VERSION}" \
 && rm -rf /data/uos/downloads /data/uos_uuid /data/unifi-core/logs/* /var/cache/apt/archives/*.deb

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
# 9099 is the readiness endpoint (unifi_runtime.readyz), served by
# uos-readyz.service for callers that hold only a URL and so cannot read this
# image's HEALTHCHECK.
EXPOSE 443/tcp 8080/tcp 7443/tcp 9099/tcp 3478/udp 10003/udp

HEALTHCHECK --interval=10s --timeout=8s --start-period=10m --retries=6 \
  CMD ["/usr/bin/python3", "-m", "unifi_runtime.healthcheck", "uos"]

ENTRYPOINT ["/usr/bin/python3", "-m", "unifi_runtime.entrypoint.uos"]
