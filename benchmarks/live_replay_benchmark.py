"""Live-vs-replay wall-clock benchmark against a real LLM provider (OpenAI).

A small research agent answers 10 questions. Each question costs exactly 5 real LLM calls
(plan → tool choice via function calling → draft → critique → final), and the chosen tool
(a calculator or a local fact lookup) runs locally. That is 50 LLM calls per run.

Every run:
  1. records a live run into a fresh SQLite store (real API calls),
  2. reopens that store from disk and replays the same agent run. The provider function
     raises if it is ever called, so replay provably makes zero API calls.

Reported: wall-clock seconds for the whole agent run (LLM calls, tool calls, trace writes),
live vs replay, with the median over --runs. Requires OPENAI_API_KEY and `pip install openai`.
Costs about 50 × --runs real API calls.

    python benchmarks/live_replay_benchmark.py --runs 3 --output live.json
"""

from __future__ import annotations

import argparse
import ast
import json
import operator
import os
import platform
import statistics
import sys
import tempfile
import time

import agentrewind as al
from agentrewind.replay import Recorder
from agentrewind.store import SQLiteStore

QUESTIONS = [
    "How many seconds are there in a leap year?",
    "What is the boiling point of water in Fahrenheit at sea level, and what is that in Kelvin?",
    "If a train travels 342 km in 2.25 hours, what is its average speed in m/s?",
    "Which planet in our solar system has the most confirmed moons, according to the facts?",
    "What is 17% of 2,340, rounded to two decimals?",
    "How many days did the Apollo 11 mission last, according to the facts?",
    "What is the compound value of $1,000 at 5% annual interest after 10 years?",
    "What is the speed of light in km/s, and how long does light take to cross 1 AU in minutes?",
    "What year was the Python programming language first released, according to the facts?",
    "How many kilobytes are in 3.5 mebibytes?",
]

FACTS = {
    "saturn moons": "Saturn has 274 confirmed moons (2025 count), the most of any planet.",
    "apollo 11": "Apollo 11 launched July 16, 1969 and splashed down July 24, 1969: 8 days.",
    "python release": "Python 0.9.0 was first released in February 1991.",
    "speed of light": "The speed of light in vacuum is 299,792.458 km/s.",
    "astronomical unit": "1 astronomical unit (AU) is 149,597,870.7 km.",
    "water boiling": "Water boils at 212 °F (100 °C, 373.15 K) at sea level.",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate an arithmetic expression (+ - * / ** and parentheses).",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_fact",
            "description": "Look up a reference fact. Topics: " + ", ".join(FACTS),
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    },
]

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


@al.traced(kind="tool")
def calculator(expression: str) -> str:
    try:
        return str(_eval(ast.parse(expression.replace(",", ""), mode="eval").body))
    except Exception as exc:
        return f"error: {exc}"


@al.traced(kind="tool")
def lookup_fact(topic: str) -> str:
    t = topic.lower()
    for key, fact in FACTS.items():
        if key in t or all(w in t for w in key.split()):
            return fact
    return "No fact found. Known topics: " + ", ".join(FACTS)


def run_agent(llm: Recorder, model: str) -> list[str]:
    """10 questions × 5 LLM calls. Returns the final answers."""

    def chat(messages, **extra):
        return llm.call({"model": model, "temperature": 0, "max_tokens": 300,
                         "messages": messages, **extra})

    def text(resp):
        return resp["choices"][0]["message"]["content"] or ""

    finals = []
    with al.trace("live-replay-benchmark", metadata={"model": model}):
        for q in QUESTIONS:
            with al.span("question", input={"question": q}) as qspan:
                system = {"role": "system", "content": "You are a precise research assistant."}
                plan = text(chat([system, {"role": "user", "content":
                    f"Question: {q}\nIn 2-3 short steps, plan how to answer it."}]))

                choice = chat(
                    [system, {"role": "user", "content":
                        f"Question: {q}\nPlan: {plan}\nCall exactly one tool to make progress."}],
                    tools=TOOLS, tool_choice="required",
                )
                call = choice["choices"][0]["message"]["tool_calls"][0]["function"]
                args = json.loads(call["arguments"])
                tool_fn = {"calculator": calculator, "lookup_fact": lookup_fact}[call["name"]]
                observation = tool_fn(**args)

                draft = text(chat([system, {"role": "user", "content":
                    f"Question: {q}\nPlan: {plan}\nTool {call['name']}({args}) returned: "
                    f"{observation}\nWrite a concise answer."}]))
                critique = text(chat([system, {"role": "user", "content":
                    f"Question: {q}\nDraft answer: {draft}\nList any errors or gaps briefly."}]))
                final = text(chat([system, {"role": "user", "content":
                    f"Question: {q}\nDraft: {draft}\nCritique: {critique}\n"
                    "Give the corrected final answer in one or two sentences."}]))
                qspan.output = final
                finals.append(final)
    return finals


def one_run(client, model: str) -> dict:
    live_calls = 0

    def live_provider(request):
        nonlocal live_calls
        live_calls += 1
        return client.chat.completions.create(**request).model_dump()

    def offline_provider(request):
        raise AssertionError("replay attempted a provider call")

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "traces.db")

        store = SQLiteStore(path)
        al.configure(store=store)
        t0 = time.perf_counter()
        live = run_agent(Recorder(live_provider, mode="record", store=store), model)
        live_s = time.perf_counter() - t0
        store.close()

        store = SQLiteStore(path)  # replay from what is on disk, not from memory
        al.configure(store=store)
        t0 = time.perf_counter()
        replayed = run_agent(Recorder(offline_provider, mode="replay", store=store), model)
        replay_s = time.perf_counter() - t0
        traces = store.list_traces()
        store.close()

    return {
        "llm_calls_live": live_calls,
        "llm_calls_replay": 0,  # offline_provider would have raised
        "live_seconds": round(live_s, 4),
        "replay_seconds": round(replay_s, 4),
        "speedup": round(live_s / replay_s, 1),
        "replay_matches_live": replayed == live,
        "traces_in_store": len(traces),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="gpt-4.1-mini-2025-04-14")
    parser.add_argument("--output")
    args = parser.parse_args()

    import openai

    client = openai.OpenAI()
    runs = []
    for i in range(args.runs):
        r = one_run(client, args.model)
        runs.append(r)
        print(f"run {i + 1}: {r['llm_calls_live']} calls  live {r['live_seconds']} s  "
              f"replay {r['replay_seconds']} s  match={r['replay_matches_live']}",
              file=sys.stderr)

    report = {
        "environment": {
            "platform": platform.platform(), "python": platform.python_version(),
            "agentrewind": al.__version__, "openai": openai.__version__, "model": args.model,
            "store": "SQLite file (default backend)",
        },
        "median_live_seconds": statistics.median(r["live_seconds"] for r in runs),
        "median_replay_seconds": statistics.median(r["replay_seconds"] for r in runs),
        "runs": runs,
    }
    text = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
