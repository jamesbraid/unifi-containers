#!/usr/bin/env bash
# wait-healthy.sh <container> [timeout-seconds]
# Poll a container's healthcheck; exit 0 on healthy, 1 on stop/timeout.
set -euo pipefail
name=$1
timeout=${2:-600}
start=$(date +%s)
while true; do
  status=$(docker inspect --format '{{.State.Health.Status}}' "$name" 2>/dev/null || echo missing)
  running=$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null || echo false)
  elapsed=$(( $(date +%s) - start ))
  if [ "$status" = healthy ]; then
    echo "healthy after ${elapsed}s"
    exit 0
  fi
  if [ "$running" != true ]; then
    echo "container stopped before becoming healthy (status=$status)"
    docker logs "$name" 2>&1 | tail -50
    exit 1
  fi
  if [ "$elapsed" -ge "$timeout" ]; then
    echo "timed out after ${timeout}s (status=$status)"
    docker logs "$name" 2>&1 | tail -50
    exit 1
  fi
  sleep 5
done
