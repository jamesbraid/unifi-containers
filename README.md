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
| Base | `X.Y.Z-N`, `latest` |
| Simulation | `X.Y.Z-N-sim`, `sim` |
| Seeded | `X.Y.Z-N-seeded`, `seeded` |
| Release candidate | `X.Y.Z-rc`, `rc` (base only) |

`N` is the packaging revision (RPM Release / Debian debian_revision): it
starts at `1` for each new upstream version and bumps when the image is
rebuilt without an upstream change (a Dockerfile or healthcheck fix). Pin an
exact `X.Y.Z-N` for immutability. The `latest` / `sim` / `seeded` / `rc`
pointers slide to the highest published stable (RC only touches `rc`); the
per-major (`X`) and per-minor (`X.Y`) sliding tags are gone.

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

Two things about the demo fleet worth knowing before you write assertions
against it. Demo device **models randomize per boot** and some drawn models
are un-adoptable (e.g. legacy `BZ2LR`), so a fleet is not deterministic
across restarts — pin models via the `demo.*_model` keys if a test needs
stable hardware. And demo devices arrive **pending-adoption** (no `_id`)
until a test adopts them; the pool does not refill, so treat sim containers
as **per-session disposable** rather than reusing one across many
adopt-heavy runs.

## Seeded mode

The `-seeded` tags carry a controller whose first-run setup is already
completed, so the container boots straight to a working login — no wizard,
no demo devices, real empty site. The two products seed different API
surfaces (see below), so their credentials differ.

**`unifi-network:seeded`** — the Network App wizard is completed at image
build time. Fastest cold start of all variants.
Credentials: **`admin` / `unifi-containers-seeded`**

```bash
docker run -d --name unifi -p 8443:8443 ghcr.io/jamesbraid/unifi-network:seeded
# wait for healthy (healthcheck = seeded-login probe), then the API is yours:
curl -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"unifi-containers-seeded"}' \
  https://localhost:8443/api/login
```

**`unifi-os-server:seeded`** — the UOS first-run setup is completed
headlessly at first boot via unifi-core's own `/api/setup` (no UI account,
no cloud/SSO), giving an **Owner** admin on the UOS-native API (`:443`).
This is the future-proof surface: it works regardless of the bundled
Network App. It seeds no demo devices — for fake devices on the Network App
API use `-sim` (the two setup paths are mutually exclusive; see below).
Credentials: **`admin` / `admin`** (override with `UOS_ADMIN_USER` /
`UOS_ADMIN_PASS`; also `UOS_COUNTRY`, `UOS_TIMEZONE`).

```bash
TAG=seeded docker compose -f unifi-os/examples/docker-compose.yml up --wait
# healthcheck = ucore-login probe; then:
curl -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
  https://localhost:11443/api/auth/login
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

UniFi OS exposes **two independent API surfaces**, each with its own admin,
and the two test variants target one each:

- **UOS ucore API** (`:443`, HTTPS) — the OS itself (`/api/auth/login`,
  `/api/users/self`, settings, backups, updates). The future-proof surface.
  The `-seeded` variant seeds an Owner here headlessly. The **platform**
  version is readable pre-auth at `GET /api/system` → `firmwareVersion`
  (e.g. `5.1.21`) — no login needed. Don't conflate it with the Network App
  version below: on `-sim`, `7443` reports the *Network App* version
  (`10.4.57` for UOS 5.1.21), not the platform's.
- **Network Application API** (`127.0.0.1:8081` loopback; behind UOS SSO
  externally) — the classic UniFi controller API that go-unifi and
  terraform-provider-unifi target today. `UOS_NETWORK_DIRECT=true` (default
  in `-sim`) exposes it on port **7443 as plain HTTP** via a systemd socket
  proxy, so tests hit it directly with no SSO dance:

```bash
TAG=sim docker compose -f unifi-os/examples/docker-compose.yml up --wait
curl -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' http://localhost:7443/api/login
```

The sim healthcheck only reports healthy once that login answers `rc: ok`.
Healthy means the API answers — the demo device fleet finishes populating a
few seconds *after* that, so a test that reads `stat/device` the instant the
container goes healthy may see an incomplete set; poll for the count you need.
Note `7443` is plain HTTP: an HTTPS client gets a TLS-handshake error
(`SSL: WRONG_VERSION_NUMBER`), not a redirect.

The `-sim` (Network App demo, with devices) and `-seeded` (UOS-native owner)
paths are **mutually exclusive within one container** — a demo Network App
is already "installed", so unifi-core's `/api/setup` can't drive it. Run the
variant that matches the API surface you're testing.

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
