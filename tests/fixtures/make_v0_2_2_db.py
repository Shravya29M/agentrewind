"""Regenerate traces_v0_2_2.db with the *published* 0.2.2 wheel, never with this checkout.

    python -m venv /tmp/v022 && /tmp/v022/bin/pip install llm-run-recorder==0.2.2
    /tmp/v022/bin/python tests/fixtures/make_v0_2_2_db.py tests/fixtures/traces_v0_2_2.db
    sqlite3 tests/fixtures/traces_v0_2_2.db "PRAGMA journal_mode=DELETE"
"""
import importlib.metadata as md
import sys

from agentrewind.models import Span, SpanKind, Status, Trace
from agentrewind.store import TraceStore

assert md.version("llm-run-recorder") == "0.2.2"
store = TraceStore(sys.argv[1])
t = Trace(trace_id="legacy0a1b2c3d4e5", name="legacy-run", started_at=1000.0, ended_at=1010.0,
          status=Status.OK, metadata={"release": "0.2.2"})
t.spans = [
    # Inserted in this order; two share started_at=1001.0 so the NULL-seq fallback
    # (timestamp, then span_id) is observable: "aaa-tie" must precede "zzz-tie".
    Span(span_id="root000000000000", trace_id=t.trace_id, parent_id=None, name="agent",
         kind=SpanKind.SPAN, started_at=1000.0, ended_at=1010.0, status=Status.OK,
         input={"q": "hi"}, output={"a": "bye"}),
    Span(span_id="zzz-tie", trace_id=t.trace_id, parent_id="root000000000000", name="search",
         kind=SpanKind.TOOL, started_at=1001.0, ended_at=1002.0, status=Status.OK,
         input={"query": "x"}, output=["r1"]),
    Span(span_id="aaa-tie", trace_id=t.trace_id, parent_id="root000000000000", name="llm",
         kind=SpanKind.LLM, started_at=1001.0, ended_at=1003.0, status=Status.OK,
         input={"messages": [{"role": "user", "content": "hi"}]}, output={"content": "bye"},
         attributes={"model": "m"}),
]
store.save_trace(t)
store.cache_put("fp-legacy", {"model": "m"}, {"content": "cached"})
print("ok")
