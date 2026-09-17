"""Concurrency safety: many async runs interleaving on one event loop must never
leak spans into each other's traces, and replay must reproduce each run exactly.

All provider calls go to in-process fake async clients; nothing touches the network.
"""

import asyncio

import pytest

import agentrewind as al
from agentrewind.models import SpanKind, Trace
from agentrewind.replay import Recorder

N_RUNS = 12


def _delay(run: int, step: int) -> float:
    # Deterministic but scrambled latencies so runs finish in a different order
    # than they start, forcing interleaving across await points.
    return ((run * 7 + step * 3) % 5) * 0.002


class FakeAsyncCompletions:
    """Mimics AsyncOpenAI().chat.completions: an awaitable create()."""

    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **request):
        self.calls.append(request)
        prompt = request["messages"][-1]["content"]
        run, step = request["metadata"]["run"], request["metadata"]["step"]
        await asyncio.sleep(_delay(run, step))
        return {
            "choices": [{"message": {"role": "assistant", "content": f"answer to {prompt}"}}],
            "usage": {"prompt_tokens": run + 1, "completion_tokens": step + 1},
        }

    async def create_stream(self, **request):
        self.calls.append(request)
        prompt = request["messages"][-1]["content"]
        run = request["metadata"]["run"]
        for i, piece in enumerate(["chunk-a", "chunk-b", "chunk-c"]):
            await asyncio.sleep(_delay(run, i))
            yield {"delta": f"{piece} for {prompt}"}
        yield {"delta": "", "usage": {"prompt_tokens": run + 1, "completion_tokens": 3}}


class FakeAsyncClient:
    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeAsyncCompletions()


class ForbiddenClient:
    """Stands in for the provider during replay: any live call is a test failure."""

    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = self

    async def create(self, **request):
        raise AssertionError(f"replay hit the provider: {request}")

    def create_stream(self, **request):
        raise AssertionError(f"replay hit the provider: {request}")


def _request(run: int, step: int, content: str, stream: bool = False) -> dict:
    req = {
        "model": "fake-model",
        "messages": [{"role": "user", "content": content}],
        "metadata": {"run": run, "step": step},
    }
    if stream:
        req["stream"] = True
    return req


def _recorder(client, mode: str) -> Recorder:
    return Recorder(lambda req: client.chat.completions.create(**req), mode=mode)


def _stream_recorder(client, mode: str) -> Recorder:
    return Recorder(lambda req: client.chat.completions.create_stream(**req), mode=mode)


async def agent_run(run: int, rec: Recorder) -> tuple[Trace, str]:
    """A two-step agent: plan (LLM) -> tool -> act (LLM using the plan)."""
    with al.trace(f"run-{run}", metadata={"run": run}) as t:
        with al.span("agent", input={"run": run}):
            plan = await rec.acall(_request(run, 0, f"plan for run {run}"))
            plan_text = plan["choices"][0]["message"]["content"]
            with al.span("tool", kind=SpanKind.TOOL, input=plan_text) as tool:
                await asyncio.sleep(_delay(run, 9))
                tool.output = f"tool result {run}"
            act = await rec.acall(_request(run, 1, f"act on {plan_text}"))
        return t, act["choices"][0]["message"]["content"]


async def streaming_run(run: int, rec: Recorder) -> tuple[Trace, list[dict]]:
    with al.trace(f"stream-run-{run}", metadata={"run": run}) as t:
        with al.span("agent", input={"run": run}):
            chunks = [
                c async for c in rec.acall_stream(_request(run, 0, f"stream {run}", stream=True))
            ]
        return t, chunks


def assert_run_isolated(t: Trace, run: int, expected_llm_spans: int) -> None:
    """Every span belongs to this trace, hangs off this run's agent span, and
    carries only this run's data."""
    assert t.name.endswith(f"run-{run}")
    assert all(s.trace_id == t.trace_id for s in t.spans)

    (agent,) = t.roots()
    assert agent.name == "agent" and agent.input == {"run": run}
    span_ids = {s.span_id for s in t.spans}
    for s in t.spans:
        if s is not agent:
            assert s.parent_id == agent.span_id, f"{s.name} mis-parented in run {run}"
            assert s.parent_id in span_ids

    llm_spans = [s for s in t.spans if s.kind == SpanKind.LLM]
    assert len(llm_spans) == expected_llm_spans
    for s in llm_spans:
        assert s.input["metadata"]["run"] == run
        assert s.attributes["prompt_tokens"] == run + 1
        prompt = s.input["messages"][-1]["content"]
        assert prompt.endswith(f" {run}")  # " 1" must not match run 11
        if "chunks" in s.output:
            texts = [c["delta"] for c in s.output["chunks"] if c["delta"]]
        else:
            texts = [s.output["choices"][0]["message"]["content"]]
        assert texts and all(
            text.endswith(f" for {prompt}") or text == f"answer to {prompt}" for text in texts
        )


def _shape(t: Trace) -> list[tuple]:
    """Timestamp/id-free view of a trace for record-vs-replay comparison."""

    def walk(span, depth):
        name = span.name.removeprefix("replay:")
        rows = [(depth, span.kind.value, name, span.status.value, span.input, span.output)]
        for child in t.children_of(span.span_id):
            rows += walk(child, depth + 1)
        return rows

    return [row for root in t.roots() for row in walk(root, 0)]


# -- 1 & 2: concurrent non-streaming calls -----------------------------------------


