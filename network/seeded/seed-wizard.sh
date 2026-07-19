#!/usr/bin/env bash
# seed-wizard.sh <base-url> <admin-user> <admin-pass>
# Complete a fresh controller's first-run wizard through the API, leaving
# a fully-initialized installation with a known local admin. Used to
# produce the -seeded images; also usable against any fresh container.
set -euo pipefail

base=$1
user=$2
pass=$3

curlj() {
    curl -ksf --max-time 10 -H 'Content-Type: application/json' "$@"
}

echo "==> waiting for controller"
for _ in $(seq 1 60); do
    curl -ks --max-time 3 "$base/status" | grep -q '"up"[[:space:]]*:[[:space:]]*true' && break
    sleep 5
done

echo "==> creating default admin"
curlj -X POST -d "{\"cmd\":\"add-default-admin\",\"name\":\"${user}\",\"email\":\"admin@example.invalid\",\"x_password\":\"${pass}\"}" \
    "$base/api/cmd/sitemgr"

echo "==> naming the installation"
curlj -X POST -d '{"name":"unifi-containers-seeded"}' \
    "$base/api/set/setting/super_identity" || true

echo "==> marking setup complete"
curlj -X POST -d '{"cmd":"set-installed"}' "$base/api/cmd/system"

echo "==> verifying login"
curlj -X POST -d "{\"username\":\"${user}\",\"password\":\"${pass}\"}" \
    "$base/api/login" | grep -q '"rc"[[:space:]]*:[[:space:]]*"ok"'
echo "==> seeded: ${user} login ok"
