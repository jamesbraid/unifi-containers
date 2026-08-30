"""Container entrypoint for the UniFi OS Server test-target image.

Runs as PID 1 until the final `execv("/sbin/init")` hands the container to
systemd. Volume layout, UUID persistence and stamps are adapted from
toquanghieu/unifi-os-server-docker (MIT).
"""

import os
import shutil
import subprocess
import sys
from collections import OrderedDict, namedtuple

from .. import readyz
from ..env import SEED_DEFAULTS, is_enabled, setting

INIT = "/sbin/init"
INIT_HOOK_DIR = "/usr/local/uos/init.d"
UUID_FILE = "/unifi/data/uos_uuid"
RANDOM_UUID_SOURCE = "/proc/sys/kernel/random/uuid"

# --- Single-volume layout ---
# Service state moves under /unifi and the original path becomes a symlink,
# so one volume captures everything worth persisting.
SYMLINK_MAP = OrderedDict(
    (
        ("/data", "/unifi/data"),
        ("/var/lib/mongodb", "/unifi/db"),
        ("/var/lib/unifi", "/unifi/config"),
        ("/var/log", "/unifi/logs"),
        ("/srv", "/unifi/srv"),
        ("/persistent", "/unifi/persistent"),
        ("/etc/rabbitmq/ssl", "/unifi/rabbitmq-ssl"),
        ("/usr/lib/unifi", "/unifi/app"),
    )
)

#: Both `dpkg --print-architecture` and `uname -m` spellings, because
#: `detect_arch` falls back from the first to the second.
PLATFORMS = {
    "amd64": "linux-x64",
    "x86_64": "linux-x64",
    "arm64": "arm64",
    "aarch64": "arm64",
}

#: owner, group, directory. Created only when absent, so a restart does not
#: re-chown a directory an operator has adjusted.
SERVICE_LOG_DIRS = (
    ("nginx", "nginx", "/var/log/nginx"),
    ("mongodb", "mongodb", "/var/log/mongodb"),
    ("rabbitmq", "rabbitmq", "/var/log/rabbitmq"),
)

#: Tailed into the container's stdout so `docker logs` shows something;
#: systemd otherwise routes all of this to journald.
TAILED_LOGS = (
    "/var/log/mongodb/mongodb.log",
    "/usr/lib/unifi/logs/server.log",
    "/usr/lib/unifi/logs/unifi-core.log",
)

Relink = namedtuple("Relink", "original target migrate")


# --- symlink plan -----------------------------------------------------


def plan_symlinks(root="/", links=SYMLINK_MAP):
    """What `apply_symlinks` would do, as data. `migrate` marks paths still holding content."""
    prefix = "" if root == "/" else str(root).rstrip("/")
    plans = []
    for original, target in links.items():
        source = prefix + original
        plans.append(
            Relink(
                original=source,
                target=prefix + target,
                migrate=os.path.isdir(source) and not os.path.islink(source),
            )
        )
    return plans


def _chown_like(source, target):
    """Give `target` the uid and gid of `source`. False when it could not be done."""
    try:
        stat = os.lstat(source)
        os.chown(target, stat.st_uid, stat.st_gid, follow_symlinks=False)
    except OSError:
        # Best effort: an owner we cannot reproduce must not stop the boot.
        return False
    return True


def _mirror_ownership(source, target):
    """Give every path under `target` the uid and gid of its counterpart in `source`.

    `shutil` copies mode, times and xattrs but never ownership, so a migration
    built on it lands the whole tree root-owned. That is not cosmetic: the
    services run as their own users. mongod writes /var/log/mongodb/mongodb.log
    and its dbpath, and when it cannot it exits 100, which takes unifi-core's
    /api/system to 500 and leaves the image unhealthy for good.
    """
    if not _chown_like(source, target):
        return
    if os.path.isdir(source) and not os.path.islink(source):
        for entry in os.listdir(source):
            _mirror_ownership(os.path.join(source, entry), os.path.join(target, entry))


def _copy_no_clobber(source, target):
    """`cp -a --no-clobber source/. target/`: the volume's copy has been running, so it wins."""
    for entry in os.listdir(source):
        src = os.path.join(source, entry)
        dst = os.path.join(target, entry)
        if os.path.lexists(dst):
            continue
        if os.path.islink(src):
            os.symlink(os.readlink(src), dst)
        elif os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
        _mirror_ownership(src, dst)


