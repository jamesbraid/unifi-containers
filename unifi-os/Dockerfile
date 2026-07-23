# UniFi OS Server as a test target. Built FROM the OCI image embedded in
# the official installer (see extract-uos.sh), layering a container-shaped
# entrypoint (single /unifi volume, persisted UUID, init hooks) over it.
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
ENV APP_MODEL=UOSSERVER \
    PRODUCT_NAME=uosserver \
    APP_VERSION=${UOS_VERSION}

RUN mkdir -p /usr/local/uos/init.d
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY docker-healthcheck-uos.sh /usr/local/bin/docker-healthcheck-uos.sh
# Copied in always; inert unless UOS_SEED_OWNER=true (the -seeded variant).
COPY seed-owner.sh /usr/local/bin/uos-seed-owner.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh \
              /usr/local/bin/docker-healthcheck-uos.sh \
              /usr/local/bin/uos-seed-owner.sh

# systemd shutdown signal
STOPSIGNAL SIGRTMIN+3

VOLUME ["/unifi"]

# 443 GUI/API (ucore via nginx), 8080 device inform, 7443 the opt-in
# direct (SSO-free) Network API port (UOS_NETWORK_DIRECT=true; the sim
# variant enables it), 3478/udp STUN, 10003/udp discovery.
EXPOSE 443/tcp 8080/tcp 7443/tcp 3478/udp 10003/udp

HEALTHCHECK --interval=10s --timeout=8s --start-period=10m --retries=6 \
  CMD /usr/local/bin/docker-healthcheck-uos.sh || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
