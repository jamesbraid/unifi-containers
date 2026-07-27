"""The ways extracting the UOS installer can go wrong quietly.

A checksum that is not checked and a missing member that is not noticed both
produce a `uos-base:local` that builds fine and is the wrong thing.
"""

import io
import zipfile

import pytest
from conftest import REPO_ROOT

from unifi_containers import extract_uos as extract
from unifi_containers import pins
from unifi_containers.download import ChecksumError, sha256_of, verify_sha256

PINS = REPO_ROOT / "unifi-os" / "pins.env"

IMAGE_TAR = b"this is definitely a rootfs tarball" * 100
# The installer is an ELF with the zip appended; nothing may depend on the
# archive starting at offset 0.
ELF_PREAMBLE = b"\x7fELF" + bytes(8192)


def make_installer(path, members=None, prepend=ELF_PREAMBLE):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in (members or {"image.tar": IMAGE_TAR}).items():
            archive.writestr(name, data)
    path.write_bytes(prepend + buffer.getvalue())
    return path


def test_image_tar_comes_out_of_a_zip_with_an_elf_bolted_to_the_front(tmp_path):
    installer = make_installer(tmp_path / "installer")
    out = extract.extract_image_tar(installer, tmp_path / "image.tar")
    assert out.read_bytes() == IMAGE_TAR


def test_extracted_image_tar_is_readable(tmp_path):
    # The zip stores it mode 000 and `docker load` has to read it.
    installer = make_installer(tmp_path / "installer")
    out = extract.extract_image_tar(installer, tmp_path / "image.tar")
    assert out.stat().st_mode & 0o400


def test_a_missing_image_tar_fails_and_says_what_was_there(tmp_path):
    installer = make_installer(tmp_path / "installer", {"rootfs.squashfs": b"drifted"})
    with pytest.raises(extract.ExtractError) as caught:
        extract.extract_image_tar(installer, tmp_path / "image.tar")
    assert "image.tar not found" in str(caught.value)
    assert "rootfs.squashfs" in str(caught.value)


def test_an_empty_image_tar_fails(tmp_path):
    installer = make_installer(tmp_path / "installer", {"image.tar": b""})
    with pytest.raises(extract.ExtractError):
        extract.extract_image_tar(installer, tmp_path / "image.tar")


def test_a_checksum_mismatch_names_both_digests(tmp_path):
    artifact = tmp_path / "installer"
    artifact.write_bytes(b"not what was pinned")
    expected = "0" * 64
    with pytest.raises(ChecksumError) as caught:
        verify_sha256(artifact, expected)
    assert expected in str(caught.value)
    assert sha256_of(artifact) in str(caught.value)


def test_a_matching_checksum_passes(tmp_path):
    artifact = tmp_path / "installer"
    artifact.write_bytes(b"exactly what was pinned")
    assert verify_sha256(artifact, sha256_of(artifact)) == sha256_of(artifact)


@pytest.mark.parametrize("arch", ["amd64", "x86_64", "arm64", "aarch64"])
def test_every_supported_arch_resolves_against_the_real_pins(arch):
    url, sha = extract.select(pins.env_values(PINS.read_text()), arch)
    assert url.startswith("https://")
    assert len(sha) == 64


def test_an_unsupported_arch_fails(tmp_path):
    with pytest.raises(extract.ExtractError) as caught:
        extract.select({}, "riscv64")
    assert "riscv64" in str(caught.value)


def test_a_pin_missing_from_pins_env_fails_by_name():
    with pytest.raises(extract.ExtractError) as caught:
        extract.select({"UOS_URL_AMD64": "https://example.invalid"}, "amd64")
    assert "UOS_SHA256_AMD64" in str(caught.value)
