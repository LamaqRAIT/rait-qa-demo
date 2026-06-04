"""
OpenTelemetry setup and span helpers.

Priority order (from doc §2.7):
1. FastAPI auto-instrumentation + httpx auto-instrumentation (covers vLLM HTTP calls)
2. DeBERTa NLI: manual span in confidence_gate.py / nli.py
3. Confidence gate: manual span with all 5 signal values
4. Embedding model: manual span
5. Playwright execution spans (future)

Collector: OTel Collector sidecar → Cloud Trace + Cloud Monitoring + Cloud Logging.
OTEL_EXPORTER_OTLP_ENDPOINT env var points to the sidecar (http://localhost:4317).
"""
import os
import structlog

log = structlog.get_logger()


def init_telemetry(app=None) -> None:
    """
    Initialise OTel SDK and instrument FastAPI + httpx.
    Call from FastAPI lifespan startup.
    No-op if opentelemetry packages are not installed or OTEL_ENDPOINT is not set.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        log.info("telemetry.disabled", reason="OTEL_EXPORTER_OTLP_ENDPOINT not set")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": "rait-qa-backend",
            "service.version": os.environ.get("APP_VERSION", "dev"),
            "deployment.environment": os.environ.get("ENVIRONMENT", "development"),
        })

        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        # Auto-instrument FastAPI
        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)

        # Auto-instrument httpx (covers all vLLM HTTP calls)
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()

        log.info("telemetry.initialized", endpoint=endpoint)

    except ImportError as exc:
        log.warning("telemetry.packages_missing", error=str(exc)[:100])
    except Exception as exc:
        log.warning("telemetry.init_failed", error=str(exc)[:100])


def get_tracer():
    """Return the OTel tracer for manual span creation."""
    try:
        from opentelemetry import trace
        return trace.get_tracer("rait-qa-backend")
    except ImportError:
        return _NoopTracer()


class _NoopTracer:
    """Fallback when OTel is not installed — spans are no-ops."""
    def start_as_current_span(self, name, **kwargs):
        import contextlib
        @contextlib.contextmanager
        def _noop():
            yield _NoopSpan()
        return _noop()


class _NoopSpan:
    def set_attribute(self, key, value):
        pass
