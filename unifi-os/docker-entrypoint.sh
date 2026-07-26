#!/bin/bash
# Container entrypoint for the UniFi OS Server test-target image.
# Volume layout, UUID persistence, and stamps adapted from
# toquanghieu/unifi-os-server-docker (MIT); the stock image's own
# /root/uos-entrypoint.sh duties are folded in here.
set -e

# --- Single-volume layout ---
# Move service state under /unifi and symlink back, so one volume captures
# everything worth persisting.
declare -A SYMLINK_MAP=(
    ["/data"]="/unifi/data"
    ["/var/lib/mongodb"]="/unifi/db"
    ["/var/lib/unifi"]="/unifi/config"
    ["/var/log"]="/unifi/logs"
    ["/srv"]="/unifi/srv"
    ["/persistent"]="/unifi/persistent"
    ["/etc/rabbitmq/ssl"]="/unifi/rabbitmq-ssl"
    ["/usr/lib/unifi"]="/unifi/app"
)

for ORIG in "${!SYMLINK_MAP[@]}"; do
    TARGET="${SYMLINK_MAP[$ORIG]}"
    mkdir -p "$TARGET"
    if [ -d "$ORIG" ] && [ ! -L "$ORIG" ]; then
        cp -a --no-clobber "$ORIG/." "$TARGET/" 2>/dev/null || true
        rm -rf "$ORIG"
    fi
    mkdir -p "$(dirname "$ORIG")"
    ln -sfn "$TARGET" "$ORIG"
    chmod 755 "$TARGET"
done

# --- First-boot UUID, persisted across restarts ---
if [ ! -f /unifi/data/uos_uuid ]; then
    if [ -z "${UOS_UUID:-}" ]; then
        UOS_UUID=$(sed 's/./5/15' /proc/sys/kernel/random/uuid)
    fi
    echo "$UOS_UUID" > /unifi/data/uos_uuid
fi

# --- Platform / version / product stamps the services read ---
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case "$ARCH" in
    amd64|x86_64) FIRMWARE_PLATFORM=linux-x64 ;;
    arm64|aarch64) FIRMWARE_PLATFORM=arm64 ;;
    *) echo "ERROR: unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
echo "$FIRMWARE_PLATFORM" > /usr/lib/platform
echo "${APP_MODEL}.0000000.${APP_VERSION}.0000000.000000.0000" > /usr/lib/version
echo "${PRODUCT_NAME}" > /usr/lib/product_name

# --- eth0 alias (NET_ADMIN) for setups that provide tap0 ---
if [ ! -d /sys/devices/virtual/net/eth0 ] && [ -d /sys/devices/virtual/net/tap0 ]; then
    ip link add name eth0 link tap0 type macvlan
    ip link set eth0 up
fi

# --- Service dirs the units expect ---
for SPEC in "nginx:nginx:/var/log/nginx" \
            "mongodb:mongodb:/var/log/mongodb" \
            "rabbitmq:rabbitmq:/var/log/rabbitmq"; do
    IFS=':' read -r OWNER GROUP DIR <<< "$SPEC"
    if [ ! -d "$DIR" ]; then
        mkdir -p "$DIR"
        chown "$OWNER:$GROUP" "$DIR"
        chmod 755 "$DIR"
    fi
done
chown -R mongodb:mongodb /var/lib/mongodb 2>/dev/null || true

# --- Opt-in direct (SSO-free) Network API port ---
# The integrated Network Application serves its full API on 127.0.0.1:8081
# (plain HTTP, loopback only). With UOS_NETWORK_DIRECT=true a
# systemd-socket-proxyd unit exposes it on 0.0.0.0:7443 so test harnesses
# can talk to the controller API without the UOS SSO dance. Off by default.
if [ "${UOS_NETWORK_DIRECT:-0}" = "true" ] || [ "${UOS_NETWORK_DIRECT:-0}" = "1" ]; then
    cat > /etc/systemd/system/uos-network-direct.socket <<'EOF'
