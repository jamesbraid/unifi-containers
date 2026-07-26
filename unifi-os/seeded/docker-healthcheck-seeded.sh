#!/bin/bash
# Readiness gate, proven ONCE: the seeded X-API-KEY authenticates the classic
# /proxy/network dialect, and the UOS ucore API on :443 accepts a real JSON
# login for the seeded admin. Together those prove both seed steps finished and
# both surfaces a harness uses are live. A *repeated* login probe trips UniFi's
# login rate-limiter (HTTP 429, Retry-After up to an hour), so log in only until
# the first success, drop a marker, and never log in again — thereafter a no-op.
#
# Checks run cheapest-first, with the rate-limited one last: file, then key,
# then login. Healthy therefore means the published key works, not merely that
# a file exists.
MARKER=/tmp/unifi-ready
[ -f "${MARKER}" ] && exit 0

KEYFILE=${UOS_API_KEY_FILE:-}
if [ -n "${KEYFILE}" ]; then
    # Unset KEYFILE means the key seed is off (base image, or someone disabled
    # it); the login check alone is then the whole gate.
    [ -s "${KEYFILE}" ] || exit 1
    [ "$(curl -ks --max-time 5 -o /dev/null -w '%{http_code}' \
        -H "X-API-KEY: $(cat "${KEYFILE}")" \
        https://127.0.0.1/proxy/network/api/s/default/stat/device)" = "200" ] || exit 1
fi

USER=${UOS_ADMIN_USER:-admin}
PASS=${UOS_ADMIN_PASS:-admin}
[ "$(curl -ks --max-time 5 -o /dev/null -w '%{http_code}' \
    -X POST -H 'Content-Type: application/json' \
    -d "{\"username\":\"${USER}\",\"password\":\"${PASS}\"}" \
    https://127.0.0.1/api/auth/login)" = "200" ] && touch "${MARKER}"
