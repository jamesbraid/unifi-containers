#!/bin/bash
# Healthy only once the headless owner seed has completed: the UOS ucore API
# on :443 accepts a real JSON login for the seeded admin. That single check
# proves setup finished and the future-proof UOS-native API is usable.
USER=${UOS_ADMIN_USER:-admin}
PASS=${UOS_ADMIN_PASS:-admin}
[ "$(curl -ks --max-time 5 -o /dev/null -w '%{http_code}' \
    -X POST -H 'Content-Type: application/json' \
    -d "{\"username\":\"${USER}\",\"password\":\"${PASS}\"}" \
    https://127.0.0.1/api/auth/login)" = "200" ]
