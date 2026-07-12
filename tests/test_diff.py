import agentrewind as al
from agentrewind.diff import diff_traces, format_divergences


def run_agent(tool_answer: str, extra_step: bool = False):
    with al.trace("agent") as t:
        with al.span("plan", kind="llm", input={"prompt": "what next?"}) as s:
            s.output = {"decision": "use tool"}
        with al.span("search", kind="tool", input={"query": "weather"}) as s:
            s.output = {"answer": tool_answer}
        if extra_step:
            with al.span("retry", kind="tool", input={"query": "weather again"}):
                pass
    return t


def test_identical_runs_have_no_divergence():
    a, b = run_agent("sunny"), run_agent("sunny")
    assert diff_traces(a, b) == []
    assert "identical" in format_divergences([])


def test_output_divergence_detected():
    a, b = run_agent("sunny"), run_agent("rainy")
    divs = diff_traces(a, b)
    assert len(divs) == 1
    assert divs[0].kind == "output"
    assert "tool:search" in divs[0].path
    assert divs[0].left == {"answer": "sunny"}
    assert divs[0].right == {"answer": "rainy"}


def test_structural_divergence_detected():
    a, b = run_agent("sunny"), run_agent("sunny", extra_step=True)
    divs = diff_traces(a, b)
    assert any(d.kind == "structure" and "retry" in d.path and "right" in d.detail for d in divs)


def test_nested_divergence_path():
    def nested(answer):
        with al.trace("agent") as t:
            with al.span("step"):
                with al.span("llm-call", kind="llm", input={"p": 1}) as s:
                    s.output = answer
        return t

    divs = diff_traces(nested("a"), nested("b"))
    assert len(divs) == 1
    assert divs[0].path == "/span:step/llm:llm-call"


def test_format_is_readable():
    a, b = run_agent("sunny"), run_agent("rainy")
    text = format_divergences(diff_traces(a, b))
    assert "1 divergence" in text
    assert "sunny" in text and "rainy" in text
