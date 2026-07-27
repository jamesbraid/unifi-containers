"""The sim variant's simulation-mode seed.

The interesting property is `only_if_absent`: this hook runs on every boot,
and the Network App rewrites `system.properties` itself.
"""

from unifi_runtime import sysprops
from unifi_runtime.entrypoint import demo


def test_defaults():
    assert demo.demo_settings({}) == {
        "is_simulation": "true",
        "demo.num_uap": "3",
        "demo.num_ugw": "1",
        "demo.num_usw": "5",
    }


def test_device_counts_are_overridable():
    settings = demo.demo_settings({"DEMO_NUM_UAP": "20", "DEMO_NUM_USW": "0"})
    assert settings["demo.num_uap"] == "20"
    assert settings["demo.num_usw"] == "0"
    assert settings["demo.num_ugw"] == "1"


def test_writes_the_keys_into_a_missing_file(tmp_path):
    path = tmp_path / "unifi" / "system.properties"
    demo.apply(path=str(path), env={})
    assert sysprops.parse(path.read_text())["is_simulation"] == "true"


def test_a_restart_does_not_stomp_what_the_app_changed(tmp_path):
    # The app is entitled to rewrite these; a second boot must leave them.
    path = tmp_path / "system.properties"
    path.write_text("# hand-written\ndemo.num_uap=99\nunifi.https.port=8443\n")

    demo.apply(path=str(path), env={})

    text = path.read_text()
    assert "# hand-written" in text
    assert sysprops.parse(text)["demo.num_uap"] == "99"
    assert sysprops.parse(text)["unifi.https.port"] == "8443"
    assert sysprops.parse(text)["is_simulation"] == "true"


def test_applying_twice_changes_nothing(tmp_path):
    path = tmp_path / "system.properties"
    demo.apply(path=str(path), env={})
    once = path.read_text()
    demo.apply(path=str(path), env={})
    assert path.read_text() == once
