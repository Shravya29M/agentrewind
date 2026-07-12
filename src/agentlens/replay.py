"""Record/replay engine for LLM calls.

Wrap any provider-call function in a Recorder. In "record" mode calls hit the
real provider and responses are cached by a request fingerprint. In "replay"
mode cached responses are served without touching the network, so an entire
agent run re-executes deterministically and for free. "auto" replays on cache
hit and records on miss.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from .sdk import get_store, record_llm_call
from .store import TraceStore


class ReplayMissError(RuntimeError):
    """Raised in replay mode when a request was never recorded."""


def fingerprint(request: dict[str, Any]) -> str:
    """Stable hash of a request. Key order and whitespace do not matter."""
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class Recorder:
    def __init__(
        self,
        call_fn: Callable[[dict[str, Any]], dict[str, Any]],
        mode: str = "auto",
        store: TraceStore | None = None,
    ):
        if mode not in ("record", "replay", "auto"):
            raise ValueError(f"mode must be record|replay|auto, got {mode!r}")
        self.call_fn = call_fn
        self.mode = mode
        self.store = store or get_store()

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        fp = fingerprint(request)
        cached = self.store.cache_get(fp) if self.mode in ("replay", "auto") else None

        if cached is not None:
            response, replayed = cached, True
        elif self.mode == "replay":
            raise ReplayMissError(
                f"No recorded response for request fingerprint {fp[:12]}… "
                f"(model={request.get('model')}). Run in record mode first."
            )
        else:
            response, replayed = self.call_fn(request), False
            self.store.cache_put(fp, request, response)

        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        record_llm_call(
            model=str(request.get("model", "unknown")),
            request=request,
            response=response,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            name=("replay:" if replayed else "") + str(request.get("model", "llm")),
        )
        return response
