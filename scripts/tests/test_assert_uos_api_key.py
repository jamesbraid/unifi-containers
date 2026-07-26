"""Tests for scripts/assert-uos-api-key.py (imported via importlib —
the filename has dashes)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))  # the script imports ci_docker

_spec = importlib.util.spec_from_file_location(
    "assert_uos_api_key", _SCRIPTS / "assert-uos-api-key.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

NAME = "unifi-containers-seeded"


def ulp_list(*names):
    return json.dumps({
        "code": 1,
        "codeS": "SUCCESS",
        "data": [{"id": f"id-{i}", "name": n, "masked_api_key": "ABCD***WXYZ"}
                 for i, n in enumerate(names)],
    })


def test_counts_only_keys_with_the_seeded_name():
    payload = ulp_list(NAME, "someone-elses-key", NAME)
    assert gate.count_keys_named(payload, NAME) == 2


def test_counts_one_for_a_freshly_seeded_console():
    assert gate.count_keys_named(ulp_list(NAME), NAME) == 1


def test_counts_zero_when_the_list_is_empty():
    assert gate.count_keys_named(ulp_list(), NAME) == 0


@pytest.mark.parametrize("payload", [
    "",
    None,
    "not json at all",
    json.dumps([1, 2, 3]),               # valid JSON, wrong shape
    json.dumps({"data": "not a list"}),
    json.dumps({"code": 404}),           # no data key
    json.dumps({"data": ["bare string"]}),
])
def test_unparseable_or_odd_bodies_count_zero(payload):
    # Counting zero turns into "expected 1, found 0", which reads better as a
    # gate failure than a traceback.
    assert gate.count_keys_named(payload, NAME) == 0


def test_explain_status_calls_out_a_failed_request():
    message = gate.explain_status("000", "200", "classic dialect")
    assert "never completed" in message
    assert "unreachable" in message


def test_explain_status_reports_the_mismatch():
    message = gate.explain_status("401", "200", "classic dialect")
    assert "got HTTP 401" in message
    assert "expected 200" in message


class Recorder:
    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)


def test_absent_check_passes_when_no_key_file_exists(monkeypatch):
    monkeypatch.setattr(gate.ci_docker, "read_file", lambda *_: None)
    out = Recorder()
    assert gate.check_absent("c", "/unifi/api-key", gate.Gate(out)) is True
    assert any("inert" in line for line in out.lines)


def test_absent_check_fails_when_a_key_leaked_into_the_image(monkeypatch):
    monkeypatch.setattr(gate.ci_docker, "read_file", lambda *_: "somekey\n")
    out = Recorder()
    assert gate.check_absent("c", "/unifi/api-key", gate.Gate(out)) is False
    assert any("should not seed a key" in line for line in out.lines)
