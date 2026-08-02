# Seeded variant: a fully-initialized controller — first-run wizard
# already completed with a known local admin — captured as the image's
# initial /unifi content. Docker copies image content at a VOLUME path
# into the fresh volume on first run (the mysql-image pattern), so the
# controller boots straight to a working login with no wizard.
#
# Known credentials: admin / unifi-containers-seeded
#
# seed.tgz is produced at release time by `unifi-containers make-seed` (boot
# the exact base digest, run the wizard, stop it cleanly, snapshot the volume)
# — it is never committed to git.
ARG BASE_IMAGE=ghcr.io/jamesbraid/unifi-network:latest
FROM ${BASE_IMAGE}

ADD seed.tgz /unifi/
RUN chown -R unifi:unifi /unifi

HEALTHCHECK --interval=10s --timeout=20s --start-period=5m --retries=5 \
  CMD ["/usr/bin/python3", "-m", "unifi_runtime.healthcheck", "network-seeded"]