def apply_symlinks(plans):
    """Create the targets, migrate content once, and relink."""
    for plan in plans:
        existed = os.path.isdir(plan.target)
        os.makedirs(plan.target, exist_ok=True)
        if plan.migrate and not existed:
            # The directory itself, not just what moves into it: mongod's dbpath
            # is a symlink to this path and it has to be writable by `mongodb`.
            # os.makedirs would leave it root-owned.
            _chown_like(plan.original, plan.target)
        if plan.migrate:
            try:
                _copy_no_clobber(plan.original, plan.target)
            except OSError:
                # A partial copy is survivable; refusing to boot over it is
                # not.
                pass
            shutil.rmtree(plan.original, ignore_errors=True)
        os.makedirs(os.path.dirname(plan.original) or "/", exist_ok=True)
        if os.path.lexists(plan.original):
            os.remove(plan.original)
        os.symlink(plan.target, plan.original)
        os.chmod(plan.target, 0o755)


# --- stamps -----------------------------------------------------------


def version_stamp(app_model, app_version):
    """The `/usr/lib/version` line the stock services parse; the format is the vendor's."""
    return f"{app_model}.0000000.{app_version}.0000000.000000.0000"


def firmware_platform(arch):
    """Map a machine architecture to the vendor's platform name. Raises rather than guessing."""
    try:
        return PLATFORMS[arch]
    except KeyError:
        raise ValueError(f"unsupported architecture: {arch}") from None


