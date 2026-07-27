"""The sim-key scan, end to end, against a .deb built in tmp_path.

The whole value of the scan is that its output changes when the keys change. An
extraction that quietly finds nothing, or that stops looking in the jar the keys
moved to, turns a drift signal into a list that always agrees with itself.
"""

import io
import lzma
import tarfile
import zipfile

import pytest

from unifi_containers import simkeys

# Keys embedded the way they appear in a class file: surrounded by binary
# noise, length-prefixed, with near-misses that must not match.
CLASS_BLOB = (
    b"\xca\xfe\xba\xbe\x00\x00\x00\x41\x00\x1f\x08\x00"
    b"\x00\x0ddemo.num_uap\x08\x00\x0dis_simulation"
    b"\x07\x00\x09demo.mode\x00DEMO.MODE\x00demo.CamelCase\x00"
    b"\x00demo.\x00xdemo.usw_model\x00"
)


def jar(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def data_tarball(files):
    """A `./`-prefixed data.tar.xz, the way dpkg writes one."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, blob in files.items():
            info = tarfile.TarInfo("./" + name)
            info.size = len(blob)
            archive.addfile(info, io.BytesIO(blob))
    return lzma.compress(raw.getvalue())


def ar_member(name, data):
    header = (
        name.ljust(16)
        + "0".ljust(12)
        + "0".ljust(6)
        + "0".ljust(6)
        + "100644".ljust(8)
        + str(len(data)).ljust(10)
        + "`\n"
    )
    assert len(header) == 60
    return header.encode() + data + (b"\n" if len(data) % 2 else b"")


def deb(tmp_path, files, name="unifi.deb", member="data.tar.xz"):
    path = tmp_path / name
    path.write_bytes(
        b"!<arch>\n"
        + ar_member("debian-binary", b"2.0\n")
        + ar_member("control.tar.xz", lzma.compress(b"x" * 101))
        + ar_member(member, data_tarball(files))
    )
    return path


def test_scan_finds_the_keys_in_binary_noise_and_nothing_else():
    # Exact equality is the point: CLASS_BLOB also carries DEMO.MODE,
    # demo.CamelCase and a bare `demo.`, none of which is a key.
    assert simkeys.scan(CLASS_BLOB) == {
        "demo.num_uap",
        "is_simulation",
        "demo.mode",
        "demo.usw_model",
    }


def test_keys_come_from_every_entry_in_a_jar():
    blob = jar(
        {
            "com/ubnt/A.class": CLASS_BLOB,
            "com/ubnt/B.class": b"\x00demo.skip_wizard\x00",
            "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n",
        }
    )
    assert "demo.skip_wizard" in simkeys.keys_in_jar(blob)


def test_an_unreadable_jar_is_skipped_not_fatal():
    warnings = []
    assert simkeys.keys_in_jar(b"not a zip", "broken.jar", warnings.append) == set()
    assert any("broken.jar" in line for line in warnings)


def test_jars_are_found_wherever_the_release_moved_them(tmp_path):
    # 10.x relocated the keys from lib/ace.jar into lib/internal/core.jar.
    path = deb(
        tmp_path,
        {
            "usr/lib/unifi/lib/internal/core.jar": jar({"A.class": CLASS_BLOB}),
            "usr/lib/unifi/lib/ace.jar": jar({"B.class": b"\x00demo.mode\x00"}),
        },
    )
    assert "demo.num_uap" in simkeys.keys_in_deb(path)


def test_jars_outside_the_lib_directory_are_ignored(tmp_path):
    path = deb(
        tmp_path,
        {
            "usr/lib/unifi/lib/ace.jar": jar({"A.class": b"\x00demo.mode\x00"}),
            "usr/share/other/vendor.jar": jar({"B.class": CLASS_BLOB}),
        },
    )
    assert simkeys.keys_in_deb(path) == {"demo.mode"}


def test_a_deb_with_no_jars_fails_rather_than_reporting_nothing(tmp_path):
    path = deb(tmp_path, {"usr/lib/unifi/readme.txt": b"hello"})
    with pytest.raises(simkeys.ExtractionError, match="no jars"):
        simkeys.keys_in_deb(path)


def test_collect_reads_a_whole_deb(tmp_path):
    path = deb(tmp_path, {"usr/lib/unifi/lib/internal/core.jar": jar({"A.class": CLASS_BLOB})})
    assert simkeys.collect("10.4.57", path, out=lambda _: None) == [
        "demo.mode",
        "demo.num_uap",
        "demo.usw_model",
        "is_simulation",
    ]


@pytest.mark.parametrize("member", ["data.tar.xz", "data.tar.xz/"])
def test_both_ar_name_conventions_are_read(tmp_path, member):
    # dpkg pads short names with spaces; GNU ar terminates them with a slash.
    path = deb(
        tmp_path,
        {"usr/lib/unifi/lib/ace.jar": jar({"A": CLASS_BLOB})},
        name=f"m{len(member)}.deb",
        member=member,
    )
    assert "is_simulation" in simkeys.keys_in_deb(path)


def test_a_file_that_is_not_an_ar_archive_fails(tmp_path):
    path = tmp_path / "not.deb"
    path.write_bytes(b"PK\x03\x04 this is a zip")
    with pytest.raises(simkeys.ExtractionError, match="not a readable"):
        simkeys.keys_in_deb(path)


def test_a_deb_without_a_data_member_fails(tmp_path):
    path = tmp_path / "control-only.deb"
    path.write_bytes(b"!<arch>\n" + ar_member("debian-binary", b"2.0\n"))
    with pytest.raises(simkeys.ExtractionError, match="not a readable"):
        simkeys.keys_in_deb(path)
