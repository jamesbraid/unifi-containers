#!/usr/bin/env bash
#
# Build-time bootstrap for the UniFi Network image. Deliberately still
# shell: it runs against a bare ubuntu:24.04 that has no interpreter until
# this script installs one, it runs only at build time, and every failure
# aborts the layer loudly.

# pipefail so a failed `curl | gpg` aborts here rather than writing an empty
# keyring and failing later as an unrelated-looking apt error.
set -euo pipefail

BASEDIR=/usr/lib/unifi
DATADIR=/unifi/data
LOGDIR=/unifi/log
RUNDIR=/unifi/run

if [ -z "${1:-}" ]; then
    echo "usage: docker-build.sh PKGURL PKGSHA256" >&2
    exit 1
fi
if [ -z "${2:-}" ]; then
    echo "refusing to build without a pinned sha256 for ${1}" >&2
    exit 1
fi

apt-get update
# The deb's own dependencies are listed here, not left to the deb install
# below, because only this call can pass --no-install-recommends.
#
# python3 is explicit: the entrypoint, the healthcheck and the init hooks are
# all `python3 -m` invocations, so it must not be a transitive dependency.
apt-get install -qy --no-install-recommends \
    binutils \
    ca-certificates \
    curl \
    gpg \
    logrotate \
    openjdk-25-jre-headless \
    procps \
    python3 \
    tzdata

# The deb wants mongodb-org-server >=3.6 <8.1 and Ubuntu 24.04 ships no
# mongodb-server at all, so it comes from MongoDB's own repo. `jammy`, not
# `noble`: MongoDB published no 6.0 series for noble.
curl -Ls https://www.mongodb.org/static/pgp/server-6.0.asc | gpg --dearmor -o /usr/share/keyrings/mongo.gpg
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongo.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/6.0 multiverse" \
    | tee /etc/apt/sources.list.d/mongodb-org-6.0.list
apt-get update
apt-get install -qy mongodb-org-server

# By URL and sha256, never from Ubiquiti's apt repo — the pin is the point.
curl -L -o ./unifi.deb "${1}"
echo "${2}  ./unifi.deb" | sha256sum -c -
apt-get -qy install ./unifi.deb
rm -f ./unifi.deb
chown -R unifi:unifi ${BASEDIR}
rm -rf /var/lib/apt/lists/*

# State lives on one volume. The BASEDIR symlinks are NOT created here:
# `unifi-network-service-helper init` owns them and re-points them on every boot.
#
# The /var/{lib,log,run}/unifi links do stay: the deb hardcodes those paths
# in places we do not control, and the sim hook writes system.properties
# through /var/lib/unifi so it shares one implementation with the UniFi OS
# variant.
rm -rf /var/lib/unifi /var/log/unifi /var/run/unifi \
       ${BASEDIR}/data ${BASEDIR}/run ${BASEDIR}/logs
mkdir -p ${DATADIR} ${LOGDIR} ${RUNDIR}
ln -s ${DATADIR} /var/lib/unifi
ln -s ${LOGDIR} /var/log/unifi
ln -s ${RUNDIR} /var/run/unifi
chown -R unifi:unifi /unifi

rm -rf "${0}"
