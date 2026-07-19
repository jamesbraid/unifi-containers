#!/usr/bin/env bash
# Healthy only when the API answers a real JSON login for the simulation
# admin. During early boot the controller serves an HTML placeholder with
# HTTP 200 on every path, so a bare port/status probe (like the base
# image's healthcheck) reports ready before the API actually works.

SYSPROPS_FILE=${DATADIR}/system.properties
if [ -f "${SYSPROPS_FILE}" ]; then
    SYSPROPS_PORT=$(grep "^unifi.https.port=" "${SYSPROPS_FILE}" | cut -d'=' -f2)
fi
PORT=${SYSPROPS_PORT:-8443}

curl --max-time 5 -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
  "https://localhost:${PORT}/api/login" | grep -q '"rc"[[:space:]]*:[[:space:]]*"ok"'
