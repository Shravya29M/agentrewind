import asyncio

import agentrewind as al
from agentrewind.replay import Recorder


def test_async_traced_decorator_nests_spans():
    @al.traced(kind="tool")
    async def fetch(url):
        await asyncio.sleep(0)
        return f"body of {url}"

    async def agent():
        with al.trace("async-agent") as t:
            with al.span("step"):
                await fetch("http://a")
            return t

    t = asyncio.run(agent())
    step, fetch_span = t.spans
    assert fetch_span.parent_id == step.span_id
    assert fetch_span.output == "body of http://a"


def test_async_recorder_records_and_replays():
    counter = {"n": 0}

    async def provider(request):
        counter["n"] += 1
        usage = {"prompt_tokens": 1, "completion_tokens": 1}
        return {"content": f"#{counter['n']}", "usage": usage}

    rec = Recorder(provider, mode="auto")
    req = {"model": "m", "messages": [{"role": "user", "content": "x"}]}

    async def run():
        with al.trace("run"):
            a = await rec.acall(req)
            b = await rec.acall(req)
        return a, b

    a, b = asyncio.run(run())
    assert counter["n"] == 1
    assert a == b


def test_concurrent_tasks_share_trace_but_nest_correctly():
    @al.traced(kind="tool")
    async def work(i):
        await asyncio.sleep(0)
        return i

    async def agent():
        with al.trace("parallel") as t:
            await asyncio.gather(work(1), work(2), work(3))
            return t

    t = asyncio.run(agent())
    assert len(t.spans) == 3
    # gather() tasks copy context: each span is a root child, not nested in a sibling
    assert all(s.parent_id is None for s in t.spans)
    assert sorted(s.output for s in t.spans) == [1, 2, 3]


def test_canonicalize_hook_ignores_volatile_fields():
    counter = {"n": 0}

    def provider(request):
        counter["n"] += 1
        return {"content": f"#{counter['n']}"}

    def strip_request_id(req):
        return {k: v for k, v in req.items() if k != "request_id"}

    rec = Recorder(provider, mode="auto", canonicalize=strip_request_id)
    with al.trace("run"):
        a = rec.call({"model": "m", "messages": [], "request_id": "abc"})
        b = rec.call({"model": "m", "messages": [], "request_id": "xyz"})
    assert counter["n"] == 1
    assert a == b
