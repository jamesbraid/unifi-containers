"""Enumerate the Network Application's simulation/demo configuration keys.

The drift signal for a release that renames or drops a `demo.*` key. The scan
covers every jar the deb ships under `lib/`, because the keys have moved
between jars across releases.
"""

import io
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from debian import debfile
from debian.arfile import ArError

from unifi_containers.download import download

DEB_URL = "https://dl.ui.com/unifi/{version}/unifi_sysvinit_all.deb"
LIB_DIR = "usr/lib/unifi/lib"
KEY_RE = re.compile(rb"is_simulation|demo\.[a-z0-9_]+")


class ExtractionError(RuntimeError):
    """The deb is not shaped the way the scan expects."""


def scan(blob):
    """Every key literal in a blob of bytes."""
    return {match.group(0).decode("ascii") for match in KEY_RE.finditer(blob)}


def keys_in_jar(blob, name="<jar>", warn=None):
    """Every key literal across a jar's decompressed entries."""
    found = set()
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as jar:
            for info in jar.infolist():
                if not info.is_dir():
                    found |= scan(jar.read(info))
    except (zipfile.BadZipFile, RuntimeError) as exc:
        # An unreadable jar is not a reason to abandon the other twenty.
        if warn:
            warn(f"warning: skipping unreadable {name}: {exc}")
    return found


def keys_in_data_tar(archive, warn=None):
    """Scan every jar under the unifi lib directory of an opened data tarball."""
    found = set()
    jars = 0
    for member in archive:
        name = member.name.removeprefix("./")
        if not member.isfile() or not name.endswith(".jar"):
            continue
        if not name.startswith(LIB_DIR + "/"):
            continue
        jars += 1
        handle = archive.extractfile(member)
        if handle is None:
            continue
        with handle:
            found |= keys_in_jar(handle.read(), name, warn)
    if not jars:
        raise ExtractionError(f"no jars under {LIB_DIR} in the deb's data archive")
    return found


def keys_in_deb(deb_path, warn=None):
    """Scan the jars in a .deb. python-debian reads the ar wrapper and the compression."""
    try:
        archive = debfile.DebFile(str(deb_path))
    except (ArError, debfile.DebError) as exc:
        raise ExtractionError(f"{deb_path} is not a readable .deb: {exc}") from exc
    try:
        return keys_in_data_tar(archive.data.tgz(), warn)
    finally:
        archive.close()


def collect(version, deb_path=None, out=print):
    """Download the release deb if needed, scan it, return sorted keys."""
    with tempfile.TemporaryDirectory() as work:
        if deb_path is None:
            deb_path = Path(work) / "unifi.deb"
            url = DEB_URL.format(version=version)
            out(f"==> downloading {url}")
            download(url, deb_path)
        keys = keys_in_deb(deb_path, lambda message: print(message, file=sys.stderr))
    if not keys:
        raise ExtractionError("no simulation keys found — extraction drift?")
    return sorted(keys)
