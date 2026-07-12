"""AgentLens: flight recorder for LLM agents — trace, replay, diff."""

from .diff import Divergence, diff_traces, format_divergences
from .models import Span, SpanKind, Status, Trace
from .replay import Recorder, ReplayMissError, fingerprint
from .sdk import (
    configure,
    current_span,
    current_trace,
    get_store,
    record_llm_call,
    span,
    trace,
    traced,
)
from .store import TraceStore

__version__ = "0.1.0"

__all__ = [
    "Divergence",
    "Recorder",
    "ReplayMissError",
    "Span",
    "SpanKind",
    "Status",
    "Trace",
    "TraceStore",
    "configure",
    "current_span",
    "current_trace",
    "diff_traces",
    "fingerprint",
    "format_divergences",
    "get_store",
    "record_llm_call",
    "span",
    "trace",
    "traced",
]
