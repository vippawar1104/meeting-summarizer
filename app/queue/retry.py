import random
from collections.abc import Callable


def backoff_seconds(
    attempt: int,
    base: float,
    cap: float,
    rng: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with "equal jitter": half fixed, half random.

    Keeps a floor so retries never fire immediately, while still spreading a burst of failures
    (e.g. an LLM outage) so they do not all retry in the same instant.
    """
    ceiling = min(cap, base * 2 ** max(attempt - 1, 0))
    return float(ceiling / 2 + rng() * ceiling / 2)
