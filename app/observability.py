"""OpenTelemetry instrumentation for SupportFlow.

Provides vendor-neutral observability with:
- W3C trace context propagation
- Fail-open telemetry (business operations never blocked by observability failures)
- Privacy-first design (secrets/PII redacted by default)
- Configurable sampling and export
"""

import os
import hashlib
import json
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION (Single Source of Truth)
# ══════════════════════════════════════════════════════════════════════


class ObservabilityConfig:
    """Central observability configuration."""
    # Core settings
    ENABLED = os.getenv("OBSERVABILITY_ENABLED", "true").lower() == "true"
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", None)  # e.g. "http://jaeger:4317"

    # Logging
    LOG_LEVEL = os.getenv("OBSERVABILITY_LOG_LEVEL", "INFO")

    # Privacy controls
    LOG_CUSTOMER_MESSAGES = os.getenv("LOG_CUSTOMER_MESSAGES", "false").lower() == "true"
    LOG_TOOL_ARGS = os.getenv("LOG_TOOL_ARGS", "false").lower() == "true"
    LOG_PROMPTS = os.getenv("LOG_PROMPTS", "false").lower() == "true"

    # Sampling
    TRACE_SAMPLE_RATE = float(os.getenv("TRACE_SAMPLE_RATE", "1.0"))  # 1.0 = 100%

    # Use hashed customer IDs for external telemetry (privacy-first)
    HASH_CUSTOMER_IDS = os.getenv("HASH_CUSTOMER_IDS", "true").lower() == "true"


OBSERVABILITY_ENABLED = ObservabilityConfig.ENABLED

# ══════════════════════════════════════════════════════════════════════
# INITIALIZATION
# ══════════════════════════════════════════════════════════════════════


def init_observability():
    """Initialize OpenTelemetry with fail-open semantics.

    If initialization fails, logs error but does not crash application.
    All subsequent tracing becomes no-op.
    """
    if not ObservabilityConfig.ENABLED:
        print("[OBSERVABILITY] Disabled via OBSERVABILITY_ENABLED=false")
        return

    try:
        resource = Resource.create({
            "service.name": "supportflow",
            "service.version": "1.0.0",
            "deployment.environment": ObservabilityConfig.ENVIRONMENT,
        })

        # Configure sampling
        # NOTE: Current sampling is probability-based (TraceIdRatioBased).
        # Future work: Implement parent-based sampling with error/latency overrides
        # to always sample errors and high-latency requests regardless of sample rate.
        sampler = TraceIdRatioBased(ObservabilityConfig.TRACE_SAMPLE_RATE)

        provider = TracerProvider(resource=resource, sampler=sampler)

        # Choose exporter based on config
        if ObservabilityConfig.OTLP_ENDPOINT:
            exporter = OTLPSpanExporter(endpoint=ObservabilityConfig.OTLP_ENDPOINT)
        else:
            exporter = ConsoleSpanExporter()  # Local dev: print to console

        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        print(
            f"[OBSERVABILITY] Initialized: env={ObservabilityConfig.ENVIRONMENT}, "
            f"sampling={ObservabilityConfig.TRACE_SAMPLE_RATE}, "
            f"endpoint={ObservabilityConfig.OTLP_ENDPOINT or 'console'}"
        )
    except Exception as err:
        # Fail-open: log error but don't crash
        print(f"[OBSERVABILITY] Failed to initialize: {err}")


# Get tracer instance
tracer = trace.get_tracer("supportflow")

# ══════════════════════════════════════════════════════════════════════
# PRIVACY / SANITIZATION UTILITIES
# ══════════════════════════════════════════════════════════════════════


def hash_sensitive(value: str) -> str:
    """Hash sensitive string to 16-char hex for safe logging."""
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def get_customer_identifier(customer_id: int) -> str:
    """Return customer identifier for telemetry (hashed if configured)."""
    if ObservabilityConfig.HASH_CUSTOMER_IDS:
        return hash_sensitive(f"customer_{customer_id}")
    return str(customer_id)


def sanitize_tool_args(args: dict) -> str:
    """Return hash of tool args instead of raw data."""
    return hash_sensitive(json.dumps(args, sort_keys=True))


# ══════════════════════════════════════════════════════════════════════
# TRACED SPAN CONTEXT MANAGER
# ══════════════════════════════════════════════════════════════════════


@contextmanager
def traced_span(name: str, attributes: dict = None):
    """Context manager for creating traced spans with fail-open telemetry.

    CRITICAL BEHAVIOR:
    - Telemetry failures are suppressed (logged and ignored)
    - Exceptions from wrapped business logic ALWAYS propagate unchanged
    - If business exception occurs, span is marked ERROR before re-raising

    Args:
        name: Span name (e.g. "supportflow.graph.classify")
        attributes: Optional dict of span attributes

    Yields:
        Span object or None (if observability disabled or failed)

    Example:
        with traced_span("operation", {"key": "value"}) as span:
            result = do_business_logic()  # Exceptions propagate normally
            if span:
                span.set_attribute("result", result)
    """
    if not OBSERVABILITY_ENABLED:
        yield None
        return

    span = None
    span_context_manager = None

    try:
        # Attempt to create span - this may fail
        span_context_manager = tracer.start_as_current_span(name)
        span = span_context_manager.__enter__()

        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
    except Exception as err:
        # Telemetry initialization failure - log and continue with no-op span
        print(f"[OBSERVABILITY] Span creation failed for '{name}': {err}")
        span = None
        span_context_manager = None

    try:
        # Execute business logic - exceptions propagate normally
        yield span
    except Exception as business_err:
        # Business logic raised an exception
        # Try to mark span as error, but always re-raise
        if span is not None:
            try:
                span.set_status(Status(StatusCode.ERROR, str(business_err)))
                span.record_exception(business_err)
            except Exception:
                pass  # Telemetry failure - ignore

        # ALWAYS re-raise the business exception unchanged
        raise
    finally:
        # Clean up span
        if span_context_manager is not None:
            try:
                span_context_manager.__exit__(None, None, None)
            except Exception:
                pass  # Telemetry failure during cleanup - ignore


# ══════════════════════════════════════════════════════════════════════
# SPAN ATTRIBUTE HELPERS
# ══════════════════════════════════════════════════════════════════════


def record_llm_call(
    span,
    model: str,
    prompt_version: str,
    latency_ms: float,
    input_tokens: int = None,
    output_tokens: int = None,
):
    """Record LLM call metadata to span (fail-open)."""
    if span is None:
        return

    try:
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.prompt_version", prompt_version)
        span.set_attribute("llm.latency_ms", latency_ms)
        if input_tokens:
            span.set_attribute("llm.tokens.input", input_tokens)
        if output_tokens:
            span.set_attribute("llm.tokens.output", output_tokens)
    except Exception:
        pass  # Fail-open


def safe_set_attribute(span, key: str, value):
    """Set span attribute with fail-open semantics."""
    if span is None:
        return

    try:
        span.set_attribute(key, value)
    except Exception:
        pass  # Fail-open
