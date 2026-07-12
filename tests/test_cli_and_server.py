import agentlens as al
from agentlens.cli import main


def make_run(answer="sunny"):
    with al.trace("weather-agent") as t:
        with al.span("search", kind="tool", input={"q": "weather"}) as s:
            s.output = {"answer": answer}
    return t


def test_cli_list_and_show(capsys):
    t = make_run()
    assert main(["list"]) == 0
    assert t.trace_id[:16] in capsys.readouterr().out

    assert main(["show", t.trace_id]) == 0
    out = capsys.readouterr().out
    assert "weather-agent" in out
    assert "[tool] search" in out


def test_cli_show_missing_trace(capsys):
    assert main(["show", "nope"]) == 1


def test_cli_diff_exit_codes(capsys):
    a, b = make_run("sunny"), make_run("rainy")
    assert main(["diff", a.trace_id, b.trace_id]) == 2
    assert "outputs differ" in capsys.readouterr().out

    c, d = make_run("same"), make_run("same")
    assert main(["diff", c.trace_id, d.trace_id]) == 0


def test_server_endpoints():
    from fastapi.testclient import TestClient

    from agentlens.server import create_app

    t = make_run()
    client = TestClient(create_app())

    assert "AgentLens" in client.get("/").text

    traces = client.get("/api/traces").json()
    assert traces[0]["trace_id"] == t.trace_id
    assert traces[0]["num_spans"] == 1

    detail = client.get(f"/api/traces/{t.trace_id}").json()
    assert detail["spans"][0]["name"] == "search"

    assert client.get("/api/traces/does-not-exist").status_code == 404


def test_server_diff_endpoint():
    from fastapi.testclient import TestClient

    from agentlens.server import create_app

    a, b = make_run("sunny"), make_run("rainy")
    client = TestClient(create_app())

    d = client.get(f"/api/diff/{a.trace_id}/{b.trace_id}").json()
    assert d["identical"] is False
    assert d["divergences"][0]["kind"] == "output"
    assert d["divergences"][0]["left"] == {"answer": "sunny"}

    same = client.get(f"/api/diff/{a.trace_id}/{a.trace_id}").json()
    assert same["identical"] is True

    assert client.get(f"/api/diff/{a.trace_id}/missing").status_code == 404
