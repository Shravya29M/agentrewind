"""Drop-in traced wrappers for OpenAI and Anthropic clients.

The wrappers are duck-typed — they call ``client.chat.completions.create`` /
``client.messages.create`` on whatever client you hand them — so agentlens
does not depend on either SDK and the wrappers work with mocks in tests.

Usage:

    from openai import OpenAI
    from agentlens.providers import OpenAIChat

    llm = OpenAIChat(OpenAI(), mode="auto")
    resp = llm.create(model="gpt-4o", messages=[...])   # traced + replayable
"""

from __future__ import annotations

from typing import Any

from .replay import Recorder
from .store import TraceStore


def _to_dict(response: Any) -> dict:
    """Normalize SDK response objects (pydantic models) to plain dicts."""
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "to_dict"):
        return response.to_dict()
    raise TypeError(f"cannot serialize response of type {type(response).__name__}")


def _anthropic_usage_to_openai(response: dict) -> dict:
    """Map Anthropic usage keys onto the prompt/completion names the tracer reads."""
    usage = response.get("usage")
    if isinstance(usage, dict) and "input_tokens" in usage:
        usage.setdefault("prompt_tokens", usage["input_tokens"])
        usage.setdefault("completion_tokens", usage.get("output_tokens", 0))
    return response


class OpenAIChat:
    """Traced, replayable wrapper around an OpenAI client's chat completions."""

    def __init__(self, client: Any, mode: str = "auto", store: TraceStore | None = None):
        self.client = client
        self._recorder = Recorder(self._call, mode=mode, store=store)

    def _call(self, request: dict) -> dict:
        return _to_dict(self.client.chat.completions.create(**request))

    def create(self, **request: Any) -> dict:
        return self._recorder.call(request)


class AnthropicMessages:
    """Traced, replayable wrapper around an Anthropic client's messages API."""

    def __init__(self, client: Any, mode: str = "auto", store: TraceStore | None = None):
        self.client = client
        self._recorder = Recorder(self._call, mode=mode, store=store)

    def _call(self, request: dict) -> dict:
        return _anthropic_usage_to_openai(_to_dict(self.client.messages.create(**request)))

    def create(self, **request: Any) -> dict:
        return self._recorder.call(request)
