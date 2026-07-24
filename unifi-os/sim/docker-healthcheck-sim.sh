#!/bin/bash
# Readiness gate, proven ONCE. The bundled Network Application serves its API
# on 127.0.0.1:8081 (plain HTTP); a real JSON login there is the only probe
# that fails until the app can truly authenticate. A *repeated* login probe
# trips UniFi's login rate-limiter (HTTP 429, Retry-After up to an hour), so
# log in only until the first success, drop a marker, and never log in again.
MARKER=/tmp/unifi-ready
[ -f "${MARKER}" ] && exit 0
curl -ks --max-time 5 -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
  http://127.0.0.1:8081/api/login | grep -q '"rc"[[:space:]]*:[[:space:]]*"ok"' \
  && touch "${MARKER}"
