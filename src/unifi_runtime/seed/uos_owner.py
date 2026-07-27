"""Headless UOS owner seed — the UOS-native test-target path.

Completes UniFi OS first-run setup with no UI account and no cloud/SSO via
unifi-core's own `/api/setup`. Mutually exclusive with the sim `demo-mode`
hook: with `is_simulation` on, `/api/setup` fails 401.
"""

import os
import sys
import time
from collections import namedtuple

from ..env import is_enabled, setting
from ..unifi.ucore import Ucore
from ..unifi.ulp import Ulp

LOGFILE = "/var/log/uos-seed-owner.log"

#: key_check verdicts. REJECTED and NO_VERDICT are deliberately different:
#: only a rejection justifies minting a replacement key.
KEY_OK = 0
KEY_REJECTED = 1
KEY_NO_VERDICT = 2

#: Wait for the ucore API to answer at all: 120 x 5s, ~10 min.
API_WAIT_ATTEMPTS = 120
#: Prove a key against the real endpoint: 36 x 5s, ~3 min.
KEY_CHECK_ATTEMPTS = 36
#: ULP learns the owner from /api/setup but lags it: 60 x 5s, ~5 min.
OWNER_WAIT_ATTEMPTS = 60
POLL_SECONDS = 5
#: /api/setup can briefly 4xx/5xx while the Network App finishes coming up.
SETUP_ATTEMPTS = 5
SETUP_RETRY_SECONDS = 15

Config = namedtuple(
    "Config", ("username password country timezone console_name seed_api_key key_file key_name")
)


