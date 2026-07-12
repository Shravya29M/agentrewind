"""Instrumentation API: trace() and span() context managers, traced() decorator."""

from __future__ import annotations

import contextvars
import functools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .models import Span, SpanKind, Status, Trace
from .store import TraceStore

_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "agentlens_trace", default=None
)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "agentlens_span", default=None
)
_store: TraceStore | None = None


def configure(store: TraceStore | None = None, db_path: str | None = None) -> TraceStore:
    """Set the global store. Called implicitly with defaults on first use."""
    global _store
    _store = store or TraceStore(db_path)
    return _store


def get_store() -> TraceStore:
    global _store
    if _store is None:
        _store = TraceStore()
    return _store


def current_trace() -> Trace | None:
    return _current_trace.get()


def current_span() -> Span | None:
    return _current_span.get()


@contextmanager
def trace(name: str, metadata: dict[str, Any] | None = None) -> Iterator[Trace]:
    """Open a trace for one agent run. Saves to the store on exit, even on error."""
    t = Trace(name=name, metadata=metadata or {})
    token = _current_trace.set(t)
    try:
        yield t
        t.end(Status.OK)
    except BaseException:
        t.end(Status.ERROR)
        raise
    finally:
        _current_trace.reset(token)
        get_store().save_trace(t)


@contextmanager
def span(
    name: str,
    kind: SpanKind | str = SpanKind.SPAN,
    input: Any = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Open a span nested under the current trace/span. No-op if no trace is active."""
    t = _current_trace.get()
    if t is None:
        # Not inside a trace: yield a detached span so instrumented code still runs.
        yield Span(trace_id="detached", name=name)
        return
    parent = _current_span.get()
    s = Span(
        trace_id=t.trace_id,
        name=name,
        kind=SpanKind(kind),
        parent_id=parent.span_id if parent else None,
        input=input,
        attributes=attributes or {},
    )
    t.spans.append(s)
    token = _current_span.set(s)
    try:
        yield s
        if s.status == Status.RUNNING:
            s.end(Status.OK)
    except BaseException as e:
        s.end(Status.ERROR, error=f"{type(e).__name__}: {e}")
        raise
    finally:
        _current_span.reset(token)


def traced(name: str | None = None, kind: SpanKind | str = SpanKind.SPAN):
    """Decorator: record a function call as a span, capturing args and return value."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with span(name or fn.__name__, kind=kind, input={"args": args, "kwargs": kwargs}) as s:
                result = fn(*args, **kwargs)
                s.output = result
                return result

        return wrapper

    return decorator


def record_llm_call(
    model: str,
    request: dict[str, Any],
    response: dict[str, Any],
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    name: str | None = None,
) -> None:
    """Record an already-completed LLM call as an instantaneous llm span."""
    attrs: dict[str, Any] = {"model": model}
    if prompt_tokens is not None:
        attrs["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        attrs["completion_tokens"] = completion_tokens
    with span(name or model, kind=SpanKind.LLM, input=request, attributes=attrs) as s:
        s.output = response
