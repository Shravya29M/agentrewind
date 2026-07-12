"""Core data models: a Trace is a tree of Spans recorded during one agent run."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SpanKind(str, Enum):
    SPAN = "span"
    LLM = "llm"
    TOOL = "tool"


class Status(str, Enum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


def new_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    trace_id: str
    name: str
    kind: SpanKind = SpanKind.SPAN
    span_id: str = field(default_factory=new_id)
    parent_id: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    status: Status = Status.RUNNING
    error: str | None = None
    # Arbitrary JSON-serializable payloads. For LLM spans, input is the request
    # (model, messages, params) and output is the response.
    input: Any = None
    output: Any = None
    # Free-form key/values: token counts, model name, tool name, etc.
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at) * 1000

    def end(self, status: Status = Status.OK, error: str | None = None) -> None:
        self.ended_at = time.time()
        self.status = status
        self.error = error

    def to_row(self) -> tuple:
        return (
            self.span_id,
            self.trace_id,
            self.parent_id,
            self.name,
            self.kind.value,
            self.started_at,
            self.ended_at,
            self.status.value,
            self.error,
            json.dumps(self.input, default=str),
            json.dumps(self.output, default=str),
            json.dumps(self.attributes, default=str),
        )

    @classmethod
    def from_row(cls, row: tuple) -> Span:
        return cls(
            span_id=row[0],
            trace_id=row[1],
            parent_id=row[2],
            name=row[3],
            kind=SpanKind(row[4]),
            started_at=row[5],
            ended_at=row[6],
            status=Status(row[7]),
            error=row[8],
            input=json.loads(row[9]),
            output=json.loads(row[10]),
            attributes=json.loads(row[11]),
        )


@dataclass
class Trace:
    name: str
    trace_id: str = field(default_factory=new_id)
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    status: Status = Status.RUNNING
    metadata: dict[str, Any] = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)

    def end(self, status: Status = Status.OK) -> None:
        self.ended_at = time.time()
        self.status = status

    def roots(self) -> list[Span]:
        return [s for s in self.spans if s.parent_id is None]

    def children_of(self, span_id: str) -> list[Span]:
        return sorted(
            (s for s in self.spans if s.parent_id == span_id),
            key=lambda s: s.started_at,
        )

    def total_tokens(self) -> dict[str, int]:
        totals = {"prompt_tokens": 0, "completion_tokens": 0}
        for s in self.spans:
            for key in totals:
                v = s.attributes.get(key)
                if isinstance(v, int):
                    totals[key] += v
        return totals