def _country(value):
    """ISO-3166 numeric as a JSON *number*: the wire format is `"country":840`, not `"840"`."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def config_from_env(env=None):
    env = os.environ if env is None else env
    return Config(
        username=setting("UOS_ADMIN_USER", env),
        password=setting("UOS_ADMIN_PASS", env),
        country=_country(setting("UOS_COUNTRY", env)),
        timezone=setting("UOS_TIMEZONE", env),
        console_name=setting("UOS_CONSOLE_NAME", env),
        seed_api_key=is_enabled(env.get("UOS_SEED_API_KEY")),
        key_file=setting("UOS_API_KEY_FILE", env),
        key_name=setting("UOS_API_KEY_NAME", env),
    )


def make_log(path=LOGFILE, out=print):
    """Log to stdout and to the persisted volume: journald is unreliable in this image."""

    def log(message):
        line = f"uos-seed-owner: {message}"
        out(line)
        try:
            with open(path, "a") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    return log


def wait_for_api(ucore, log, sleep=time.sleep):
    """Poll /api/system until it answers anything at all."""
    for _ in range(API_WAIT_ATTEMPTS):
        if ucore.system().answered:
            return True
        sleep(POLL_SECONDS)
    log(f"ucore API never answered at {ucore.base_url}/api/system")
    return False


def ensure_owner(ucore, ulp, cfg, log, sleep=time.sleep):
    """Complete first-run setup, unless it already is. Idempotent.

    UniFi rate-limits logins hard (429, Retry-After up to an hour), so a
    non-200 login is not evidence the owner is missing; ULP is the tiebreaker.
    """
    code = ucore.login_status(cfg.username, cfg.password)
    if code == 200:
        log("already seeded (login OK) — nothing to do")
        return True
    if ulp.is_setup():
        log(f"already seeded (login gave HTTP {code}, ULP reports setup complete)")
        return True
    log(f"owner not present yet (login HTTP {code}, ULP reports no setup) — seeding")

    # Bound before the loop: the log line below is a failed seed's only
    # diagnostic, and a NameError there would replace it with a traceback.
    response = None
    for attempt in range(1, SETUP_ATTEMPTS + 1):
        response = ucore.setup(
            cfg.console_name, cfg.username, cfg.password, country=cfg.country, timezone=cfg.timezone
        )
        log(f"/api/setup attempt {attempt} -> {response.status}")
        if response.status == 200 and ucore.login_status(cfg.username, cfg.password) == 200:
            log(f"owner '{cfg.username}' created; UOS API ready on :443")
            return True
        # unifi-core answers an already-completed setup with 500 "Device is
        # already setup". That is a success signal, not a retry condition.
        if "already setup" in response.text.lower():
            log("console reports setup already complete — treating as seeded")
            return True
        if attempt < SETUP_ATTEMPTS:
            sleep(SETUP_RETRY_SECONDS)

    last = response.text.strip() if response is not None else "(never attempted)"
    log(f"FAILED to seed owner; last response: {last}")
    return False


def key_check(ucore, key, log, sleep=time.sleep, attempts=KEY_CHECK_ATTEMPTS):
    """Tri-state: KEY_OK / KEY_REJECTED / KEY_NO_VERDICT.

    Only a clear 401/403 is a rejection. "Still starting" must not read as a
    dead key, or every slow boot mints a duplicate and leaks the old one.
    """
    code = None
    for _ in range(attempts):
        code = ucore.api_key_status(key)
        if code == 200:
            return KEY_OK
        if code in (401, 403):
            log(f"API key rejected by the console (HTTP {code})")
            return KEY_REJECTED
        sleep(POLL_SECONDS)
    log(
        f"no verdict on the API key after {attempts} attempts "
        f"(last HTTP {code}) — the key probe never answered"
    )
    return KEY_NO_VERDICT


def publish_key(path, key):
    """Write the key where the harness reads it, atomically: no reader may see a partial key."""
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as handle:
            handle.write(key + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def _read_key(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return ""


def wait_for_owner_id(ulp, log, sleep=time.sleep):
    for _ in range(OWNER_WAIT_ATTEMPTS):
        owner = ulp.owner_id()
        if owner:
            return owner
        sleep(POLL_SECONDS)
    log(f"FAILED: ULP never reported an owner id at {ulp.base_url}/api/v2/info")
    return None


def ensure_api_key(ucore, ulp, cfg, log, sleep=time.sleep):
    """Publish a working X-API-KEY at cfg.key_file. Idempotent."""
    existing = _read_key(cfg.key_file)
    if existing:
        verdict = key_check(ucore, existing, log, sleep=sleep)
        if verdict == KEY_OK:
            log(f"reusing API key from {cfg.key_file}: {existing}")
            return True
        if verdict == KEY_NO_VERDICT:
            # No verdict is not a rejection. Fail loudly and keep the key.
            return False
        log("minting a replacement")

    owner = wait_for_owner_id(ulp, log, sleep=sleep)
    if not owner:
        return False

    key = ulp.mint_key(owner, cfg.key_name)
    if not key:
        log(f"FAILED to mint API key: POST {ulp.base_url}/api/v2/user/<owner>/keys")
        return False

    # A key that mints but does not authenticate is worse than no key: it
    # would publish a credential the harness cannot use. Prove it first.
    if key_check(ucore, key, log, sleep=sleep) != KEY_OK:
        log("FAILED: minted key does not authenticate the key probe")
        return False

    if not publish_key(cfg.key_file, key):
        log(f"FAILED to write {cfg.key_file}")
        return False

    # Logged in full on purpose: this is a random key on an admin/admin test
    # target, so `docker logs` is a legitimate second way to fetch it.
    log(f"minted API key '{cfg.key_name}': {key} -> {cfg.key_file}")
    return True


def run(cfg, ucore=None, ulp=None, log=None, sleep=time.sleep):
    ucore = Ucore() if ucore is None else ucore
    ulp = Ulp() if ulp is None else ulp
    log = make_log() if log is None else log

    wait_for_api(ucore, log, sleep=sleep)
    if not ensure_owner(ucore, ulp, cfg, log, sleep=sleep):
        return 1
    if cfg.seed_api_key and not ensure_api_key(ucore, ulp, cfg, log, sleep=sleep):
        return 1
    return 0


def main(env=None):
    return run(config_from_env(env))


if __name__ == "__main__":
    sys.exit(main())
