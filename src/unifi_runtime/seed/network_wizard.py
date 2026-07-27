"""Complete a fresh controller's first-run wizard through the API.

Lives here, on the image floor, so the same code seeds a controller from the
host at release time and from inside a container.
"""

import time

from ..unifi.network import NetworkApp, rc_of

SEED_IDENTITY = "unifi-containers-seeded"

#: The account the seed creates. The -seeded healthcheck logs in with these, so
#: they are one constant rather than two that have to agree.
SEED_USER = "admin"
SEED_PASS = SEED_IDENTITY

WAIT_ATTEMPTS = 60
WAIT_SECONDS = 5


class WizardError(RuntimeError):
    """The controller refused a step the seed cannot continue without."""


def wait_up(app, sleep=time.sleep):
    """Poll `/status` until the controller claims to be up."""
    for _ in range(WAIT_ATTEMPTS):
        if app.is_up():
            return True
        sleep(WAIT_SECONDS)
    return False


def seed(base_url, username, password, identity=SEED_IDENTITY, out=print, sleep=time.sleep):
    """Run the wizard. Raises WizardError at the first refusal."""
    app = NetworkApp(base_url)

    out("==> waiting for controller")
    if not wait_up(app, sleep=sleep):
        # Not fatal on its own: /status reports up several seconds before the
        # API can authenticate, so its absence is a hint, and the steps below
        # are the real verdict.
        out("warning: /status never reported up; trying the wizard anyway")

    out("==> creating default admin")
    response = app.add_default_admin(username, password)
    if not response.ok:
        raise WizardError(f"add-default-admin -> HTTP {response.status}: {response.text[:300]}")

    out("==> naming the installation")
    if not app.set_super_identity(identity).ok:
        # Cosmetic. A seeded image with the default name is still a seeded
        # image, so this has never been worth failing a release over.
        out("warning: could not set the installation name")

    out("==> marking setup complete")
    response = app.set_installed()
    if not response.ok:
        raise WizardError(f"set-installed -> HTTP {response.status}: {response.text[:300]}")

    out("==> verifying login")
    response = app.login(username, password)
    if rc_of(response.json()) != "ok":
        raise WizardError(
            f"the seeded admin cannot log in -> HTTP {response.status}: {response.text[:300]}"
        )
    out(f"==> seeded: {username} login ok")
