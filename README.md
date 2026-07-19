# unifi-containers

Version-pinned, multi-arch (amd64 + arm64) OCI images of UniFi controllers,
built as **test targets** for automation: deterministic startup, a health
signal that means "the controller API is answering", no setup wizard in
simulation mode, testcontainers-friendly.

Not production hosting. Runs anywhere standard OCI images run — Docker,
Podman, Colima, Kubernetes.

## Images

| Image | Product |
|---|---|
| `ghcr.io/jamesbraid/unifi-network` | UniFi Network Application (standalone) |
| `ghcr.io/jamesbraid/unifi-os-server` | UniFi OS Server (planned) |

## Current versions

| Image | Version | Release notes |
|---|---|---|
| `ghcr.io/jamesbraid/unifi-network` | 10.4.57 | [Release notes](https://community.ui.com/releases/UniFi-Network-Application-10-4-57/92694b29-fd78-4d52-906a-3211136610e2) |

## Tags

| Variant | Tags |
|---|---|
| Base stable | `X.Y.Z`, `X.Y`, `X`, `latest` |
| Simulation | `X.Y.Z-sim`, `X.Y-sim`, `X-sim`, `sim` |
| Release candidate | `X.Y.Z-rc`, `rc` (base only) |

Sliding tags (`latest`, `sim`, `X`, `X.Y`, …) always point at the highest
published stable version. RC tags never touch them.

## Simulation mode

The `-sim` tags boot straight into a demo controller: `admin`/`admin`
account, seeded demo sites and devices (3 APs, 1 gateway, 5 switches), no
setup wizard. The image's healthcheck only reports healthy once the API
answers a real JSON login, so "wait for healthy" is a reliable readiness
signal:

```bash
docker run -d --name unifi -p 8443:8443 ghcr.io/jamesbraid/unifi-network:sim
# wait for healthy, then:
curl -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' https://localhost:8443/api/login
```

or `TAG=sim docker compose -f network/examples/docker-compose.yml up --wait`.

Simulation fidelity (which writes stick, how stats evolve) is a per-version
empirical question — treat it as a controller-API test double, not a
device-network emulator.

## Attribution and licensing

- The `network/` build is vendored from
  [jacobalberty/unifi-docker](https://github.com/jacobalberty/unifi-docker)
  (MIT). The original copyright notice is retained at
  `network/LICENSE.upstream`.
- The Ubuntu 24.04 / Java 25 / MongoDB 6 modernization is by Joshua Stark
  ([jacobalberty/unifi-docker#903](https://github.com/jacobalberty/unifi-docker/pull/903)),
  carried here with original authorship.
- Images download official Ubiquiti artifacts at build time; artifact URLs
  are pinned and sha256-verified. Nothing Ubiquiti-proprietary is
  redistributed in this repository.
- UniFi is a trademark of Ubiquiti Inc. This project is not affiliated with
  or endorsed by Ubiquiti.

Project license: MIT.
