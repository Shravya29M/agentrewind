"""FastAPI server: JSON API plus a minimal built-in trace viewer."""

from __future__ import annotations

from .models import Span, Trace
from .sdk import get_store


def _span_json(s: Span) -> dict:
    return {
        "span_id": s.span_id,
        "parent_id": s.parent_id,
        "name": s.name,
        "kind": s.kind.value,
        "status": s.status.value,
        "error": s.error,
        "started_at": s.started_at,
        "duration_ms": s.duration_ms,
        "input": s.input,
        "output": s.output,
        "attributes": s.attributes,
    }


def _trace_json(t: Trace, with_spans: bool = False) -> dict:
    d = {
        "trace_id": t.trace_id,
        "name": t.name,
        "status": t.status.value,
        "started_at": t.started_at,
        "ended_at": t.ended_at,
        "num_spans": len(t.spans),
        "tokens": t.total_tokens(),
        "metadata": t.metadata,
    }
    if with_spans:
        d["spans"] = [_span_json(s) for s in t.spans]
    return d


_INDEX_HTML = """<!doctype html>
<meta charset="utf-8"><title>AgentRewind</title>
<style>
 body{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1a1a2e}
 h1{font-size:1.3rem} table{border-collapse:collapse;width:100%}
 td,th{padding:.4rem .6rem;border-bottom:1px solid #e2e2ef;text-align:left}
 a{color:#4c4cd8;text-decoration:none} .err{color:#c0392b}
 pre{background:#f4f4fb;padding:.6rem;overflow-x:auto;border-radius:6px}
 .span{margin-left:calc(var(--d)*1.4rem);border-left:3px solid #ccd;padding:.3rem .6rem;margin-bottom:.3rem;background:#fafaff}
 .kind{font-size:.75rem;background:#e6e6f7;border-radius:4px;padding:0 .4rem;margin-right:.4rem}
 .div{border-left:3px solid #e67e22;padding:.3rem .6rem;margin-bottom:.5rem;background:#fdf6ef}
 .cols{display:flex;gap:.6rem}.cols pre{flex:1;min-width:0}
</style>
<h1>AgentRewind — traces</h1><div id="app">loading…</div>
<script>
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let picked = [];
function pick(id, box){
  picked = box.checked ? [...picked, id] : picked.filter(x=>x!==id);
  if (picked.length === 2) location.hash = 'diff/' + picked.join('/');
}
async function list(){
  const traces = await (await fetch('/api/traces')).json();
  picked = [];
  document.getElementById('app').innerHTML =
    '<p>Select two runs to diff them.</p>'
    + '<table><tr><th></th><th>trace</th><th>name</th><th>status</th><th>spans</th><th>tokens</th></tr>'
    + traces.map(t=>`<tr><td><input type="checkbox" onchange="pick('${t.trace_id}',this)"></td>`
    + `<td><a href="#${t.trace_id}">${t.trace_id.slice(0,8)}</a></td><td>${esc(t.name)}</td>`
    + `<td class="${t.status==='error'?'err':''}">${t.status}</td><td>${t.num_spans}</td>`
    + `<td>${t.tokens.prompt_tokens}+${t.tokens.completion_tokens}</td></tr>`).join('') + '</table>';
}
async function diffView(a, b){
  const d = await (await fetch(`/api/diff/${a}/${b}`)).json();
  const rows = d.identical
    ? '<p>Runs are identical in structure, inputs, and outputs.</p>'
    : d.divergences.map((x,i)=>`<div class="div"><b>${i+1}. [${x.kind}]</b> ${esc(x.path)} — ${esc(x.detail)}
       <div class="cols"><pre>left: ${esc(JSON.stringify(x.left,null,1))}</pre>
       <pre>right: ${esc(JSON.stringify(x.right,null,1))}</pre></div></div>`).join('');
  document.getElementById('app').innerHTML =
    `<p><a href="#">&larr; all traces</a></p>
     <h2>diff <a href="#${d.left}">${d.left.slice(0,8)}</a> vs <a href="#${d.right}">${d.right.slice(0,8)}</a></h2>`
    + `<p>${d.identical ? '' : d.divergences.length + ' divergence(s); #1 is where the runs first split.'}</p>` + rows;
}
async function show(id){
  const t = await (await fetch('/api/traces/'+id)).json();
  const byParent = {};
  t.spans.forEach(s=>{(byParent[s.parent_id||'root'] ||= []).push(s)});
  const render = (pid,d)=> (byParent[pid]||[]).map(s=>
    `<div class="span" style="--d:${d}"><span class="kind">${s.kind}</span><b>${esc(s.name)}</b>
     ${s.duration_ms!=null?Math.round(s.duration_ms)+'ms':''} ${s.error?`<span class="err">${esc(s.error)}</span>`:''}
     <pre>in: ${esc(JSON.stringify(s.input))}\nout: ${esc(JSON.stringify(s.output))}</pre></div>`
    + render(s.span_id,d+1)).join('');
  document.getElementById('app').innerHTML =
    `<p><a href="#">&larr; all traces</a></p><h2>${esc(t.name)} <small>${t.trace_id}</small></h2>` + render('root',0);
}
function route(){
  const h = location.hash.slice(1);
  if (h.startsWith('diff/')) { const [,a,b] = h.split('/'); diffView(a,b); }
  else if (h) show(h);
  else list();
}
addEventListener('hashchange', route); route();
</script>"""


def create_app():
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="AgentRewind")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    @app.get("/api/traces")
    def list_traces(limit: int = 50) -> list[dict]:
        store = get_store()
        # list_traces returns headers only; reload each trace so span counts
        # and token rollups are populated. Fine at local-tool scale.
        return [_trace_json(store.get_trace(t.trace_id)) for t in store.list_traces(limit)]

    @app.get("/api/traces/{trace_id}")
    def get_trace(trace_id: str) -> dict:
        t = get_store().get_trace(trace_id)
        if t is None:
            raise HTTPException(404, f"trace {trace_id} not found")
        return _trace_json(t, with_spans=True)

    @app.get("/api/diff/{left_id}/{right_id}")
    def diff(left_id: str, right_id: str) -> dict:
        from .diff import diff_traces

        store = get_store()
        left, right = store.get_trace(left_id), store.get_trace(right_id)
        for tid, t in ((left_id, left), (right_id, right)):
            if t is None:
                raise HTTPException(404, f"trace {tid} not found")
        divs = diff_traces(left, right)
        return {
            "left": left.trace_id,
            "right": right.trace_id,
            "identical": not divs,
            "divergences": [
                {"kind": d.kind, "path": d.path, "detail": d.detail, "left": d.left, "right": d.right}
                for d in divs
            ],
        }

    return app
