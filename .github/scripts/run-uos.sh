#!/usr/bin/env bash
# run-uos.sh <container-name> <image> [extra docker-run args...]
# Start a UniFi OS Server container with the documented runtime contract:
# systemd PID 1 needs the explicit cap list (no privileged mode), host
# cgroupns with /sys/fs/cgroup rw, and the tmpfs set.
set -euo pipefail
name=$1
image=$2
shift 2
docker run -d --name "$name" \
  --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --cap-drop ALL \
  --cap-add SYS_ADMIN --cap-add NET_ADMIN --cap-add NET_RAW \
  --cap-add NET_BIND_SERVICE --cap-add DAC_OVERRIDE --cap-add DAC_READ_SEARCH \
  --cap-add FOWNER --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
  --cap-add KILL --cap-add SYS_CHROOT --cap-add SYS_PTRACE \
  --cap-add SYS_RESOURCE --cap-add AUDIT_WRITE --cap-add MKNOD \
  --tmpfs /run:exec --tmpfs /run/lock --tmpfs /tmp:exec \
  --tmpfs /var/lib/journal --tmpfs /var/opt/unifi/tmp:size=64m \
  "$@" "$image"
