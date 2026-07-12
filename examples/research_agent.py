"""Demo: a toy research agent traced with AgentLens, run twice with a seeded
"regression", then diffed to locate exactly where behavior changed.

Uses a mock LLM so it runs offline. Try:

    python examples/research_agent.py
    agentlens list
    agentlens diff <run1> <run2>
    agentlens serve   # then open http://127.0.0.1:4317
"""

import agentlens as al
from agentlens.replay import Recorder

PLAYBOOK_V1 = {"plan": "search(weather Boston)", "answer": "72F and sunny in Boston."}
PLAYBOOK_V2 = {"plan": "search(weather Boston MA)", "answer": "72F and sunny in Boston."}


def mock_llm(playbook):
    def call(request):
        step = request["messages"][-1]["content"]
        key = "plan" if "decide" in step else "answer"
        return {
            "content": playbook[key],
            "usage": {"prompt_tokens": len(step.split()), "completion_tokens": 8},
        }

    return call


@al.traced(kind="tool")
def search(query: str) -> str:
    return f"[3 results for {query!r}] Boston: 72F, sunny."


def run_agent(name: str, playbook: dict) -> str:
    llm = Recorder(mock_llm(playbook), mode="record")
    with al.trace(name) as t:
        plan = llm.call(
            {"model": "mock-4o", "messages": [{"role": "user", "content": "decide next step"}]}
        )["content"]
        query = plan.split("(", 1)[1].rstrip(")")
        evidence = search(query)
        answer_prompt = f"answer using {evidence}"
        llm.call({"model": "mock-4o", "messages": [{"role": "user", "content": answer_prompt}]})
    return t.trace_id


if __name__ == "__main__":
    run1 = run_agent("research-agent", PLAYBOOK_V1)
    run2 = run_agent("research-agent", PLAYBOOK_V2)  # prompt tweak = seeded regression
    print(f"Recorded two runs:\n  {run1}\n  {run2}\n")
    print(f"Find where they diverged:\n  agentlens diff {run1} {run2}")
