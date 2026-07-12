import pytest

import agentrewind as al
from agentrewind.models import SpanKind, Status


def test_trace_persists_span_tree(fresh_store):
    with al.trace("run") as t:
        with al.span("outer"):
            with al.span("inner", kind="tool") as s:
                s.output = {"result": 42}

    loaded = fresh_store.get_trace(t.trace_id)
    assert loaded.status == Status.OK
    assert [s.name for s in loaded.spans] == ["outer", "inner"]
    outer, inner = loaded.spans
    assert inner.parent_id == outer.span_id
    assert inner.kind == SpanKind.TOOL
    assert inner.output == {"result": 42}
    assert inner.duration_ms is not None


def test_error_marks_span_and_trace_and_still_saves(fresh_store):
    with pytest.raises(ValueError):
        with al.trace("failing-run") as t:
            with al.span("boom"):
                raise ValueError("bad tool input")

    loaded = fresh_store.get_trace(t.trace_id)
    assert loaded.status == Status.ERROR
    assert loaded.spans[0].status == Status.ERROR
    assert "bad tool input" in loaded.spans[0].error


def test_traced_decorator_captures_args_and_result():
    @al.traced(kind="tool")
    def add(a, b):
        return a + b

    with al.trace("run") as t:
        assert add(2, 3) == 5

    s = t.spans[0]
    assert s.name == "add"
    assert s.input == {"args": (2, 3), "kwargs": {}}
    assert s.output == 5


def test_span_outside_trace_is_noop():
    with al.span("orphan") as s:
        assert s.trace_id == "detached"
    assert al.current_trace() is None


def test_record_llm_call_token_rollup():
    with al.trace("run") as t:
        al.record_llm_call(
            model="mock-1",
            request={"messages": [{"role": "user", "content": "hi"}]},
            response={"content": "hello"},
            prompt_tokens=10,
            completion_tokens=5,
        )
        al.record_llm_call(model="mock-1", request={}, response={}, prompt_tokens=7)

    assert t.total_tokens() == {"prompt_tokens": 17, "completion_tokens": 5}
    assert t.spans[0].kind == SpanKind.LLM


def test_trace_id_prefix_lookup(fresh_store):
    with al.trace("run") as t:
        pass
    assert fresh_store.get_trace(t.trace_id[:6]).trace_id == t.trace_id
