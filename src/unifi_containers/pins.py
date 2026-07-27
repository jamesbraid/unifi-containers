"""What version each product pins: a Dockerfile build arg for Network, an env file for UOS."""

import io
import re
from pathlib import Path

from dotenv import dotenv_values

#: `ARG PKGURL=https://dl.ui.com/unifi/10.4.57/unifi_sysvinit_all.deb`
#:
#: Two patterns because the updater rewrites the whole line and the release gate
#: reads a version out of it. Both live here so the URL the updater writes is by
#: construction one the gate can still parse.
PKGURL_RE = re.compile(r"^ARG PKGURL=(\S+)$", re.MULTILINE)
VERSION_IN_URL_RE = re.compile(r"/unifi/(\d+\.\d+\.\d+)/")

NETWORK_DOCKERFILE = "network/Dockerfile"
UOS_PINS = "unifi-os/pins.env"
UOS_KEY = "UOS_VERSION"


def pkgurl_version(dockerfile_text):
    """The Network version a Dockerfile pins, or None."""
    url = PKGURL_RE.search(dockerfile_text or "")
    version = VERSION_IN_URL_RE.search(url.group(1)) if url else None
    return version.group(1) if version else None


def env_values(text):
    """Every assignment in a shell-style env file. The file stays Dockerfile-sourceable."""
    return dict(dotenv_values(stream=io.StringIO(text or "")))


def pinned_version(product, repo_root="."):
    """The version `product` pins on disk, or None. Raises for an unknown product."""
    root = Path(repo_root)
    if product == "network":
        return pkgurl_version((root / NETWORK_DOCKERFILE).read_text())
    if product == "unifi-os":
        return env_values((root / UOS_PINS).read_text()).get(UOS_KEY) or None
    raise ValueError(f"unknown product: {product!r}")
