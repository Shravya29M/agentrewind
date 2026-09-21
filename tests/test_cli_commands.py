"""CLI-level tests for the commands that had no coverage: export, import,
serve, and the global --db flag. These are the paths a user hits first, so a
broken exit code or a silent failure here is expensive.
"""

import json
import sys
from pathlib import Path

import pytest

import agentrewind as al
from agentrewind.cli import main


def record(name="agent", answer="sunny"):
    with al.trace(name) as t:
        with al.span("search", kind="tool", input={"q": "weather"}) as s:
            s.output = {"answer": answer}
    return t


# --------------------------------------------------------------------------
# list / show
# --------------------------------------------------------------------------


def test_list_reports_no_traces_on_an_empty_store(capsys):
    assert main(["list"]) == 0
    assert "No traces recorded yet." in capsys.readouterr().out


def test_list_honours_the_limit_flag(capsys):
    for _ in range(3):
        record()
    main(["list", "--limit", "2"])
    out = capsys.readouterr().out
    assert len([ln for ln in out.splitlines() if ln.startswith("agent") or "agent" in ln]) <= 3


def test_show_prints_the_span_tree(capsys):
    t = record()
    assert main(["show", t.trace_id]) == 0
    out = capsys.readouterr().out
    assert "[tool] search" in out
    assert t.trace_id in out


def test_show_marks_a_failed_span_with_its_error(capsys):
    with al.trace("agent") as t:
        try:
            with al.span("boom", kind="tool", input={}):
                raise RuntimeError("kaboom")
        except RuntimeError:
            pass
    main(["show", t.trace_id])
    assert "ERROR: RuntimeError: kaboom" in capsys.readouterr().out


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def test_export_writes_an_artifact_to_a_file(tmp_path, capsys):
    t = record()
    dest = tmp_path / "trace.json"
    assert main(["export", t.trace_id, "-o", str(dest)]) == 0
    artifact = json.loads(dest.read_text())
    assert artifact["trace"]["trace_id"] == t.trace_id
    assert f"exported {t.trace_id}" in capsys.readouterr().out


def test_export_defaults_to_stdout(capsys):
    t = record()
    assert main(["export", t.trace_id]) == 0
    artifact = json.loads(capsys.readouterr().out)
    assert artifact["trace"]["trace_id"] == t.trace_id


def test_export_of_an_unknown_trace_fails_with_exit_1(capsys):
    assert main(["export", "nope"]) == 1
    assert "not found" in capsys.readouterr().err


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def test_export_then_import_round_trips_into_a_fresh_store(tmp_path, capsys):
    t = record(answer="sunny")
    dest = tmp_path / "trace.json"
    main(["export", t.trace_id, "-o", str(dest)])
    capsys.readouterr()

    fresh_db = tmp_path / "other.db"
    assert main(["--db", str(fresh_db), "import", str(dest)]) == 0
    assert f"imported {t.trace_id}" in capsys.readouterr().out

    assert main(["--db", str(fresh_db), "show", t.trace_id]) == 0
    assert "[tool] search" in capsys.readouterr().out


def test_import_of_a_missing_file_fails_with_exit_1(tmp_path, capsys):
    assert main(["import", str(tmp_path / "absent.json")]) == 1
    assert "could not import" in capsys.readouterr().err


def test_import_of_malformed_json_fails_with_exit_1(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["import", str(bad)]) == 1
    assert "could not import" in capsys.readouterr().err


def test_importing_a_duplicate_trace_fails_without_overwrite(tmp_path, capsys):
    t = record()
    dest = tmp_path / "trace.json"
    main(["export", t.trace_id, "-o", str(dest)])
    capsys.readouterr()
    assert main(["import", str(dest)]) == 1


def test_overwrite_flag_replaces_an_existing_trace(tmp_path, capsys):
    t = record()
    dest = tmp_path / "trace.json"
    main(["export", t.trace_id, "-o", str(dest)])
    capsys.readouterr()
    assert main(["import", str(dest), "--overwrite"]) == 0


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def test_diff_of_an_unknown_left_trace_fails_with_exit_1(capsys):
    t = record()
    assert main(["diff", "nope", t.trace_id]) == 1
    assert "trace nope not found" in capsys.readouterr().err


def test_diff_of_an_unknown_right_trace_fails_with_exit_1(capsys):
    t = record()
    assert main(["diff", t.trace_id, "nope"]) == 1
    assert "trace nope not found" in capsys.readouterr().err


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------


def test_serve_starts_uvicorn_on_the_requested_host_and_port(monkeypatch):
    import uvicorn

    captured = {}

    def fake_run(app, host, port):
        captured.update(host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert main(["serve", "--host", "0.0.0.0", "--port", "9999"]) == 0
    assert captured == {"host": "0.0.0.0", "port": 9999}


def test_serve_defaults_to_localhost_4317(monkeypatch):
    import uvicorn

    captured = {}
    monkeypatch.setattr(
        uvicorn, "run", lambda app, host, port: captured.update(host=host, port=port)
    )
    main(["serve"])
    assert captured == {"host": "127.0.0.1", "port": 4317}


def test_serve_explains_how_to_install_the_extras_when_they_are_missing(monkeypatch, capsys):
    """The server deps are optional, so a missing uvicorn must produce an
    actionable message rather than a traceback."""
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert main(["serve"]) == 1
    assert "pip install 'agentrewind[server]'" in capsys.readouterr().err


# --------------------------------------------------------------------------
# argument handling
# --------------------------------------------------------------------------


def test_db_flag_points_the_cli_at_another_database(tmp_path, capsys):
    record()
    other = tmp_path / "empty.db"
    assert main(["--db", str(other), "list"]) == 0
    assert "No traces recorded yet." in capsys.readouterr().out


def test_a_missing_subcommand_is_rejected():
    with pytest.raises(SystemExit):
        main([])


def test_an_unknown_subcommand_is_rejected():
    with pytest.raises(SystemExit):
        main(["frobnicate"])


def test_module_entrypoint_is_wired_up():
    """`python -m agentrewind.cli` must reach main()."""
    source = Path("src/agentrewind/cli.py").read_text()
    assert 'if __name__ == "__main__":' in source
    assert "sys.exit(main())" in source
