# unifi-containers

<p align="center">
  <img src="assets/logo.png" alt="unifi-containers — an otter hugging a stack of shipping containers" width="280">
</p>

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
| `ghcr.io/jamesbraid/unifi-network` | 10.6.101 | [Release notes](https://community.ui.com/releases/UniFi-Network-Application-10-6-101/05283624-0980-4dd7-b8d6-9fa5c4e28da4) |
| `ghcr.io/jamesbraid/unifi-os-server` | 5.1.37 | [Release notes](https://community.ui.com/releases) |

## Tags

| Variant | Image tags |
|---|---|
| Base | `X.Y.Z`, `latest` |
| Simulation | `X.Y.Z-sim`, `sim` |
| Seeded | `X.Y.Z-seeded`, `seeded` |

`X.Y.Z` is the upstream version. Build numbers are a git concept and never
appear as an image tag: `X.Y.Z-sim` is the current build of that upstream
version, and it moves when we rebuild the image without an upstream change — a
Dockerfile or healthcheck fix. `latest` / `sim` / `seeded` follow the *highest*
stable upstream version, not the most recent release, so rebuilding an older
version does not drag them backwards.

**For something that never moves, pin the digest** (`image@sha256:…`). Every
other name slides. `docker inspect` reports what you actually have in
`org.opencontainers.image.version` and which commit built it in
`org.opencontainers.image.revision`.

The consequence of everything sliding: a superseded build ends up untagged and
becomes garbage-collectable, so rolling back means cutting a new build rather
than repointing at an old one.

### Cutting a release

Git tags are product-scoped and carry the build number:
`network/10.4.57-1`, `unifi-os/5.1.21-3`.

Only GA upstream versions are built. Ubiquiti publishes release candidates too,
and the community feed cannot tell you which is which — it carries no channel
field at all, so a candidate and a GA release appear identically. The channel is
stated in the firmware API, which is what the updater asks.

That API publishes the same application under two product ids, and the
difference matters:

| product | artifact | says GA is |
|---|---|---|
| `unifi` | the app bundled into UniFi OS | current, and complete |
| `unifi-controller` | the standalone `.deb` these images install | days to weeks behind, and skips versions |

So the **version** comes from `unifi` and the **checksum** from
`unifi-controller` whenever it has caught up to that version — otherwise the
`.deb` is hashed. Reading the version from the `.deb` product instead would park
the pin on a superseded release with nothing to say so: it has no 10.2.x record
at all, though 10.2.105 was GA.

The version comes from the pins, the build number from the tags. So a bump is
automatic — the updater rewrites the pin, and CI notices the pinned version has
no tag yet and cuts build 1. A rebuild is deliberate:

```bash
unifi-containers cut-release --all                      # what is due; no --push, no tag
unifi-containers cut-release network --rebuild --push   # next build of the pinned version
```

Pushing the tag is the whole release trigger: the mirror to GitHub syncs on
push, so a tag reaching the canonical repo reaches the image build by itself.
Nothing in this repo asks anything to synchronise.

Tags from before this scheme are archived under `legacy/` and still
checkout-able. They were not renamed because they could not be: `v10.4.57` and
`v10.4.57-1` are different commits and were different published releases, so
both would have mapped onto `network/10.4.57-1`.

## Simulation mode

The `-sim` tags boot straight into a demo controller: `admin`/`admin`
account, seeded demo sites and devices (3 APs, 1 gateway, 5 switches), no
setup wizard. The image's healthcheck reports healthy only once a real JSON
login succeeds, the v2 API surface answers, and all 9 demo devices are
present — the three things that come up at different times during boot. So
"wait for healthy" is the whole readiness contract:

```bash
docker run -d --name unifi -p 8443:8443 ghcr.io/jamesbraid/unifi-network:sim
# wait for healthy, then:
curl -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' https://localhost:8443/api/login
```

or `TAG=sim docker compose -f network/examples/docker-compose.yml up --wait`.

Simulation fidelity (which writes stick, how stats evolve) is a per-version
empirical question — treat it as a controller-API test double, not a
device-network emulator. `unifi-containers sim-keys <version>` prints the
full simulation/demo key set a release ships, which doubles as the de facto
documentation for it (notably `demo.username` / `demo.password` /
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
build time. Credentials: **`admin` / `unifi-containers-seeded`**

```bash
docker run -d --name unifi -p 8443:8443 ghcr.io/jamesbraid/unifi-network:seeded
# wait for healthy (healthcheck = seeded-login + v2 probes), then the API is yours:
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
# healthcheck = API-key probe + ucore-login probe + v2 probe; then:
curl -ks -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' \
  https://localhost:11443/api/auth/login
```

### The seeded X-API-KEY

The same boot mints an **X-API-KEY** and writes it to **`/unifi/api-key`**.
Production UniFi OS clients authenticate with that header rather than a
cookie, so a harness holding this key drives the real `/proxy/network`
dialect and skips SSO entirely.

Read the key whichever way suits the harness:

```bash
key=$(docker exec <container> cat /unifi/api-key)   # from the container
key=$(cat /path/to/volume/api-key)                  # from a mounted volume

curl -ks -H "X-API-KEY: $key" \
  https://localhost:11443/proxy/network/api/s/default/stat/device
```

The seed records the key in `/unifi/logs/uos-seed-owner.log` and in `docker logs`,
either of which is a second way to fetch it and the first place to look when a
boot goes wrong. That is deliberate: this is a random key on an `admin`/`admin`
test target, so it is not a secret worth hiding. Do not treat these images as
somewhere to keep one.

The path is the contract; the value is not. UniFi OS mints the key itself and
ignores any value a caller supplies, so expect 32 random characters, fresh for
each volume, carrying full admin scope and no expiry. Restarts reuse the same
key. Set `UOS_API_KEY_FILE` or `UOS_API_KEY_NAME` to move or rename it, and
`UOS_SEED_API_KEY=false` to skip minting.

What the key opens:

| Endpoint | With `X-API-KEY` |
|---|---|
| `/proxy/network/api/s/<site>/...` (classic dialect) | works |
| `/proxy/network/integration/v1/...` (integration API) | works |
| `/api/...` (ucore, e.g. `/api/users/self`) | **401** — these need a session |

The console's identity service owns API keys, so mint further ones there:
`POST /api/v2/user/<owner-uuid>/keys` on `127.0.0.1:9080` inside the
container, taking the owner UUID from `GET /api/v2/info`. unifi-core has no
API-key route of its own. The `-sim` variant seeds no key, having no UOS
owner.

## Using from test harnesses

Every image's healthcheck proves the surfaces a harness actually uses are
live, not merely that the port is open: a real JSON login, the v2 API (which
5xxs for a window after login works, while zone-based-firewall defaults
materialize), and on `-sim` the full demo fleet. Waiting for healthy is
therefore sufficient — you should not need a readiness poll of your own:

- **docker compose**: `docker compose up --wait`
- **testcontainers (any language)**: use the container-healthy wait
  strategy, e.g. Go: `wait.ForHealthCheck()`; Java:
  `Wait.forHealthcheck()`. Container reuse across tests works well with the
  sim/seeded variants since state is deterministic.
- **Anything holding only a URL**: `GET :9099/readyz` — **200** once ready,
  **503** until then. Docker will not serve its health verdict over the
  network, so a caller that did not start the container has no way to read
  it. This does. It runs the same probe the healthcheck runs, so it is the
  same verdict, not a second opinion.
- **Startup budgets**, measured to healthy on Apple Silicon / native arm64:
  `unifi-network:sim` 39 s; `unifi-os-server` base 32 s, `-sim` 66 s,
  `-seeded` 78 s. Healthy now also waits for the v2 surface and the demo
  fleet, so on a loaded machine it can land well past these — that wait used
  to happen in your test code instead. Size your own timeout off the CI
  gate's ceilings instead —
  300 s for the network images, 900 s for UOS — because a cold pull and an
  emulated arch both dwarf the boot itself.
- **Colima / Docker Desktop / Podman**: all fine for `unifi-network`.
  `unifi-os-server` additionally needs cgroup v2 and the runtime contract
  below; Colima works out of the box.
  [sysbox-runc](https://github.com/nestybox/sysbox) can run systemd
  containers without the explicit capability list — an option, not a
  requirement.

### The readiness endpoint

Some orchestrators start a container for you and hand back only a URL. CI
services (Woodpecker, GitLab) do it, Kubernetes does it, and none of them can
run `docker inspect`. Those callers used to reimplement readiness against an
API they do not own — typically a login poll, which goes green before the v2
surface and the demo fleet do.

Every image serves the verdict instead:

```bash
curl -fsS http://localhost:9099/readyz     # 200 ready, 503 not yet
until curl -fsS http://host:9099/readyz >/dev/null; do sleep 2; done
```

```yaml
readinessProbe:                              # Kubernetes
  httpGet: { path: /readyz, port: 9099 }
  periodSeconds: 5
  failureThreshold: 60
```

It answers 503 rather than refusing the connection from the moment the
container starts, so "not ready yet" never has to be told apart from "wrong
host". Poll it as fast as you like: the probe underneath runs at most once
every two seconds however many callers ask, because its first stage is a
login and UniFi rate-limits those globally.

Set `READYZ_PORT` to move it, `READYZ_DISABLE=true` to switch it off.

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

The sim healthcheck reports healthy once that login answers `rc: ok`, the v2
API surface stops 5xxing, and the demo fleet is fully populated. Those three
land at different times during boot, so reading `stat/device` or a v2
endpoint the instant the container goes healthy is safe — no poll of your
own. The expected device count follows `DEMO_NUM_UAP` / `DEMO_NUM_UGW` /
`DEMO_NUM_USW`. Set `SIM_EXPECT_DEVICES` to override the total outright.
Note `7443` is plain HTTP: an HTTPS client gets a TLS-handshake error
(`SSL: WRONG_VERSION_NUMBER`), not a redirect.

The `-sim` (Network App demo, with devices) and `-seeded` (UOS-native owner)
paths are **mutually exclusive within one container** — a demo Network App
is already "installed", so unifi-core's `/api/setup` can't drive it. Run the
variant that matches the API surface you're testing.

## Attribution and licensing

- The `network/` entrypoint and healthchecks are written against Ubiquiti's
  own `unifi.init` and `unifi-network-service-helper` from the deb. An
  earlier version was vendored from
  [jacobalberty/unifi-docker](https://github.com/jacobalberty/unifi-docker)
  (MIT); the Ubuntu 24.04 / Java 25 / MongoDB 6 modernization of that build
  is by Joshua Stark
  ([jacobalberty/unifi-docker#903](https://github.com/jacobalberty/unifi-docker/pull/903)).
  Both remain in the git history with their original authorship.
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
