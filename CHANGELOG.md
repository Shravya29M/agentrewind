# Changelog

## Unreleased

## 0.1.0 — 2026-07-12
- Trace SDK: `trace()`, `span()`, `@traced` (sync + async), `record_llm_call`.
- Zero-config SQLite trace store (`~/.agentlens/traces.db`).
- Record/replay engine: `Recorder` with `record` / `replay` / `auto` modes, canonical
  request fingerprinting, `canonicalize` hook for volatile fields, async `acall`.
- Provider wrappers: `OpenAIChat`, `AnthropicMessages` (duck-typed, no SDK dependency).
- Structural run diff: `agentlens diff` with execution-ordered divergences; exit code 2
  on divergence for use as a CI regression gate.
- CLI (`list` / `show` / `diff` / `serve`) and built-in web trace viewer.
- Offline example agent with a seeded regression (`examples/research_agent.py`).
