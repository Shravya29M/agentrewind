# Changelog

## Unreleased

## 0.2.0 — 2026-07-12
- Streaming capture: `Recorder.call_stream` / `acall_stream` record chunks while passing
  them through live; replay re-streams identical chunks offline. Usage picked up from the
  final chunk (OpenAI `stream_options` / Anthropic `message_delta` convention).
- Auto-instrumentation: `agentlens.instrument(client)` patches an OpenAI- or
  Anthropic-shaped client in place — no code changes at call sites.
- Web viewer: select two runs to open a side-by-side divergence view; new
  `/api/diff/{left}/{right}` endpoint.
- Demo GIF and vhs tape (`docs/demo.tape`).

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
