# USD per 1M tokens (input, output). These are list prices recorded when this was written and MUST
# be verified against each provider's pricing page before any cost number is reported as fact.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "mistral-large-latest": (2.00, 6.00),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Unknown models cost 0.0 rather than a guess, so a missing entry is visible, not invented."""
    inp, out = PRICES.get(model, (0.0, 0.0))
    return (prompt_tokens * inp + completion_tokens * out) / 1_000_000
