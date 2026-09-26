"""OpenTelemetry tracing, off unless REVIEWLY_OTLP_ENDPOINT is set.

`span()` is always safe to call: with no exporter configured it is a no-op tracer. Traces are
linked across the webhook -> queue -> worker hop by the correlation id (an attribute on every
span), not by W3C context propagation: the job row does not carry a traceparent. Search a trace
backend for `correlation_id` to see one PR's whole path.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from opentelemetry import trace

from app.core.logging import correlation_id

log = structlog.get_logger()
_tracer = trace.get_tracer("reviewly")
_configured = False


def configure_tracing(endpoint: str | None, service: str) -> bool:
    """Install an OTLP exporter once. Returns whether tracing is now exporting."""
    global _configured
    if not endpoint or _configured:
        return _configured
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    global _tracer
    _tracer = trace.get_tracer("reviewly")
    _configured = True
    log.info("tracing_enabled", endpoint=endpoint, service=service)
    return True


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    with _tracer.start_as_current_span(name) as sp:
        if (cid := correlation_id.get()) != "-":
            sp.set_attribute("correlation_id", cid)
        for key, value in attributes.items():
            if value is not None:
                sp.set_attribute(key, value)
        try:
            yield sp
        except Exception as exc:
            sp.record_exception(exc)
            sp.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)[:200]))
            raise
