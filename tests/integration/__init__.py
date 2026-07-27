# Load-bearing, despite being empty: without it pytest imports this
# directory's conftest.py under the top-level name `conftest`, which replaces
# tests/conftest.py in sys.modules and breaks the `from conftest import
# REPO_ROOT` that tests/runtime relies on. With it, this conftest is
# `integration.conftest` and the two coexist.
