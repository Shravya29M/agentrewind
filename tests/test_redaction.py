import agentrewind as al
from agentrewind.redaction import RedactionPolicy
from agentrewind.store import TraceStore


def test_redaction_removes_sensitive_values_from_persisted_trace(tmp_path):
    store = TraceStore(tmp_path / "traces.db", redaction=RedactionPolicy())
    al.configure(store=store)

    with al.trace("private", metadata={"api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz"}) as trace:
        with al.span(
            "call",
            input={"headers": {"Authorization": "Bearer super-secret-token"}, "safe": "ok"},
        ) as recorded:
            recorded.output = {"message": "key sk-abcdefghijklmnopqrstuvwxyz was used"}

    persisted = store.get_trace(trace.trace_id)
    assert persisted.metadata["api_key"] == "[REDACTED]"
    assert persisted.spans[0].input["headers"]["Authorization"] == "[REDACTED]"
    assert persisted.spans[0].input["safe"] == "ok"
    assert persisted.spans[0].output["message"] == "key [REDACTED] was used"
    # The in-memory value is untouched, so instrumentation never changes app behavior.
    assert trace.spans[0].input["headers"]["Authorization"].startswith("Bearer ")


def test_redaction_applies_to_replay_cache(tmp_path):
    store = TraceStore(tmp_path / "traces.db", redaction=RedactionPolicy())
    store.cache_put(
        "fingerprint", {"token": "private"}, {"authorization": "Bearer abcdefghijklmnop"}
    )
    assert store.cache_get("fingerprint") == {
        "authorization": "[REDACTED]"
    }
