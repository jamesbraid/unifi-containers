#!/usr/bin/env bash
# Healthy only when the API answers a real JSON login for the seeded
# admin (admin / unifi-containers-seeded).

SYSPROPS_FILE=${DATADIR}/system.properties
if [ -f "${SYSPROPS_FILE}" ]; then
    SYSPROPS_PORT=$(grep "^unifi.https.port=" "${SYSPROPS_FILE}" | cut -d'=' -f2)
fi
PORT=${SYSPROPS_PORT:-8443}

curl --max-time 5 -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"unifi-containers-seeded"}' \
  "https://localhost:${PORT}/api/login" | grep -q '"rc"[[:space:]]*:[[:space:]]*"ok"'