[Unit]
Description=Direct (SSO-free) UniFi Network API port
[Socket]
ListenStream=7443
[Install]
WantedBy=sockets.target
EOF
    cat > /etc/systemd/system/uos-network-direct.service <<'EOF'
[Unit]
Description=Proxy the direct Network API port to the integrated app
Requires=uos-network-direct.socket
After=uos-network-direct.socket
[Service]
ExecStart=/lib/systemd/systemd-socket-proxyd 127.0.0.1:8081
EOF
    mkdir -p /etc/systemd/system/sockets.target.wants
    ln -sfn /etc/systemd/system/uos-network-direct.socket \
        /etc/systemd/system/sockets.target.wants/uos-network-direct.socket
fi

# --- Opt-in headless owner seed (UOS-native, cloud-free) ---
# With UOS_SEED_OWNER=true a systemd oneshot completes the first-run setup
# after unifi-core is up, via unifi-core's own /api/setup — giving an Owner
# admin on the UOS API (:443) with no UI account and no setup wizard. The
# `-seeded` variant turns this on; the base image stays inert without it.
# UOS_SEED_API_KEY=true adds a second step to the same oneshot: mint an
# X-API-KEY through ULP and publish it at UOS_API_KEY_FILE, so a harness can
# use the production /proxy/network dialect without SSO. It needs the owner,
# hence the nesting.
# systemd services don't inherit the container env, so the resolved values
# go in an EnvironmentFile the unit reads.
if [ "${UOS_SEED_OWNER:-0}" = "true" ] || [ "${UOS_SEED_OWNER:-0}" = "1" ]; then
    mkdir -p /run
    cat > /run/uos-seed-owner.env <<EOF
UOS_ADMIN_USER=${UOS_ADMIN_USER:-admin}
UOS_ADMIN_PASS=${UOS_ADMIN_PASS:-admin}
UOS_COUNTRY=${UOS_COUNTRY:-840}
UOS_TIMEZONE=${UOS_TIMEZONE:-UTC}
UOS_CONSOLE_NAME=${UOS_CONSOLE_NAME:-unifi-os-sim}
UOS_SEED_API_KEY=${UOS_SEED_API_KEY:-0}
UOS_API_KEY_NAME=${UOS_API_KEY_NAME:-unifi-containers-seeded}
UOS_API_KEY_FILE=${UOS_API_KEY_FILE:-/unifi/api-key}
EOF
    cat > /etc/systemd/system/uos-seed-owner.service <<'EOF'
[Unit]
Description=Seed the UOS owner admin headlessly (test target)
After=unifi-core.service
Wants=unifi-core.service
[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=-/run/uos-seed-owner.env
ExecStart=/usr/local/bin/uos-seed-owner.sh
StandardOutput=journal+console
StandardError=journal+console
[Install]
WantedBy=multi-user.target
EOF
    mkdir -p /etc/systemd/system/multi-user.target.wants
    ln -sfn /etc/systemd/system/uos-seed-owner.service \
        /etc/systemd/system/multi-user.target.wants/uos-seed-owner.service
fi

# --- Variant init hooks (e.g. the sim layer) run before systemd ---
if [ -d /usr/local/uos/init.d ]; then
    run-parts /usr/local/uos/init.d 2>/dev/null || run-parts --regex '.*' /usr/local/uos/init.d
fi

# --- Surface key service logs in `docker logs` ---
# systemd routes service output to journald; this tail inherits the
# container's stdout and keeps writing to it after exec replaces the shell.
if [ "${FORWARD_SERVICE_LOGS:-1}" != "0" ]; then
    tail -F -n0 \
        /var/log/mongodb/mongodb.log \
        /usr/lib/unifi/logs/server.log \
        /usr/lib/unifi/logs/unifi-core.log \
        2>/dev/null &
fi

exec /sbin/init
