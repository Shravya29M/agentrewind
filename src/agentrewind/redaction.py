"""Opt-in protection against persisting common credentials in local traces."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedactionPolicy:
    """Recursively redact credentials before AgentRewind writes to SQLite.

    The policy is deliberately opt-in: redacting a request also redacts its replay
    cache, so a replay may not contain the original sensitive value. Applications
    handling real user data should enable it with ``configure(redaction=...)``.
    """

    replacement: str = "[REDACTED]"
    sensitive_keys: tuple[str, ...] = (
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    )
    redact_text_patterns: bool = True

    def redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: self.replacement if self._is_sensitive_key(str(key)) else self.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, str) and self.redact_text_patterns:
            return self._redact_text(value)
        return value

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        return any(sensitive in normalized for sensitive in self.sensitive_keys)

    def _redact_text(self, text: str) -> str:
        text = re.sub(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+", f"Bearer {self.replacement}", text)
        text = re.sub(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b", self.replacement, text)
        text = re.sub(r"\bAKIA[0-9A-Z]{16}\b", self.replacement, text)
        return text
