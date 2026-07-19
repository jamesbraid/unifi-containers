#!/bin/bash
# Healthy only when the bundled Network Application answers a real JSON
# login for the simulation admin. The integrated app serves its API on
# 127.0.0.1:8081 (plain HTTP) — probing it directly makes the healthcheck
# independent of the optional external proxy port.
curl -ks --max-time 5 -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
  http://127.0.0.1:8081/api/login | grep -q '"rc"[[:space:]]*:[[:space:]]*"ok"'
