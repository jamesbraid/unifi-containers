"""Tests for scripts/ci_docker.py — the poll logic, without Docker."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_docker  # noqa: E402


@pytest.mark.parametrize("health,running,elapsed,timeout,expected", [
    ("healthy", True, 5, 600, ci_docker.HEALTHY),
    # Healthy wins over an expired deadline: it passed, whatever the clock says.
    ("healthy", True, 900, 600, ci_docker.HEALTHY),
    ("starting", False, 5, 600, ci_docker.DIED),
    ("missing", False, 5, 600, ci_docker.DIED),
    ("starting", True, 600, 600, ci_docker.TIMEOUT),
    ("starting", True, 601, 600, ci_docker.TIMEOUT),
    ("starting", True, 5, 600, ci_docker.WAIT),
    ("unhealthy", True, 5, 600, ci_docker.WAIT),
])
def test_classify(health, running, elapsed, timeout, expected):
    assert ci_docker.classify(health, running, elapsed, timeout) == expected


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def drive(states, timeout=600, poll=5):
    """Run wait_healthy over a scripted sequence of inspect results."""
    clock = FakeClock()
    lines = []
    calls = iter(states)

    def inspect(_name):
        return next(calls)

    def sleep(seconds):
        clock.now += seconds

    result = ci_docker.wait_healthy(
        "c", timeout=timeout, poll=poll, inspect=inspect, sleep=sleep,
        clock=clock, logs=lambda name, lines=50: "LOGS", out=lines.append,
    )
    return result, lines


def test_wait_healthy_returns_true_once_healthy():
    result, lines = drive([
        ("starting", True), ("starting", True), ("healthy", True),
    ])
    assert result is True
    assert lines == ["healthy after 10s"]


def test_wait_healthy_reports_a_container_that_died():
    result, lines = drive([("starting", True), ("exited", False)])
    assert result is False
    assert "container stopped before becoming healthy (status=exited)" in lines
    assert "LOGS" in lines


def test_wait_healthy_times_out_and_dumps_logs():
    # 5s poll, 12s budget: two waits, then elapsed hits the deadline.
    result, lines = drive([("starting", True)] * 5, timeout=12, poll=5)
    assert result is False
    assert "timed out after 12s (status=starting)" in lines
    assert "LOGS" in lines


def test_wait_healthy_dumps_logs_for_a_missing_container():
    result, lines = drive([("missing", False)])
    assert result is False
    assert "container stopped before becoming healthy (status=missing)" in lines
