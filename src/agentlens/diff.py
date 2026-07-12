"""Structural diff between two traces of the same agent.

Walks both span trees in parallel (children ordered by start time), aligning
spans by name+kind, and reports divergences: structure changes (a span present
in only one run), input changes, and output changes — in the order they occur,
so the first entry is where the runs first went their separate ways.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .models import Span, Trace


@dataclass
class Divergence:
    kind: str  # "structure" | "input" | "output" | "status"
    path: str  # e.g. "agent/plan/llm:gpt-4o"
    detail: str
    left: Any = None
    right: Any = None


def _norm(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _label(s: Span) -> str:
    return f"{s.kind.value}:{s.name}"


def diff_traces(left: Trace, right: Trace) -> list[Divergence]:
    out: list[Divergence] = []
    _diff_children(left, right, left.roots(), right.roots(), path="", out=out)
    return out


def _diff_children(
    lt: Trace, rt: Trace, ls: list[Span], rs: list[Span], path: str, out: list[Divergence]
) -> None:
    # Greedy alignment by label, in order. Simple and predictable; agents are
    # mostly sequential so this catches real divergence points well.
    i = j = 0
    while i < len(ls) and j < len(rs):
        a, b = ls[i], rs[j]
        if _label(a) == _label(b):
            _diff_span(lt, rt, a, b, f"{path}/{_label(a)}", out)
            i += 1
            j += 1
            continue
        # Labels differ: check if one side inserted a span the other lacks.
        r_labels = [_label(s) for s in rs[j:]]
        l_labels = [_label(s) for s in ls[i:]]
        if _label(a) in r_labels:
            out.append(
                Divergence("structure", f"{path}/{_label(b)}", "only in right run", right=b.input)
            )
            j += 1
        elif _label(b) in l_labels:
            out.append(
                Divergence("structure", f"{path}/{_label(a)}", "only in left run", left=a.input)
            )
            i += 1
        else:
            out.append(
                Divergence(
                    "structure",
                    f"{path}/{_label(a)} vs {_label(b)}",
                    "different span at same position",
                    left=a.input,
                    right=b.input,
                )
            )
            i += 1
            j += 1
    for s in ls[i:]:
        out.append(Divergence("structure", f"{path}/{_label(s)}", "only in left run", left=s.input))
    for s in rs[j:]:
        out.append(
            Divergence("structure", f"{path}/{_label(s)}", "only in right run", right=s.input)
        )


def _diff_span(lt: Trace, rt: Trace, a: Span, b: Span, path: str, out: list[Divergence]) -> None:
    if _norm(a.input) != _norm(b.input):
        out.append(Divergence("input", path, "inputs differ", left=a.input, right=b.input))
    if _norm(a.output) != _norm(b.output):
        out.append(Divergence("output", path, "outputs differ", left=a.output, right=b.output))
    if a.status != b.status:
        out.append(
            Divergence("status", path, "status differs", left=a.status.value, right=b.status.value)
        )
    _diff_children(lt, rt, lt.children_of(a.span_id), rt.children_of(b.span_id), path, out)


def format_divergences(divs: list[Divergence], max_value_len: int = 120) -> str:
    if not divs:
        return "Runs are identical in structure, inputs, and outputs."
    lines = [f"{len(divs)} divergence(s); first divergence is where the runs split:\n"]
    for n, d in enumerate(divs, 1):
        lines.append(f"{n}. [{d.kind}] {d.path} — {d.detail}")
        for side, val in (("left ", d.left), ("right", d.right)):
            if val is not None:
                text = _norm(val)
                if len(text) > max_value_len:
                    text = text[:max_value_len] + "…"
                lines.append(f"     {side}: {text}")
    return "\n".join(lines)
