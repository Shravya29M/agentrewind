"""AgentRewind: flight recorder for LLM agents — trace, replay, diff."""

from .diff import Divergence, diff_traces, format_divergences
from .models import Span, SpanKind, Status, Trace
from .providers import AnthropicMessages, OpenAIChat, instrument
from .redaction import RedactionPolicy
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
from .store import BaseStore, SQLiteStore, TraceStore, open_store

__version__ = "0.3.0"

__all__ = [
    "AnthropicMessages",
    "BaseStore",
    "Divergence",
    "OpenAIChat",
    "Recorder",
    "RedactionPolicy",
    "ReplayMissError",
    "SQLiteStore",
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
    "instrument",
    "open_store",
    "record_llm_call",
    "span",
    "trace",
    "traced",
]
