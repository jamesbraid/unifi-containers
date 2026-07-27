"""Runtime code shipped INTO the container images.

Hard rule: stdlib only, Python 3.9 floor — the UniFi OS image carries Debian's
python 3.9.2 with no pip and no venv. tests/test_stdlib_only.py enforces it.
"""