@pytest.mark.asyncio
async def test_concurrent_async_runs_keep_spans_in_their_own_trace(fresh_store):
    client = FakeAsyncClient()
    rec = _recorder(client, mode="record")

    results = await asyncio.gather(*(agent_run(i, rec) for i in range(N_RUNS)))

    assert len(client.chat.completions.calls) == N_RUNS * 2
    assert len({t.trace_id for t, _ in results}) == N_RUNS
    for run, (t, answer) in enumerate(results):
        assert answer == f"answer to act on answer to plan for run {run}"
        assert len(t.spans) == 4  # agent, llm plan, tool, llm act
        assert_run_isolated(t, run, expected_llm_spans=2)

        # The persisted copy must match the in-memory tree exactly.
        stored = fresh_store.get_trace(t.trace_id)
        assert {s.span_id for s in stored.spans} == {s.span_id for s in t.spans}
        assert_run_isolated(stored, run, expected_llm_spans=2)

    # Context must not leak out of the gathered tasks.
    assert al.current_trace() is None
    assert al.current_span() is None


@pytest.mark.asyncio
async def test_concurrent_calls_inside_one_trace_all_parent_to_caller_span():
    client = FakeAsyncClient()
    rec = _recorder(client, mode="record")

    with al.trace("fan-out") as t:
        with al.span("fan") as fan:
            responses = await asyncio.gather(
                *(rec.acall(_request(i, 0, f"q{i}")) for i in range(N_RUNS))
            )

    llm_spans = [s for s in t.spans if s.kind == SpanKind.LLM]
    assert len(llm_spans) == N_RUNS
    assert all(s.parent_id == fan.span_id for s in llm_spans)
    for i, resp in enumerate(responses):
        assert resp["choices"][0]["message"]["content"] == f"answer to q{i}"
    # Each span's output pairs with its own input.
    for s in llm_spans:
        prompt = s.input["messages"][-1]["content"]
        assert s.output["choices"][0]["message"]["content"] == f"answer to {prompt}"


# -- 3: concurrent streaming calls -------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_streaming_runs_keep_spans_in_their_own_trace(fresh_store):
    client = FakeAsyncClient()
    rec = _stream_recorder(client, mode="record")

    results = await asyncio.gather(*(streaming_run(i, rec) for i in range(N_RUNS)))

    assert len(client.chat.completions.calls) == N_RUNS
    for run, (t, chunks) in enumerate(results):
        assert [c["delta"] for c in chunks[:3]] == [f"chunk-{x} for stream {run}" for x in "abc"]
        assert len(t.spans) == 2  # agent, llm
        assert_run_isolated(t, run, expected_llm_spans=1)
        (llm,) = [s for s in t.spans if s.kind == SpanKind.LLM]
        assert llm.output["chunks"] == chunks
        assert_run_isolated(fresh_store.get_trace(t.trace_id), run, expected_llm_spans=1)


@pytest.mark.asyncio
async def test_concurrent_streams_inside_one_trace_do_not_mix_chunks():
    client = FakeAsyncClient()
    rec = _stream_recorder(client, mode="record")

    async def consume(i):
        return [c async for c in rec.acall_stream(_request(i, 0, f"s{i}", stream=True))]

    with al.trace("fan-out-stream") as t:
        with al.span("fan") as fan:
            streams = await asyncio.gather(*(consume(i) for i in range(N_RUNS)))

    llm_spans = [s for s in t.spans if s.kind == SpanKind.LLM]
    assert len(llm_spans) == N_RUNS
    assert all(s.parent_id == fan.span_id for s in llm_spans)
    for i, chunks in enumerate(streams):
        assert all(f"s{i}" in c["delta"] for c in chunks[:3])
    for s in llm_spans:
        prompt = s.input["messages"][-1]["content"]
        assert all(prompt in c["delta"] for c in s.output["chunks"][:3])


# -- 4: record then replay a concurrent run ---------------------------------------


@pytest.mark.asyncio
async def test_concurrent_record_then_replay_is_identical(fresh_store):
    recorded = await asyncio.gather(
        *(agent_run(i, _recorder(FakeAsyncClient(), "record")) for i in range(N_RUNS))
    )
    # Replay in reverse start order, with a provider that fails if touched.
    replay_rec = _recorder(ForbiddenClient(), "replay")
    replayed = await asyncio.gather(*(agent_run(i, replay_rec) for i in reversed(range(N_RUNS))))
    replayed = list(reversed(replayed))

    for run in range(N_RUNS):
        (rec_t, rec_answer), (rep_t, rep_answer) = recorded[run], replayed[run]
        assert rep_answer == rec_answer
        assert rep_t.trace_id != rec_t.trace_id
        assert_run_isolated(rep_t, run, expected_llm_spans=2)
        assert all(s.name.startswith("replay:") for s in rep_t.spans if s.kind == SpanKind.LLM)
        assert _shape(rep_t) == _shape(rec_t)
        assert _shape(fresh_store.get_trace(rep_t.trace_id)) == _shape(
            fresh_store.get_trace(rec_t.trace_id)
        )


@pytest.mark.asyncio
async def test_concurrent_streaming_record_then_replay_is_identical(fresh_store):
    recorded = await asyncio.gather(
        *(streaming_run(i, _stream_recorder(FakeAsyncClient(), "record")) for i in range(N_RUNS))
    )
    replay_rec = _stream_recorder(ForbiddenClient(), "replay")
    replayed = await asyncio.gather(*(streaming_run(i, replay_rec) for i in range(N_RUNS)))

    for run in range(N_RUNS):
        (rec_t, rec_chunks), (rep_t, rep_chunks) = recorded[run], replayed[run]
        assert rep_chunks == rec_chunks
        assert_run_isolated(rep_t, run, expected_llm_spans=1)
        assert _shape(rep_t) == _shape(rec_t)
        assert _shape(fresh_store.get_trace(rep_t.trace_id)) == _shape(
            fresh_store.get_trace(rec_t.trace_id)
        )
