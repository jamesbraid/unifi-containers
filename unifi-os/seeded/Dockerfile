# Seeded variant of the UniFi OS Server test target: the UOS first-run setup
# is completed headlessly at first boot via unifi-core's own /api/setup — an
# Owner admin, no UI account, no cloud/SSO, no setup wizard — so a harness can
# log in to the UOS API on :443 immediately:
#   docker run -d -p 11443:443 [contract flags] \
#     ghcr.io/jamesbraid/unifi-os-server:seeded
#   curl -ksX POST -H 'Content-Type: application/json' \
#     -d '{"username":"admin","password":"admin"}' https://localhost:11443/api/auth/login
#
# It also mints an X-API-KEY and publishes it at /unifi/api-key, so a harness
# can use the production `unifi-os` dialect (/proxy/network + X-API-KEY) with
# no SSO. The value is random per volume — the path is the contract:
#   key=$(docker exec <container> cat /unifi/api-key)
#   curl -ks -H "X-API-KEY: $key" \
#     https://localhost:11443/proxy/network/api/s/default/stat/device
#
# This targets the UOS-native API (the future-proof surface). It does NOT seed
# demo devices and does NOT open the direct Network App port — for fake devices
# on the Network App API use the -sim variant instead (the two setup paths are
# mutually exclusive; see docs).
#
# Build against a published base tag:
#   docker build --build-arg BASE_IMAGE=ghcr.io/jamesbraid/unifi-os-server:5.1.21 \
#     -t unifi-os-server:5.1.21-seeded unifi-os/seeded/
ARG BASE_IMAGE=ghcr.io/jamesbraid/unifi-os-server:latest
FROM ${BASE_IMAGE}

# Turn on the headless owner seed and the API-key mint the base entrypoint
# ships (both inert by default).
ENV UOS_SEED_OWNER=true \
    UOS_ADMIN_USER=admin \
    UOS_ADMIN_PASS=admin \
    UOS_COUNTRY=840 \
    UOS_TIMEZONE=UTC \
    UOS_CONSOLE_NAME=unifi-os-sim \
    UOS_SEED_API_KEY=true \
    UOS_API_KEY_NAME=unifi-containers-seeded \
    UOS_API_KEY_FILE=/unifi/api-key

COPY docker-healthcheck-seeded.sh /usr/local/bin/docker-healthcheck-seeded.sh
RUN chmod 755 /usr/local/bin/docker-healthcheck-seeded.sh

HEALTHCHECK --interval=10s --timeout=8s --start-period=10m --retries=6 \
  CMD /usr/local/bin/docker-healthcheck-seeded.sh || exit 1
