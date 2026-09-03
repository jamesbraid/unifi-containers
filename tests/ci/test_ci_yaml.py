"""Every CI file parses, and no mapping has a duplicate key.

A duplicate key is the failure mode this exists for. `yaml.safe_load` keeps the
last one silently, so "it parses" proves nothing; GitHub rejects the file
outright, and a duplicate `if:` here would have failed every network release
without anything in the pipeline seeing it. actionlint catches this for
.github/workflows; the one workflow directory serves both servers.
"""

from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT

CI_FILES = sorted(
    [
        *(REPO_ROOT / ".github" / "workflows").glob("*.yml"),
    ]
)


class _NoDuplicates(yaml.SafeLoader):
    """SafeLoader that refuses a mapping with a repeated key instead of keeping the last."""


def _mapping(loader, node, deep=False):
    seen = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise AssertionError(f"duplicate key {key!r} at {key_node.start_mark}")
        seen[key] = loader.construct_object(value_node, deep=deep)
    return seen


_NoDuplicates.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def test_there_are_ci_files_to_check():
    # An empty glob would make every assertion below vacuously true. One
    # directory serves both servers now; six workflows live there.
    assert len(CI_FILES) >= 6


@pytest.mark.parametrize("path", CI_FILES, ids=lambda p: str(Path(p).relative_to(REPO_ROOT)))
def test_no_duplicate_keys(path):
    yaml.load(path.read_text(), Loader=_NoDuplicates)


def test_the_loader_would_actually_catch_one():
    # Guard against the assertion above passing because the constructor is not
    # wired up: plain safe_load accepts this and keeps the last value.
    duplicated = "steps:\n  a: 1\nsteps:\n  b: 2\n"
    assert yaml.safe_load(duplicated) == {"steps": {"b": 2}}
    with pytest.raises(AssertionError, match="duplicate key"):
        yaml.load(duplicated, Loader=_NoDuplicates)
