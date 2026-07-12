import json

import pytest

import agentrewind as al
from agentrewind.cli import main
from agentrewind.store import TraceStore


def make_trace():
    with al.trace("portable", metadata={"suite": "regression"}) as trace:
        with al.span("lookup", kind="tool", input={"query": "weather"}) as span:
            span.output = {"answer": "sunny"}
    return trace


def test_trace_export_import_round_trip(tmp_path):
    source = TraceStore(tmp_path / "source.db")
    al.configure(store=source)
    original = make_trace()

    artifact = source.export_trace(original.trace_id)
    target = TraceStore(tmp_path / "target.db")
    restored = target.import_trace(artifact)

    assert restored.trace_id == original.trace_id
    assert restored.metadata == {"suite": "regression"}
    assert restored.spans[0].output == {"answer": "sunny"}


def test_cli_export_and_import(tmp_path, fresh_store, capsys):
    trace = make_trace()
    artifact_path = tmp_path / "baseline.json"
    assert main(["export", trace.trace_id, "--output", str(artifact_path)]) == 0
    assert json.loads(artifact_path.read_text())["format"] == "agentrewind.trace.v1"

    target = TraceStore(tmp_path / "target.db")
    al.configure(store=target)
    assert main(["import", str(artifact_path)]) == 0
    assert trace.trace_id in capsys.readouterr().out
    assert target.get_trace(trace.trace_id).spans[0].name == "lookup"


def test_import_rejects_unknown_or_duplicate_artifacts(tmp_path):
    store = TraceStore(tmp_path / "traces.db")
    with pytest.raises(ValueError, match="trace v1"):
        store.import_trace({})
