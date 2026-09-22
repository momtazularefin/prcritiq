"""Tests for the demo rate limit, driven by a fake clock."""

from __future__ import annotations

import pytest

from prcritiq.ratelimit import SlidingWindowLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_requests_within_the_limit_are_allowed() -> None:
    limiter = SlidingWindowLimiter(2, clock=Clock())

    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is None


def test_the_next_request_is_told_how_long_to_wait() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(2, clock=clock)
    limiter.acquire("a")
    clock.now += 20
    limiter.acquire("a")

    assert limiter.acquire("a") == 40


def test_the_window_slides_rather_than_resetting() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(2, clock=clock)
    limiter.acquire("a")
    clock.now += 30
    limiter.acquire("a")
    clock.now += 31

    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None


def test_clients_are_limited_separately() -> None:
    limiter = SlidingWindowLimiter(1, clock=Clock())
    limiter.acquire("a")

    assert limiter.acquire("b") is None


def test_idle_clients_are_pruned() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(1, clock=clock)
    for index in range(1100):
        limiter.acquire(f"client-{index}")
    clock.now += 61

    limiter.acquire("late")

    assert len(limiter._hits) < 1100


def test_a_limit_below_one_is_refused() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(0)
