"""Prometheus metrics. One registry per process; the API serves it at /metrics, the worker on its
own port (settings.worker_metrics_port), because the worker has no HTTP server otherwise."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

registry = CollectorRegistry()

WEBHOOKS = Counter(
    "reviewly_webhook_requests_total",
    "Webhook deliveries by outcome",
    ["result"],
    registry=registry,
)
WEBHOOK_SECONDS = Histogram(
    "reviewly_webhook_seconds",
    "Time spent handling a webhook (the whole request, ack included)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
    registry=registry,
)
JOBS = Counter(
    "reviewly_jobs_total",
    "Jobs finished by kind and outcome",
    ["kind", "outcome"],
    registry=registry,
)
JOB_SECONDS = Histogram(
    "reviewly_job_seconds",
    "Job run time in the worker (excludes time spent queued)",
    ["kind"],
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300),
    registry=registry,
)
JOB_QUEUE_SECONDS = Histogram(
    "reviewly_job_queue_seconds",
    "Time from webhook receipt to a worker starting the job",
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 900),
    registry=registry,
)
QUEUE_DEPTH = Gauge(
    "reviewly_queue_depth", "Jobs in each queue state", ["state"], registry=registry
)
LLM_CALLS = Counter(
    "reviewly_llm_calls_total",
    "LLM calls by provider, model and outcome",
    ["provider", "model", "outcome"],
    registry=registry,
)
LLM_SECONDS = Histogram(
    "reviewly_llm_seconds",
    "LLM call latency",
    ["provider"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 40, 90),
    registry=registry,
)
LLM_TOKENS = Counter(
    "reviewly_llm_tokens_total",
    "Tokens by provider, model and kind",
    ["provider", "model", "kind"],
    registry=registry,
)
LLM_COST = Counter(
    "reviewly_llm_cost_usd_total",
    "Estimated LLM spend in USD",
    ["provider", "model"],
    registry=registry,
)
CACHE = Counter(
    "reviewly_review_cache_total", "Review cache lookups", ["result"], registry=registry
)
BREAKER_OPEN = Gauge(
    "reviewly_llm_breaker_open",
    "1 while a provider's circuit breaker is not closed",
    ["provider"],
    registry=registry,
)


def render() -> bytes:
    return generate_latest(registry)
