"""Bench server tracing. Capturing events never starts a paid evaluation."""
from .client import Bench, Span, infer_span_kind
from .otel import BenchSpanExporter

__all__ = ["Bench", "Span", "EvaluationContext", "BenchSpanExporter", "infer_span_kind"]

from .evaluation import EvaluationContext

from .evaluations import EvaluationClient, EvaluationPolicy, EvaluationResult
__all__ += ["EvaluationClient", "EvaluationPolicy", "EvaluationResult"]
from .runtime_gateway import BenchGateway as RuntimeGateway
__all__ += ["RuntimeGateway"]
from .platform import BenchPlatform, PlatformError
