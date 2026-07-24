#!/usr/bin/env bash
# Readiness gate, proven ONCE. A real JSON login is the only probe that fails
# until the controller can truly authenticate: during early boot it serves an
# HTML 200 placeholder on every path, and even /status reports "up" ~30s before
# login works. But a *repeated* login probe trips UniFi's login rate-limiter
# (HTTP 429, Retry-After up to an hour), so log in only until the first success,
# drop a marker, and never log in again. After the marker exists this is a
# no-op: "was ready once, container still running".
MARKER=/tmp/unifi-ready
[ -f "${MARKER}" ] && exit 0

SYSPROPS_FILE=${DATADIR}/system.properties
if [ -f "${SYSPROPS_FILE}" ]; then
    SYSPROPS_PORT=$(grep "^unifi.https.port=" "${SYSPROPS_FILE}" | cut -d'=' -f2)
fi
PORT=${SYSPROPS_PORT:-8443}

curl --max-time 5 -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
  "https://localhost:${PORT}/api/login" | grep -q '"rc"[[:space:]]*:[[:space:]]*"ok"' \
  && touch "${MARKER}"
