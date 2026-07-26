"""Tests for scripts/sync-mirror.py (imported via importlib — the filename
has dashes)."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "sync_mirror", Path(__file__).resolve().parents[1] / "sync-mirror.py"
)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

FULL = {
    "FORGEJO_TOKEN": "t0ken",
    "CI_FORGE_URL": "https://git.example.dev",
    "CI_REPO": "infra/unifi-containers",
}


def test_complete_environment_has_nothing_missing():
    assert sync.missing_env(FULL) == []


def test_empty_token_counts_as_missing():
    # The failure that motivated this script: Woodpecker substituted the
    # secret away, leaving "Authorization: token " and a bare 404.
    assert sync.missing_env({**FULL, "FORGEJO_TOKEN": ""}) == ["FORGEJO_TOKEN"]


def test_absent_variables_are_all_reported():
    assert sync.missing_env({}) == ["FORGEJO_TOKEN", "CI_FORGE_URL", "CI_REPO"]


def test_sync_url_is_the_forgejo_endpoint():
    assert sync.sync_url("https://git.example.dev", "infra/unifi-containers") \
        == "https://git.example.dev/api/v1/repos/infra/unifi-containers/push_mirrors-sync"


def test_sync_url_tolerates_a_trailing_slash():
    assert sync.sync_url("https://git.example.dev/", "o/r") \
        == "https://git.example.dev/api/v1/repos/o/r/push_mirrors-sync"


@pytest.mark.parametrize("env", [{}, {**FULL, "CI_REPO": ""}])
def test_main_refuses_to_run_without_the_environment(env, capsys):
    assert sync.main(env) == 1
    assert "missing or empty" in capsys.readouterr().err
