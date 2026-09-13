"""FastAPI middleware for W3C trace context extraction and propagation.

Extracts W3C traceparent/tracestate headers using OpenTelemetry propagation APIs.
Creates HTTP request span as child of extracted parent context.
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from opentelemetry import trace
from opentelemetry.propagate import extract

from app.observability import OBSERVABILITY_ENABLED, hash_sensitive


class TraceMiddleware(BaseHTTPMiddleware):
    """Extract W3C trace context from request headers and create HTTP span."""

    async def dispatch(self, request: Request, call_next):
        if not OBSERVABILITY_ENABLED:
            return await call_next(request)

        try:
            # Extract W3C trace context from standard headers (traceparent, tracestate)
            # This respects distributed tracing initiated by upstream services
            parent_context = extract(request.headers)

            tracer = trace.get_tracer("supportflow")

            with tracer.start_as_current_span(
                f"{request.method} {request.url.path}",
                context=parent_context,
                attributes={
                    "http.method": request.method,
                    "http.url": str(request.url.path),
                }
            ) as span:
                # Add custom correlation ID if provided (for client-side correlation)
                # This is separate from the OTEL trace ID
                correlation_id = request.headers.get("X-Correlation-ID")
                if correlation_id:
                    span.set_attribute("correlation_id", correlation_id)

                # Add idempotency key if present (hashed for privacy)
                idempotency_key = request.headers.get("Idempotency-Key")
                if idempotency_key:
                    span.set_attribute("idempotency_key_hash", hash_sensitive(idempotency_key))

                # Store trace_id in request state for application use
                trace_id = format(span.get_span_context().trace_id, '032x')
                request.state.trace_id = trace_id

                # Execute request
                response = await call_next(request)

                # Record response status
                span.set_attribute("http.status_code", response.status_code)

                # Add trace_id to response headers for client correlation
                response.headers["X-Trace-ID"] = trace_id

                return response

        except Exception as err:
            # Fail-open: telemetry failure should not break request
            print(f"[OBSERVABILITY] Middleware error: {err}")
            return await call_next(request)
