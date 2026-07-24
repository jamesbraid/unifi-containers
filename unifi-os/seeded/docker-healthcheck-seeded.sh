#!/bin/bash
# Readiness gate, proven ONCE: the UOS ucore API on :443 accepts a real JSON
# login for the seeded admin, which proves the headless owner seed finished and
# the UOS-native API is usable. A *repeated* login probe trips UniFi's login
# rate-limiter (HTTP 429, Retry-After up to an hour), so log in only until the
# first success, drop a marker, and never log in again — thereafter a no-op.
MARKER=/tmp/unifi-ready
[ -f "${MARKER}" ] && exit 0
USER=${UOS_ADMIN_USER:-admin}
PASS=${UOS_ADMIN_PASS:-admin}
[ "$(curl -ks --max-time 5 -o /dev/null -w '%{http_code}' \
    -X POST -H 'Content-Type: application/json' \
    -d "{\"username\":\"${USER}\",\"password\":\"${PASS}\"}" \
    https://127.0.0.1/api/auth/login)" = "200" ] && touch "${MARKER}"
