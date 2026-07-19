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
| `ghcr.io/jamesbraid/unifi-os-server` | UniFi OS Server |

## Current versions

| Image | Version | Release notes |
|---|---|---|
| `ghcr.io/jamesbraid/unifi-network` | 10.4.57 | [Release notes](https://community.ui.com/releases/UniFi-Network-Application-10-4-57/92694b29-fd78-4d52-906a-3211136610e2) |
| `ghcr.io/jamesbraid/unifi-os-server` | 5.1.21 | [Release notes](https://community.ui.com/releases) |

## Tags

| Variant | Tags |
|---|---|
| Base stable | `X.Y.Z`, `X.Y`, `X`, `latest` |
| Simulation | `X.Y.Z-sim`, `X.Y-sim`, `X-sim`, `sim` |
| Seeded (`unifi-network` only) | `X.Y.Z-seeded`, `X.Y-seeded`, `X-seeded`, `seeded` |
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
device-network emulator. The full simulation/demo key set is enumerated
per release in [`docs/sim-keys/`](docs/sim-keys/) — a drift tripwire and
de facto documentation (notably `demo.username` / `demo.password` /
`demo.skip_wizard` / `demo.*_model`).

## Seeded mode (`unifi-network`)

The `-seeded` tags carry a fully-initialized controller: the first-run
wizard is already completed at image build time, so the container boots
straight to a working login — no wizard, no demo devices, real empty
site. Fastest cold start of all variants.

Credentials: **`admin` / `unifi-containers-seeded`**

```bash
docker run -d --name unifi -p 8443:8443 ghcr.io/jamesbraid/unifi-network:seeded
# wait for healthy (healthcheck = seeded-login probe), then the API is yours:
curl -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"unifi-containers-seeded"}' \
  https://localhost:8443/api/login
```

## Using from test harnesses

Every image's healthcheck means "the controller API answers" — so any
healthy-wait strategy is a correct readiness signal:

- **docker compose**: `docker compose up --wait`
- **testcontainers (any language)**: use the container-healthy wait
  strategy, e.g. Go: `wait.ForHealthCheck()`; Java:
  `Wait.forHealthcheck()`. Give it a generous startup timeout (first pull
  + boot); container reuse across tests works well with the sim/seeded
  variants since state is deterministic.
- **Startup budgets** (indicative, Apple Silicon / native arm64; CI gate
  timeouts are the enforced ceiling): seeded ≈ 15 s, sim ≈ 30–40 s,
  UOS sim ≈ 1–5 min to healthy.
- **Colima / Docker Desktop / Podman**: all fine for `unifi-network`.
  For `unifi-os-server`, the runtime needs cgroup v2 and the documented
  capability/cgroup/tmpfs contract (see `unifi-os/examples/`); Colima
  works out of the box. [sysbox-runc](https://github.com/nestybox/sysbox)
  can run systemd containers without the explicit capability list — an
  option, not a requirement.

`ghcr.io/jamesbraid/unifi-os-server` runs the full UOS stack with systemd
as PID 1, which needs an explicit runtime contract — capability list (no
privileged mode), host cgroup namespace with `/sys/fs/cgroup` mounted rw,
and a tmpfs set. `unifi-os/examples/docker-compose.yml` is the complete,
copy-pasteable version of it.

The bundled Network Application serves its API on `127.0.0.1:8081` inside
the container (loopback only, behind UOS SSO externally). Setting
`UOS_NETWORK_DIRECT=true` (default in `-sim` tags) exposes it on port
7443 via a systemd socket proxy, so tests can hit the controller API
directly with no SSO dance:

```bash
TAG=sim docker compose -f unifi-os/examples/docker-compose.yml up --wait
curl -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' http://localhost:7443/api/login
```

The sim healthcheck only reports healthy once that login answers `rc: ok`.

## Attribution and licensing

- The `network/` build is vendored from
  [jacobalberty/unifi-docker](https://github.com/jacobalberty/unifi-docker)
  (MIT). The original copyright notice is retained at
  `network/LICENSE.upstream`.
- The Ubuntu 24.04 / Java 25 / MongoDB 6 modernization is by Joshua Stark
  ([jacobalberty/unifi-docker#903](https://github.com/jacobalberty/unifi-docker/pull/903)),
  carried here with original authorship.
- The `unifi-os/` extraction approach and single-volume entrypoint layout
  are adapted from
  [toquanghieu/unifi-os-server-docker](https://github.com/toquanghieu/unifi-os-server-docker)
  (MIT). The direct-API-port idea is from
  [unihosted/unifi-os-server-docker](https://github.com/unihosted/unifi-os-server-docker)
  (concept only — reimplemented from scratch here);
  [lemker/unifi-os-server](https://github.com/lemker/unifi-os-server)
  (AGPL-3.0) informed the runtime contract, ideas only, no code.
- Images download official Ubiquiti artifacts at build time; artifact URLs
  are pinned and sha256-verified. Nothing Ubiquiti-proprietary is
  redistributed in this repository.
- UniFi is a trademark of Ubiquiti Inc. This project is not affiliated with
  or endorsed by Ubiquiti.

Project license: MIT.
