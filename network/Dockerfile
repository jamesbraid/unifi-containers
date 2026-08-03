# UniFi Network Application as a test target: deterministic startup, a
# health signal that means "the API answers", and a shutdown that closes
# MongoDB instead of killing it.
#
# Built from the REPO ROOT, because the shipped Python package lives outside
# this directory and COPY cannot reach outside its context:
#   docker build -f network/Dockerfile -t unifi-network:local .
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

ARG PKGURL=https://dl.ui.com/unifi/10.5.67/unifi_sysvinit_all.deb
ARG PKGSHA256=ee3b99b95678be4e1d8b58e3826e8911531b12b32ee8bf1ea4b49fb7e76f9783

ARG VERSION=dev
ARG REVISION=dev
LABEL org.opencontainers.image.source="https://github.com/jamesbraid/unifi-containers" \
      org.opencontainers.image.title="unifi-network" \
      org.opencontainers.image.description="UniFi Network Application as a test target" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="MIT"

# The only environment this image declares. The one runtime knob —
# UNIFI_JVM_OPTS, Ubiquiti's own name — is left unset so its default lives
# only in the entrypoint. Bytecode writing is off: the package is read-only
# in practice and .pyc files would only bloat the layer.
ENV PYTHONPATH=/usr/local/lib/unifi-containers \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /usr/local/unifi/init.d
COPY src/unifi_runtime /usr/local/lib/unifi-containers/unifi_runtime
COPY network/docker-build.sh /usr/local/bin/docker-build.sh

# The deb requires a `unifi` account to exist. It is created at a fixed
# uid/gid and cannot be remapped at runtime; the container runs as root.
RUN set -ex \
 && mkdir -p /usr/share/man/man1/ \
 && groupadd -r unifi -g 999 \
 && useradd --no-log-init -r -u 999 -g 999 unifi \
 && chmod +rx /usr/local/bin/docker-build.sh \
 && /usr/local/bin/docker-build.sh "${PKGURL}" "${PKGSHA256}"

VOLUME ["/unifi", "/unifi/run"]

EXPOSE 6789/tcp 8080/tcp 8443/tcp 8880/tcp 8843/tcp 3478/udp 10001/udp

WORKDIR /unifi

HEALTHCHECK --start-period=5m \
  CMD ["/usr/bin/python3", "-m", "unifi_runtime.healthcheck", "network"]

ENTRYPOINT ["/usr/bin/python3", "-m", "unifi_runtime.entrypoint.network"]

CMD ["unifi"]
