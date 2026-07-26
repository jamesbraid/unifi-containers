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
# With UOS_SEED_API_KEY=true it then mints an X-API-KEY and publishes it at
# UOS_API_KEY_FILE, so a harness can use the production `unifi-os` dialect
# (/proxy/network/... + X-API-KEY) with no SSO at all. See ensure_api_key.
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

SEED_API_KEY=${UOS_SEED_API_KEY:-0}
KEYFILE=${UOS_API_KEY_FILE:-/unifi/api-key}
KEYNAME=${UOS_API_KEY_NAME:-unifi-containers-seeded}
# ULP (ulp-go) owns API keys and trusts localhost — no SSO, cookie or CSRF.
ULP=http://127.0.0.1:9080
# The endpoint an X-API-KEY is proven against: the classic dialect a harness
# actually calls, not a synthetic health route.
KEYPROBE="${API}/proxy/network/api/s/default/stat/device"

# systemd-journald is unreliable in this image, so log to a file on the
# persisted volume (/var/log -> /unifi/logs) as well as stdout — a failed
# seed must stay diagnosable via `docker exec ... cat`.
LOGFILE=/var/log/uos-seed-owner.log
log() { echo "uos-seed-owner: $*" | tee -a "$LOGFILE"; }

login_code() {
    curl -ks -m8 -o /dev/null -w '%{http_code}' \
        -X POST -H 'Content-Type: application/json' \
        -d "{\"username\":\"${USER}\",\"password\":\"${PASS}\"}" \
        "${API}/api/auth/login"
}

login_ok() { [ "$(login_code)" = "200" ]; }

# ULP knows whether first-run setup completed, and answers on localhost with
# no auth and no rate limit. Used as the second opinion when login can't give
# a verdict.
ulp_is_setup() {
    [ "$(curl -s -m5 "${ULP}/api/v2/info" | jq -r '.data.is_setuped // false' 2>/dev/null)" = "true" ]
}

ensure_owner() {
    # Idempotent, and careful about WHY a login failed. UniFi rate-limits
    # logins hard (HTTP 429 AUTHENTICATION_FAILED_LIMIT_REACHED, Retry-After
    # up to an hour), so a non-200 here is not evidence the owner is missing.
    # Treating it as such makes this unit POST /api/setup at an already-set-up
    # console, collect five 500s ("Device is already setup"), and fail the
    # whole seed — turning someone else's login burst into a red healthcheck.
    code=$(login_code)
    if [ "$code" = "200" ]; then
        log "already seeded (login OK) — nothing to do"
        return 0
    fi
    if ulp_is_setup; then
        log "already seeded (login gave HTTP ${code}, ULP reports setup complete)"
        return 0
    fi
    log "owner not present yet (login HTTP ${code}, ULP reports no setup) — seeding"

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
            return 0
        fi
        # unifi-core answers an already-completed setup with 500 "Device is
        # already setup". That is a success signal, not a failure to retry.
        if grep -qi 'already setup' /run/uos-seed-owner.resp 2>/dev/null; then
            log "console reports setup already complete — treating as seeded"
            return 0
        fi
        sleep 15
    done

    log "FAILED to seed owner; last response:"
    cat /run/uos-seed-owner.resp 2>/dev/null
    return 1
}

# 0 = the key authenticates, 1 = the console rejected it, 2 = never got a
# verdict. The distinction matters: a rejection means re-mint, but "the
# Network App is still starting" must NOT be read as a dead key, or every
# slow boot mints a duplicate and leaks the old one.
key_check() {
    for _ in $(seq 1 36); do
        code=$(curl -ks -m8 -o /dev/null -w '%{http_code}' \
            -H "X-API-KEY: $1" "$KEYPROBE")
        case "$code" in
            200)     return 0 ;;
            401|403) log "API key rejected by the console (HTTP ${code})"; return 1 ;;
            *)       sleep 5 ;;
        esac
    done
    log "no verdict on the API key after ~3min (last HTTP ${code}) — ${KEYPROBE} never answered"
    return 2
}

ensure_api_key() {
    if [ -s "$KEYFILE" ]; then
        existing=$(cat "$KEYFILE")
        key_check "$existing"
        case $? in
            0) log "reusing API key from ${KEYFILE}: ${existing}"; return 0 ;;
            2) return 1 ;;
            *) log "minting a replacement" ;;
        esac
    fi

    # ULP learns the owner from /api/setup, but can lag it by a few seconds.
    owner=""
    for _ in $(seq 1 60); do
        owner=$(curl -s -m5 "${ULP}/api/v2/info" \
            | jq -r '.data.owner.unique_id // empty' 2>/dev/null)
        [ -n "$owner" ] && break
        sleep 5
    done
    if [ -z "$owner" ]; then
        log "FAILED: ULP never reported an owner id at ${ULP}/api/v2/info"
        return 1
    fi

    code=$(curl -s -m15 -o /run/uos-seed-api-key.resp -w '%{http_code}' \
        -X POST -H 'Content-Type: application/json' \
        -d "{\"name\":\"${KEYNAME}\"}" "${ULP}/api/v2/user/${owner}/keys")
    key=$(jq -r '.data.full_api_key // empty' /run/uos-seed-api-key.resp 2>/dev/null)
    if [ "$code" != "200" ] || [ -z "$key" ]; then
        log "FAILED to mint API key: POST ${ULP}/api/v2/user/<owner>/keys -> ${code}"
        cat /run/uos-seed-api-key.resp 2>/dev/null
        return 1
    fi

    # A key that mints but does not authenticate is worse than no key: it
    # would publish a credential the harness cannot use. Prove it first.
    if ! key_check "$key"; then
        log "FAILED: minted key does not authenticate ${KEYPROBE}"
        return 1
    fi

    # Atomic publish — a reader must never see a half-written key.
    tmp="${KEYFILE}.tmp.$$"
    if ! { printf '%s\n' "$key" > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$KEYFILE"; }; then
        log "FAILED to write ${KEYFILE}"
        rm -f "$tmp"
        return 1
    fi
    # Logged in full on purpose: this is a random key on an admin/admin test
    # target, so `docker logs` is a legitimate second way to fetch it.
    log "minted API key '${KEYNAME}': ${key} -> ${KEYFILE}"
}

# Wait for the ucore API to answer at all (up to ~10 min).
for _ in $(seq 1 120); do
    curl -ks -m5 -o /dev/null "${API}/api/system" && break
    sleep 5
done

ensure_owner || exit 1

if [ "$SEED_API_KEY" = "true" ] || [ "$SEED_API_KEY" = "1" ]; then
    ensure_api_key || exit 1
fi
