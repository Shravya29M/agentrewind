"""Edge cases in the supporting modules: response normalization, redaction of
uncommon container types, in-memory stores, and artifact validation."""

import pytest

import agentrewind as al
from agentrewind.models import Span, SpanKind, Status
from agentrewind.providers import _anthropic_usage_to_openai, _to_dict
from agentrewind.redaction import RedactionPolicy
from agentrewind.store import TraceStore

# --------------------------------------------------------------------------
# provider response normalization
# --------------------------------------------------------------------------


def test_to_dict_passes_a_plain_dict_through():
    payload = {"id": "x"}
    assert _to_dict(payload) is payload


def test_to_dict_uses_model_dump_for_pydantic_style_objects():
    class Pydanticish:
        def model_dump(self):
            return {"id": "from-model-dump"}

    assert _to_dict(Pydanticish()) == {"id": "from-model-dump"}


def test_to_dict_falls_back_to_to_dict():
    class Legacy:
        def to_dict(self):
            return {"id": "from-to-dict"}

    assert _to_dict(Legacy()) == {"id": "from-to-dict"}


def test_to_dict_rejects_an_unserializable_response():
    class Opaque:
        pass

    with pytest.raises(TypeError, match="Opaque"):
        _to_dict(Opaque())


def test_anthropic_usage_is_mapped_onto_openai_names():
    out = _anthropic_usage_to_openai(
        {"usage": {"input_tokens": 10, "output_tokens": 4}}
    )
    assert out["usage"]["prompt_tokens"] == 10
    assert out["usage"]["completion_tokens"] == 4


def test_anthropic_mapping_does_not_clobber_existing_openai_names():
    out = _anthropic_usage_to_openai(
        {"usage": {"input_tokens": 10, "output_tokens": 4, "prompt_tokens": 99}}
    )
    assert out["usage"]["prompt_tokens"] == 99


def test_anthropic_mapping_defaults_missing_output_tokens_to_zero():
    out = _anthropic_usage_to_openai({"usage": {"input_tokens": 10}})
    assert out["usage"]["completion_tokens"] == 0


def test_anthropic_mapping_leaves_a_response_without_usage_alone():
    payload = {"id": "x"}
    assert _anthropic_usage_to_openai(payload) == {"id": "x"}


def test_anthropic_mapping_ignores_a_non_dict_usage_field():
    payload = {"usage": "unknown"}
    assert _anthropic_usage_to_openai(payload) == payload


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------


def test_redaction_walks_into_tuples():
    policy = RedactionPolicy()
    result = policy.redact(({"api_key": "sk-secret"}, "plain"))
    assert isinstance(result, tuple)
    assert result[0]["api_key"] == "[REDACTED]"


def test_redaction_walks_into_lists():
    policy = RedactionPolicy()
    assert policy.redact([{"password": "hunter2"}])[0]["password"] == "[REDACTED]"


def test_redaction_leaves_non_string_scalars_alone():
    policy = RedactionPolicy()
    assert policy.redact({"count": 7, "ok": True, "none": None}) == {
        "count": 7,
        "ok": True,
        "none": None,
    }


def test_text_pattern_redaction_can_be_switched_off():
    """Key-based redaction still applies; only free-text scanning is disabled."""
    policy = RedactionPolicy(redact_text_patterns=False)
    result = policy.redact({"note": "my key is sk-abcdefghijklmnopqrst", "token": "t"})
    assert result["note"] == "my key is sk-abcdefghijklmnopqrst"
    assert result["token"] == "[REDACTED]"


def test_sensitive_key_matching_is_case_insensitive():
    policy = RedactionPolicy()
    assert policy.redact({"API_KEY": "x"})["API_KEY"] == "[REDACTED]"


def test_redaction_uses_the_configured_replacement():
    policy = RedactionPolicy(replacement="***")
    assert policy.redact({"secret": "x"})["secret"] == "***"


def test_non_string_keys_are_handled():
    policy = RedactionPolicy()
    assert policy.redact({1: "value"}) == {1: "value"}


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------


def test_an_in_memory_store_needs_no_directory():
    store = TraceStore(":memory:")
    assert store.list_traces(10) == []


def test_import_rejects_a_payload_that_is_not_an_export():
    store = TraceStore(":memory:")
    with pytest.raises(ValueError, match="not an AgentRewind trace v1 export"):
        store.import_trace({"format": "something.else", "trace": {}})


def test_import_rejects_an_export_whose_trace_is_not_an_object():
    store = TraceStore(":memory:")
    with pytest.raises(ValueError, match="not an AgentRewind trace v1 export"):
        store.import_trace({"format": "agentrewind.trace.v1", "trace": []})


def test_import_rejects_an_export_missing_required_fields():
    store = TraceStore(":memory:")
    with pytest.raises(ValueError, match="missing required fields"):
        store.import_trace(
            {"format": "agentrewind.trace.v1", "trace": {"trace_id": "x"}}
        )


def test_import_rejects_an_export_whose_spans_are_not_a_list():
    store = TraceStore(":memory:")
    with pytest.raises(ValueError, match="missing required fields"):
        store.import_trace(
            {
                "format": "agentrewind.trace.v1",
                "trace": {
                    "trace_id": "x",
                    "name": "n",
                    "started_at": 0.0,
                    "status": "ok",
                    "metadata": {},
                    "spans": "not-a-list",
                },
            }
        )


def test_import_rejects_a_malformed_span():
    store = TraceStore(":memory:")
    with pytest.raises(ValueError, match="invalid trace export"):
        store.import_trace(
            {
                "format": "agentrewind.trace.v1",
                "trace": {
                    "trace_id": "x",
                    "name": "n",
                    "started_at": 0.0,
                    "status": "ok",
                    "metadata": {},
                    "spans": [{"span_id": "only-this"}],
                },
            }
        )


def test_import_refuses_to_clobber_an_existing_trace(fresh_store):
    with al.trace("agent"):
        pass
    artifact = fresh_store.export_trace(fresh_store.list_traces(1)[0].trace_id)
    with pytest.raises(ValueError, match="already exists"):
        fresh_store.import_trace(artifact)


def test_export_of_an_unknown_trace_returns_none():
    assert TraceStore(":memory:").export_trace("nope") is None


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------


def test_duration_is_none_while_a_span_is_still_running():
    span = Span(name="s", kind=SpanKind.TOOL, trace_id="t", span_id="s1")
    assert span.duration_ms is None


def test_duration_is_populated_once_a_span_ends():
    span = Span(name="s", kind=SpanKind.TOOL, trace_id="t", span_id="s1")
    span.end(Status.OK)
    assert span.duration_ms is not None
    assert span.duration_ms >= 0
