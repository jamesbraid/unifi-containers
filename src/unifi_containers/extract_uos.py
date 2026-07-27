"""Extract the UOS installer's embedded OCI base image and load it as uos-base:local.

The installer is an ELF binary with a ZIP appended, so zipfile finds the central
directory from the end and ignores the ELF. Derived from
toquanghieu/unifi-os-server-docker (MIT).
"""

import platform
import shutil
import tempfile
import zipfile
from pathlib import Path

from unifi_containers import docker
from unifi_containers import pins as pinfile
from unifi_containers.download import download, verify_sha256

#: src/unifi_containers/ -> the repo root, where the image directories live.
PINS = Path(__file__).resolve().parents[2] / "unifi-os" / "pins.env"
MEMBER = "image.tar"
LOCAL_REPO = "uos-base"
LOCAL_TAG = "local"

ARCHES = {
    "amd64": "AMD64",
    "x86_64": "AMD64",
    "arm64": "ARM64",
    "aarch64": "ARM64",
}


class ExtractError(RuntimeError):
    """The installer is not shaped the way the extraction expects."""


def select(pins, arch):
    """(url, sha256) for an architecture name."""
    suffix = ARCHES.get(arch)
    if suffix is None:
        raise ExtractError(f"unsupported arch: {arch}")
    try:
        return pins[f"UOS_URL_{suffix}"], pins[f"UOS_SHA256_{suffix}"]
    except KeyError as exc:
        raise ExtractError(f"{exc.args[0]} missing from pins.env") from exc


def extract_image_tar(installer, dest):
    """Copy image.tar out of the installer. The zip stores mode 000, so the umask applies."""
    with zipfile.ZipFile(str(installer)) as archive:
        try:
            info = archive.getinfo(MEMBER)
        except KeyError as exc:
            raise ExtractError(
                f"{MEMBER} not found in the installer — format drift? "
                f"members: {', '.join(archive.namelist()[:10]) or 'none'}"
            ) from exc
        if info.file_size == 0:
            raise ExtractError(f"{MEMBER} is empty in the installer")
        with archive.open(info) as source, open(str(dest), "wb") as out:
            shutil.copyfileobj(source, out, 1 << 20)
    return dest


def extract(arch=None, pins_path=PINS):
    arch = arch or platform.machine()
    pins = pinfile.env_values(Path(pins_path).read_text())
    url, sha = select(pins, arch)
    version = pins.get("UOS_VERSION", "unknown")

    with tempfile.TemporaryDirectory() as work:
        installer = Path(work) / "installer"
        print(f"==> downloading UOS {version} ({arch}) installer")
        download(url, installer)
        verify_sha256(installer, sha)

        print(f"==> extracting embedded {MEMBER}")
        image_tar = extract_image_tar(installer, Path(work) / MEMBER)

        print("==> loading into docker")
        image = docker.load_image(image_tar)
        loaded = image.tags[0] if image.tags else image.short_id
        image.tag(LOCAL_REPO, LOCAL_TAG)

    print(f"==> {LOCAL_REPO}:{LOCAL_TAG} <- {loaded} (UOS {version}, {arch})")
    return 0
