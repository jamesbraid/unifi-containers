#!/bin/bash
# Headless UOS owner seed — the future-proof, UOS-native test-target path.
#
# Completes the UniFi OS first-run setup with no UI account and no cloud/SSO
# by calling unifi-core's own /api/setup (the endpoint the browser wizard
# posts to). The result is an Owner admin usable on the UOS API (:443):
#   POST /api/auth/login {username,password}  ->  200 + session
#
# This is distinct from, and mutually exclusive with, the Network App
# simulation path (see sim/demo-mode): with is_simulation on, the demo
# Network App is already "installed" and /api/setup fails with
# "Network set-installed returned status 401". So this seed runs on a
# non-sim controller and targets the UOS ucore API, not the Network App.
#
# Runs as the uos-seed-owner.service systemd oneshot (After=unifi-core),
# installed by docker-entrypoint.sh when UOS_SEED_OWNER=true. Idempotent.
set -u

USER=${UOS_ADMIN_USER:-admin}
PASS=${UOS_ADMIN_PASS:-admin}
COUNTRY=${UOS_COUNTRY:-840}          # ISO-3166 numeric; 840 = US
TZ=${UOS_TIMEZONE:-UTC}
NAME=${UOS_CONSOLE_NAME:-unifi-os-sim}
API=https://127.0.0.1

# systemd-journald is unreliable in this image, so log to a file on the
# persisted volume (/var/log -> /unifi/logs) as well as stdout — a failed
# seed must stay diagnosable via `docker exec ... cat`.
LOGFILE=/var/log/uos-seed-owner.log
log() { echo "uos-seed-owner: $*" | tee -a "$LOGFILE"; }

login_ok() {
    [ "$(curl -ks -m8 -o /dev/null -w '%{http_code}' \
        -X POST -H 'Content-Type: application/json' \
        -d "{\"username\":\"${USER}\",\"password\":\"${PASS}\"}" \
        "${API}/api/auth/login")" = "200" ]
}

# Wait for the ucore API to answer at all (up to ~10 min).
for _ in $(seq 1 120); do
    curl -ks -m5 -o /dev/null "${API}/api/system" && break
    sleep 5
done

# Idempotent: nothing to do if the owner already logs in.
if login_ok; then
    log "already seeded (login OK) — nothing to do"
    exit 0
fi

payload=$(printf '{"name":"%s","username":"%s","password":"%s","country":%s,"timezone":"%s","updateFirmware":false,"sendDiagnostics":false}' \
    "$NAME" "$USER" "$PASS" "$COUNTRY" "$TZ")

# /api/setup can briefly 4xx/5xx while the Network App finishes coming up;
# retry a few times before giving up.
for attempt in 1 2 3 4 5; do
    code=$(curl -ks -m90 -o /run/uos-seed-owner.resp -w '%{http_code}' \
        -X POST -H 'Content-Type: application/json' -d "$payload" "${API}/api/setup")
    log "/api/setup attempt ${attempt} -> ${code}"
    if [ "$code" = "200" ] && login_ok; then
        log "owner '${USER}' created; UOS API ready on :443"
        exit 0
    fi
    sleep 15
done

log "FAILED to seed owner; last response:"
cat /run/uos-seed-owner.resp 2>/dev/null
exit 1
