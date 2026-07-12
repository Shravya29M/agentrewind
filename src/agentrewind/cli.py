"""Command-line interface: agentrewind list | show | diff | serve."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .diff import diff_traces, format_divergences
from .sdk import get_store


def _fmt_time(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"


def cmd_list(args) -> int:
    traces = get_store().list_traces(args.limit)
    if not traces:
        print("No traces recorded yet.")
        return 0
    print(f"{'TRACE':<18}{'NAME':<30}{'STATUS':<8}{'STARTED':<20}")
    for t in traces:
        print(f"{t.trace_id[:16]:<18}{t.name[:28]:<30}{t.status.value:<8}{_fmt_time(t.started_at):<20}")
    return 0


def cmd_show(args) -> int:
    t = get_store().get_trace(args.trace_id)
    if t is None:
        print(f"trace {args.trace_id} not found", file=sys.stderr)
        return 1
    tokens = t.total_tokens()
    print(f"{t.name}  ({t.trace_id})  status={t.status.value}")
    print(f"started {_fmt_time(t.started_at)}  spans={len(t.spans)}  "
          f"tokens={tokens['prompt_tokens']}+{tokens['completion_tokens']}\n")

    def walk(parent_id: str | None, depth: int) -> None:
        for s in [x for x in t.spans if x.parent_id == parent_id]:
            dur = f"{s.duration_ms:.0f}ms" if s.duration_ms is not None else "-"
            err = f"  ERROR: {s.error}" if s.error else ""
            print(f"{'  ' * depth}[{s.kind.value}] {s.name}  {dur}{err}")
            walk(s.span_id, depth + 1)

    walk(None, 0)
    return 0


def cmd_diff(args) -> int:
    store = get_store()
    left, right = store.get_trace(args.left), store.get_trace(args.right)
    for tid, t in ((args.left, left), (args.right, right)):
        if t is None:
            print(f"trace {tid} not found", file=sys.stderr)
            return 1
    divs = diff_traces(left, right)
    print(format_divergences(divs))
    return 0 if not divs else 2


def cmd_export(args) -> int:
    artifact = get_store().export_trace(args.trace_id)
    if artifact is None:
        print(f"trace {args.trace_id} not found", file=sys.stderr)
        return 1
    text = json.dumps(artifact, indent=2, sort_keys=True, default=str) + "\n"
    if args.output == "-":
        print(text, end="")
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"exported {artifact['trace']['trace_id']} to {args.output}")
    return 0


def cmd_import(args) -> int:
    try:
        artifact = json.loads(Path(args.input).read_text(encoding="utf-8"))
        trace = get_store().import_trace(artifact, overwrite=args.overwrite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"could not import {args.input}: {exc}", file=sys.stderr)
        return 1
    print(f"imported {trace.trace_id} ({trace.name})")
    return 0


def cmd_serve(args) -> int:
    try:
        import uvicorn

        from .server import create_app
    except ImportError:
        print(
            "Server extras not installed. Run: pip install 'agentrewind[server]'",
            file=sys.stderr,
        )
        return 1
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentrewind", description="Flight recorder for LLM agents"
    )
    parser.add_argument("--db", help="path to traces db (default ~/.agentrewind/traces.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list recent traces")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="print a trace's span tree")
    p.add_argument("trace_id")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("diff", help="diff two traces of the same agent")
    p.add_argument("left")
    p.add_argument("right")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("export", help="write a portable trace JSON artifact")
    p.add_argument("trace_id")
    p.add_argument("--output", "-o", default="-", help="artifact path, or - for stdout")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("import", help="load a portable trace JSON artifact")
    p.add_argument("input", help="artifact JSON path")
    p.add_argument("--overwrite", action="store_true", help="replace a trace with the same id")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("serve", help="start the web viewer")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4317)
    p.set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    if args.db:
        from .sdk import configure

        configure(db_path=args.db)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
