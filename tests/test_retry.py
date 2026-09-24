import pytest

from app.queue.retry import backoff_seconds


def test_first_attempt_floor_is_half_base():
    assert backoff_seconds(1, 4, 100, rng=lambda: 0.0) == 2


def test_first_attempt_ceiling_is_base():
    assert backoff_seconds(1, 4, 100, rng=lambda: 1.0) == 4


@pytest.mark.parametrize("attempt,expected", [(1, 4), (2, 8), (3, 16), (4, 32)])
def test_grows_exponentially(attempt, expected):
    assert backoff_seconds(attempt, 4, 1000, rng=lambda: 1.0) == expected


def test_capped():
    assert backoff_seconds(20, 4, 60, rng=lambda: 1.0) == 60


def test_jitter_stays_in_bounds():
    import random

    r = random.Random(1)
    for attempt in range(1, 8):
        d = backoff_seconds(attempt, 3, 50, rng=r.random)
        ceiling = min(50, 3 * 2 ** (attempt - 1))
        assert ceiling / 2 <= d <= ceiling


def test_jitter_actually_varies():
    values = {backoff_seconds(3, 3, 50, rng=lambda v=v: v) for v in (0.0, 0.3, 0.9)}
    assert len(values) == 3


def test_attempt_zero_does_not_crash_or_go_negative():
    assert backoff_seconds(0, 4, 100, rng=lambda: 0.0) > 0
