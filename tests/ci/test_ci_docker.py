"""Tests for unifi_containers.docker — the poll logic, without Docker."""

from unifi_containers import docker


def drive(states, timeout=600):
    """Run wait_healthy over a scripted sequence of inspect results."""
    now = 0.0
    lines = []
    calls = iter(states)

    def sleep(seconds):
        nonlocal now
        now += seconds

    result = docker.wait_healthy(
        "c",
        timeout=timeout,
        inspect=lambda _name: next(calls),
        sleep=sleep,
        clock=lambda: now,
        logs=lambda _name: "LOGS",
        out=lines.append,
    )
    return result, lines


def test_wait_healthy_returns_true_once_healthy():
    result, lines = drive([("starting", True), ("starting", True), ("healthy", True)])
    assert result is True
    assert lines == ["healthy after 10s"]


def test_wait_healthy_reports_a_container_that_died():
    result, lines = drive([("starting", True), ("exited", False)])
    assert result is False
    assert "container stopped before becoming healthy (status=exited)" in lines
    assert "LOGS" in lines


def test_wait_healthy_accepts_a_container_that_went_healthy_past_the_deadline():
    # Healthy wins over an expired deadline: it passed, whatever the clock says.
    result, lines = drive([("starting", True), ("healthy", True)], timeout=5)
    assert result is True
    assert lines == ["healthy after 5s"]


def test_wait_healthy_times_out_and_dumps_logs():
    # 5s poll, 12s budget: three waits, then elapsed hits the deadline.
    result, lines = drive([("starting", True)] * 5, timeout=12)
    assert result is False
    assert "timed out after 12s (status=starting)" in lines
    assert "LOGS" in lines
