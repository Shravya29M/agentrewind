import asyncio

import agentrewind as al
from agentrewind.replay import Recorder

CHUNKS = [
    {"delta": "Hel"},
    {"delta": "lo!"},
    {"delta": "", "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
]
REQ = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}


def streaming_provider():
    counter = {"n": 0}

    def call(request):
        counter["n"] += 1
        yield from CHUNKS

    return call, counter


def test_stream_records_chunks_and_replays_them():
    call, counter = streaming_provider()
    rec = Recorder(call, mode="auto")

    with al.trace("live") as t:
        live = list(rec.call_stream(REQ))
    assert live == CHUNKS
    assert counter["n"] == 1
    assert t.spans[0].output["chunks"] == CHUNKS
    assert t.total_tokens() == {"prompt_tokens": 5, "completion_tokens": 2}

    with al.trace("replay") as t2:
        replayed = list(Recorder(call, mode="replay").call_stream(REQ))
    assert replayed == CHUNKS
    assert counter["n"] == 1  # provider never touched
    assert t2.spans[0].name.startswith("replay:")


def test_async_stream_records_and_replays():
    counter = {"n": 0}

    def call(request):
        counter["n"] += 1

        async def gen():
            for c in CHUNKS:
                yield c

        return gen()

    rec = Recorder(call, mode="auto")

    async def consume():
        return [c async for c in rec.acall_stream(REQ)]

    with al.trace("run"):
        a = asyncio.run(consume())
        b = asyncio.run(consume())
    assert a == b == CHUNKS
    assert counter["n"] == 1


def test_stream_without_usage_chunk():
    def call(request):
        yield {"delta": "x"}

    with al.trace("run") as t:
        chunks = list(Recorder(call, mode="record").call_stream(REQ))
    assert chunks == [{"delta": "x"}]
    assert "usage" not in t.spans[0].output