def detect_arch():
    try:
        out = subprocess.run(
            ["dpkg", "--print-architecture"], capture_output=True, text=True, check=False
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except OSError:
        pass
    return os.uname().machine


# --- first-boot UUID --------------------------------------------------


def force_uuid_version_5(uuid):
    """Rewrite the version nibble to 5: the kernel emits a 4, but UOS console ids are v5."""
    return uuid[:14] + "5" + uuid[15:] if len(uuid) > 14 else uuid


def ensure_uuid(env, path=UUID_FILE, source=RANDOM_UUID_SOURCE):
    """Persist a console UUID on first boot. Returns the value in force."""
    if os.path.exists(path):
        with open(path) as handle:
            return handle.read().strip()
    value = env.get("UOS_UUID") or ""
    if not value:
        with open(source) as handle:
            value = force_uuid_version_5(handle.read().strip())
    os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
    with open(path, "w") as handle:
        handle.write(value + "\n")
    return value


# --- optional systemd units -------------------------------------------

#: env flag -> the units it turns on. The unit files are static and COPY'd in by
#: the Dockerfile, so enabling is all that is left to decide.
#:
#: uos-network-direct.service is absent on purpose: it has no `[Install]` and is
#: started by socket activation.
UNIT_FLAGS = (
    ("UOS_NETWORK_DIRECT", ("uos-network-direct.socket",)),
    ("UOS_SEED_OWNER", ("uos-seed-owner.service",)),
    ("UOS_READYZ", ("uos-readyz.service",)),
)

SEED_OWNER_ENV_PATH = "/run/uos-seed-owner.env"

READYZ_ENV_PATH = "/run/uos-readyz.env"

#: What the readyz unit needs from the container environment. Absent keys are
#: left out so the module's own defaults apply.
READYZ_KEYS = (readyz.PROBE_VAR, readyz.PORT_VAR)


def _quote(value):
    """Quote `value` so systemd hands the unit back the string we were given.

    systemd parses an EnvironmentFile shell-like: unquoted whitespace splits,
    and backslashes, quotes and `$` are all significant. The healthcheck reads
    the same settings straight from the container's environment, with no such
    processing, so anything systemd rewrites makes the two disagree about a
    password or a console name — and a disagreement there is a container that
    never goes healthy rather than one that fails.

    Single quotes, because systemd treats their contents literally. A value
    containing one is closed out and re-opened, the way a shell does it.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def seed_owner_env(env):
    """Resolve the seed settings into EnvironmentFile text."""
    return "".join(f"{key}={_quote(setting(key, env))}\n" for key in SEED_DEFAULTS)


def write_seed_owner_env(env, path=SEED_OWNER_ENV_PATH):
    """`docker run -e` reaches the seed unit through this file and nothing else.

    A unit inherits none of the container's environment.
    """
    with open(path, "w") as handle:
        handle.write(seed_owner_env(env))
    return path


def readyz_env(env):
    """Resolve the readiness endpoint's settings into EnvironmentFile text."""
    return "".join(f"{key}={_quote(env[key])}\n" for key in READYZ_KEYS if env.get(key))


def write_readyz_env(env, path=READYZ_ENV_PATH):
    """`docker run -e READYZ_PORT=...` reaches the unit through this file alone.

    A unit inherits none of the container's environment, the same reason the
    seed unit has one.
    """
    with open(path, "w") as handle:
        handle.write(readyz_env(env))
    return path


def enable_units(env, units=UNIT_FLAGS):
    """Make each unit's state match its env flag. Returns the units left enabled.

    Disabling matters as much as enabling: the sim and seeded Dockerfiles enable
    their unit at build time, so without this a flag set to `false` at
    `docker run` would leave the unit running anyway — and the seed unit would
    then run with no EnvironmentFile while the healthcheck still expected a key,
    which never goes healthy.

    Runs before `exec /sbin/init`, which is fine: enable/disable only read
    `[Install]` and write or remove a `.wants` symlink, needing no live manager.
    """
    enabled = []
    for flag, names in units:
        wanted = is_enabled(env.get(flag))
        for name in names:
            # Exit status is not evidence in this image: `systemctl restart
            # unifi` reports success here and does nothing. The symlink on disk
            # is the thing to check.
            subprocess.call(["systemctl", "enable" if wanted else "disable", name])
            if wanted:
                enabled.append(name)
    return enabled


# --- the rest of the boot ---------------------------------------------


def write_stamp(path, value):
    with open(path, "w") as handle:
        handle.write(value + "\n")


def ensure_eth0_alias():
    """Give setups that provide tap0 an eth0 the services will accept."""
    if os.path.isdir("/sys/devices/virtual/net/eth0"):
        return
    if not os.path.isdir("/sys/devices/virtual/net/tap0"):
        return
    subprocess.check_call(["ip", "link", "add", "name", "eth0", "link", "tap0", "type", "macvlan"])
    subprocess.check_call(["ip", "link", "set", "eth0", "up"])


def ensure_service_dirs(specs=SERVICE_LOG_DIRS):
    for owner, group, directory in specs:
        if os.path.isdir(directory):
            continue
        os.makedirs(directory, exist_ok=True)
        subprocess.call(["chown", f"{owner}:{group}", directory])
        os.chmod(directory, 0o755)
    # Best effort: the mongodb account may not exist in a stripped image.
    subprocess.call(
        ["chown", "-R", "mongodb:mongodb", "/var/lib/mongodb"], stderr=subprocess.DEVNULL
    )


def run_init_hooks(directory=INIT_HOOK_DIR):
    """Run variant hooks before systemd. A failing hook aborts the boot deliberately."""
    if not os.path.isdir(directory):
        return
    if subprocess.call(["run-parts", directory], stderr=subprocess.DEVNULL) == 0:
        return
    # Debian's run-parts skips names it dislikes; --regex '.*' is the escape
    # hatch. This one keeps its stderr, because its failure stops the
    # container.
    rc = subprocess.call(["run-parts", "--regex", ".*", directory])
    if rc != 0:
        raise RuntimeError(f"init hooks in {directory} failed (rc={rc})")


def start_log_tail(env, logs=TAILED_LOGS):
    """Surface key service logs in `docker logs`; the child keeps our stdout across execv."""
    if env.get("FORWARD_SERVICE_LOGS", "1") == "0":
        return None
    return subprocess.Popen(["tail", "-F", "-n0", *logs], stderr=subprocess.DEVNULL)


def main(env=None):
    env = os.environ if env is None else env

    apply_symlinks(plan_symlinks())
    ensure_uuid(env)

    write_stamp("/usr/lib/platform", firmware_platform(detect_arch()))
    write_stamp(
        "/usr/lib/version", version_stamp(env.get("APP_MODEL", ""), env.get("APP_VERSION", ""))
    )
    # Both spellings of the model, because ubnt-tools changed sources between
    # releases: 5.1.21 parsed the model out of /usr/lib/version's first field,
    # 5.1.37 reads /usr/lib/app_model outright. An empty model is fatal as of
    # unifi-core 5.1.132 ("Unsupported console model"), which crash-loops and
    # never serves :443.
    write_stamp("/usr/lib/app_model", env.get("APP_MODEL", ""))
    write_stamp("/usr/lib/product_name", env.get("PRODUCT_NAME", ""))

    ensure_eth0_alias()
    ensure_service_dirs()
    if is_enabled(env.get("UOS_SEED_OWNER")):
        write_seed_owner_env(env)
    if is_enabled(env.get("UOS_READYZ")):
        write_readyz_env(env)
    enable_units(env)
    run_init_hooks()
    start_log_tail(env)

    os.execv(INIT, [INIT])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError) as exc:
        # The two failures we can phrase better than a traceback can: an
        # unsupported architecture and a failed init hook. Anything else
        # keeps its traceback, which is what you want at 3am.
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
