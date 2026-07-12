import pytest

import agentlens as al
from test_providers import REQ, FakeAnthropic, FakeOpenAI


def test_instrument_openai_patches_in_place():
    client = al.instrument(FakeOpenAI(), mode="auto")
    with al.trace("run") as t:
        first = client.chat.completions.create(**REQ)
        second = client.chat.completions.create(**REQ)
    assert client.calls == 1  # second call replayed
    assert first == second
    assert [s.kind.value for s in t.spans] == ["llm", "llm"]


def test_instrument_anthropic_normalizes_usage():
    client = al.instrument(FakeAnthropic(), mode="record")
    with al.trace("run") as t:
        client.messages.create(model="claude-sonnet-5", max_tokens=64, messages=[])
    assert t.total_tokens() == {"prompt_tokens": 20, "completion_tokens": 6}


def test_instrument_rejects_unknown_client():
    with pytest.raises(TypeError, match="OpenAI-shaped"):
        al.instrument(object())
