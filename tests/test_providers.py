"""Provider wrappers tested against fake SDK clients (no openai/anthropic dependency)."""

from types import SimpleNamespace

import agentrewind as al
from agentrewind.providers import AnthropicMessages, OpenAIChat


class FakeResponse:
    """Mimics a pydantic response object from the real SDKs."""

    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


class FakeOpenAI:
    def __init__(self):
        self.calls = 0
        create = self._create
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    def _create(self, **request):
        self.calls += 1
        return FakeResponse(
            {
                "choices": [{"message": {"content": f"reply #{self.calls}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }
        )


class FakeAnthropic:
    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.calls += 1
        return FakeResponse(
            {
                "content": [{"type": "text", "text": f"reply #{self.calls}"}],
                "usage": {"input_tokens": 20, "output_tokens": 6},
            }
        )


REQ = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def test_openai_wrapper_traces_and_replays():
    client = FakeOpenAI()
    llm = OpenAIChat(client, mode="auto")

    with al.trace("run") as t:
        first = llm.create(**REQ)
        second = llm.create(**REQ)

    assert client.calls == 1  # second call replayed from cache
    assert first == second
    assert first["choices"][0]["message"]["content"] == "reply #1"
    assert t.total_tokens() == {"prompt_tokens": 24, "completion_tokens": 8}
    assert [s.kind.value for s in t.spans] == ["llm", "llm"]
    assert t.spans[1].name.startswith("replay:")


def test_anthropic_wrapper_normalizes_usage():
    llm = AnthropicMessages(FakeAnthropic(), mode="record")
    with al.trace("run") as t:
        resp = llm.create(model="claude-sonnet-5", max_tokens=100, messages=[])
    assert resp["usage"]["input_tokens"] == 20  # original keys preserved
    assert t.total_tokens() == {"prompt_tokens": 20, "completion_tokens": 6}


def test_replay_works_across_wrapper_instances(fresh_store):
    client = FakeOpenAI()
    OpenAIChat(client, mode="record").create(**REQ)

    # Fresh wrapper, replay-only: must not hit the client at all.
    broken_client = SimpleNamespace(chat=None)
    with al.trace("replay-run"):
        resp = OpenAIChat(broken_client, mode="replay").create(**REQ)
    assert resp["choices"][0]["message"]["content"] == "reply #1"
