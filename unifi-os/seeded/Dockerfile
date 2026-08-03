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
# Small context, unlike the base image: this layer turns two flags on, and
# inherits PYTHONPATH and the shipped package from the base.
#
# Build against a published base tag:
#   docker build --build-arg BASE_IMAGE=ghcr.io/jamesbraid/unifi-os-server:5.1.21 \
#     -t unifi-os-server:5.1.21-seeded unifi-os/seeded/
ARG BASE_IMAGE=ghcr.io/jamesbraid/unifi-os-server:latest
FROM ${BASE_IMAGE}

# The two flags that make this variant what it is. Kept as ENV so the seed unit
# and the healthcheck read the same values, and `docker inspect` shows them.
ENV UOS_SEED_OWNER=true \
    UOS_SEED_API_KEY=true \
    READYZ_PROBE=seeded

# This variant always seeds, so the unit is enabled at build time.
RUN systemctl enable uos-seed-owner.service

HEALTHCHECK --interval=10s --timeout=20s --start-period=10m --retries=6 \
  CMD ["/usr/bin/python3", "-m", "unifi_runtime.healthcheck", "seeded"]
