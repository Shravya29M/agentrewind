import pytest

import agentlens as al
from agentlens.replay import Recorder, ReplayMissError, fingerprint


def flaky_provider():
    """Returns a provider whose answers change every call — like a real LLM."""
    counter = {"n": 0}

    def call(request):
        counter["n"] += 1
        return {
            "content": f"answer #{counter['n']}",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    return call, counter


def test_fingerprint_ignores_key_order():
    a = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "temperature": 0}
    b = {"temperature": 0, "messages": [{"role": "user", "content": "hi"}], "model": "m"}
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint({**a, "temperature": 1})


def test_record_then_replay_is_deterministic_and_offline():
    call, counter = flaky_provider()
    req = {"model": "m", "messages": [{"role": "user", "content": "plan my day"}]}

    with al.trace("record-run"):
        first = Recorder(call, mode="record").call(req)
    assert counter["n"] == 1

    # Replay: provider must NOT be hit, response must be identical.
    with al.trace("replay-run"):
        replayed = Recorder(call, mode="replay").call(req)
    assert counter["n"] == 1
    assert replayed == first


def test_replay_miss_raises():
    call, _ = flaky_provider()
    with al.trace("run"), pytest.raises(ReplayMissError):
        Recorder(call, mode="replay").call({"model": "m", "messages": []})


def test_auto_mode_records_miss_then_replays_hit():
    call, counter = flaky_provider()
    rec = Recorder(call, mode="auto")
    req = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    with al.trace("run"):
        a = rec.call(req)
        b = rec.call(req)
    assert counter["n"] == 1
    assert a == b


def test_recorder_creates_llm_span_with_usage():
    call, _ = flaky_provider()
    with al.trace("run") as t:
        Recorder(call, mode="record").call({"model": "m", "messages": []})
    assert t.total_tokens() == {"prompt_tokens": 3, "completion_tokens": 2}
