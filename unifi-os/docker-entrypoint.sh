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
