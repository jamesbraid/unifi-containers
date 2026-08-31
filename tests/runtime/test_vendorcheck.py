"""The vendor-contract check: parsing ubnt-tools and judging the stamp set."""

from unifi_runtime import vendorcheck
from unifi_runtime.entrypoint import uos

TOOLS_5_1_21 = """\
#!/bin/bash
UOS_UUID=$(cat /data/uos_uuid)
PRODUCT_NAME=$(cat /usr/lib/product_name)
APP_MODEL=$(cut -d. -f1 /usr/lib/version)
"""

TOOLS_5_1_37 = """\
#!/bin/bash
UOS_UUID=$(cat /data/uos_uuid)
PRODUCT_NAME=$(cat /usr/lib/product_name)
APP_MODEL=$(cat /usr/lib/app_model)
"""


def test_finds_the_paths_each_release_reads():
    assert vendorcheck.stamp_reads(TOOLS_5_1_21) == [
        "/usr/lib/product_name",
        "/usr/lib/version",
    ]
    assert vendorcheck.stamp_reads(TOOLS_5_1_37) == [
        "/usr/lib/app_model",
        "/usr/lib/product_name",
    ]


def test_the_volume_owned_uuid_is_not_a_stamp():
    assert "/data/uos_uuid" not in vendorcheck.stamp_reads(TOOLS_5_1_37)


def test_write_stamps_satisfies_both_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(uos, "detect_arch", lambda: "arm64")
    (tmp_path / "usr/lib").mkdir(parents=True)
    env = {"APP_MODEL": "UOSSERVER", "APP_VERSION": "5.1.37", "PRODUCT_NAME": "uosserver"}

    uos.write_stamps(env, root=str(tmp_path))

    for script in (TOOLS_5_1_21, TOOLS_5_1_37):
        paths = vendorcheck.stamp_reads(script)
        assert vendorcheck.missing_stamps(paths, root=str(tmp_path)) == []
    assert (tmp_path / "usr/lib/app_model").read_text() == "UOSSERVER\n"
    assert (tmp_path / "usr/lib/version").read_text().startswith("UOSSERVER.")


def test_a_new_unstamped_read_is_named(tmp_path):
    (tmp_path / "usr/lib").mkdir(parents=True)
    drifted = TOOLS_5_1_37 + "FLAVOUR=$(cat /usr/lib/console_flavour)\n"
    paths = vendorcheck.stamp_reads(drifted)
    assert "/usr/lib/console_flavour" in paths
    assert "/usr/lib/console_flavour" in vendorcheck.missing_stamps(paths, root=str(tmp_path))


def test_an_empty_stamp_counts_as_missing(tmp_path):
    (tmp_path / "usr/lib").mkdir(parents=True)
    (tmp_path / "usr/lib/app_model").write_text("\n")
    assert vendorcheck.missing_stamps(["/usr/lib/app_model"], root=str(tmp_path)) == [
        "/usr/lib/app_model"
    ]


def test_shortname_is_read_from_id_output():
    out = (
        "board.sysid=0xae01\nboard.name=uosserver\nboard.shortname=UOSSERVER\nboard.hwrev=0x0000\n"
    )
    assert vendorcheck.reported_shortname(out) == "UOSSERVER"
    assert vendorcheck.reported_shortname("board.shortname=\n") == ""
    assert vendorcheck.reported_shortname("no such line") is None
