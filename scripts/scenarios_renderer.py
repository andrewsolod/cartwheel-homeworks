"""Render runner results as one self-contained HTML page for human review.

Usage:

    uv run python -m scripts.scenarios_renderer scenarios/pilot-results.jsonl \
      --scenarios scenarios/pilot_scenarios.jsonl --output reports/pilot-report.html \
      [--review scenarios/pilot_review.jsonl] [--no-langfuse] [--db FRESH_SEED_DB]

Each scenario becomes a card with its tuple, the recorded extra metadata, every
user and agent turn, and (unless ``--no-langfuse``) the tool calls and a trace
link per turn.  ``--db`` adds the database record each scenario is based on.
Pass a freshly seeded copy, not ``data/cartwheel.db`` after the run: the run's
refunds and cancellations change the records the extra metadata describes.  When a scenario was run more than once, the newest traces are
used.  The page has filters and a search box and needs no network access to open.

Each card also has a review form (scenario_valid, confirmed_failure, evidence,
scenario_change).  Drafts are kept in the browser; the Export button downloads
the review JSONL, which you save into ``scenarios/``.  ``--review`` starts the
form from an existing review file and sets the exported file name.

Scenario mode reviews scenarios before any run (Part C's 15-scenario review):

    uv run python -m scripts.scenarios_renderer scenarios/support_scenarios.jsonl --scenario-review \
      --ids support-0049,support-0212 [--db data/cartwheel.db] [--review scenarios/support_review.jsonl]

Each card shows the tuple, the extra metadata, every user message, and the
database facts of the selected order or product (``--db``, default data/cartwheel.db).  The form records decision
(accept, revise, reject), reason, and change; Export downloads support_review.jsonl.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from scenarios.validate import load_jsonl

CSS = """
:root { --bg:#f7f7f5; --card:#fff; --ink:#1d1d1b; --muted:#6b6b66; --line:#e3e2dc;
  --user:#eef3fb; --agent:#f3f1ea; --tool:#fbfaf6; --ok:#1f7a4d; --bad:#b3261e; --warn:#8a5a00;
  --chip:#efeee8; --accent:#2f5bd3; }
@media (prefers-color-scheme: dark) { :root { --bg:#161615; --card:#1f1f1d; --ink:#ecebe6;
  --muted:#a09f98; --line:#33332f; --user:#1d2636; --agent:#26251f; --tool:#1b1b19; --ok:#5cc28f;
  --bad:#f08a80; --warn:#e0b45c; --chip:#2b2b27; --accent:#8fb0ff; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header { position:sticky; top:0; z-index:2; background:var(--bg); border-bottom:1px solid var(--line);
  padding:12px 16px; }
h1 { font-size:18px; margin:0 0 6px; }
.summary { color:var(--muted); font-size:13px; margin-bottom:8px; }
.controls { display:flex; flex-wrap:wrap; gap:8px; }
.controls input, .controls select { font:inherit; font-size:13px; padding:5px 8px; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--ink); }
.controls input { flex:1 1 220px; }
main { max-width:980px; margin:0 auto; padding:16px; }
details.card { background:var(--card); border:1px solid var(--line); border-radius:10px; margin:0 0 12px; }
details.card > summary { cursor:pointer; padding:12px 14px; list-style:none; display:flex; flex-wrap:wrap;
  gap:6px 10px; align-items:center; }
details.card > summary::-webkit-details-marker { display:none; }
.sid { font-weight:600; font-family:ui-monospace, Menlo, monospace; font-size:14px; }
.first { color:var(--muted); font-size:13px; flex:1 1 100%; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }
.chip { background:var(--chip); border-radius:999px; padding:1px 8px; font-size:12px; color:var(--muted); }
.badge { border-radius:999px; padding:1px 8px; font-size:12px; font-weight:600; border:1px solid currentColor; }
.ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }
.body { padding:0 14px 14px; }
.expected { border-left:3px solid var(--accent); padding:8px 12px; background:var(--tool); border-radius:6px;
  margin-bottom:12px; font-size:14px; }
.expected code { font-size:13px; }
.review { border-left:3px solid var(--warn); padding:8px 12px; background:var(--tool); border-radius:6px;
  margin-bottom:12px; font-size:14px; }
.turn { margin:10px 0; }
.msg { border-radius:8px; padding:8px 12px; white-space:pre-wrap; word-wrap:break-word; }
.user { background:var(--user); } .agent { background:var(--agent); }
.who { font-size:12px; color:var(--muted); margin:6px 0 2px; display:flex; gap:8px; }
.who a { color:var(--accent); }
.tools { margin:6px 0 0 12px; font-size:13px; }
.tools details { background:var(--tool); border:1px solid var(--line); border-radius:6px; margin:4px 0;
  padding:4px 8px; }
.tools summary { cursor:pointer; font-family:ui-monospace, Menlo, monospace; font-size:12.5px; }
.tools pre { margin:6px 0 2px; white-space:pre-wrap; word-break:break-word; font-size:12px; }
.none { color:var(--muted); font-size:12px; font-style:italic; }
.rv { border:1px dashed var(--line); border-radius:8px; padding:10px 12px; margin-top:14px; font-size:14px;
  display:grid; grid-template-columns:max-content 1fr; gap:8px 12px; align-items:start; }
.rv > b { grid-column:1 / -1; }
.rv label { margin-right:12px; white-space:nowrap; }
.rv textarea { width:100%; min-height:60px; font:inherit; font-size:13px; padding:6px 8px; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--ink); resize:vertical; }
.rv .off { opacity:.45; }
button { font:inherit; font-size:13px; padding:5px 10px; border-radius:6px; border:1px solid var(--line);
  background:var(--card); color:var(--ink); cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
#msg { font-size:13px; color:var(--warn); flex:1 1 100%; }
#msg:empty { display:none; }
@media (max-width:600px) { .rv { grid-template-columns:1fr; } }
code, pre { font-family:ui-monospace, Menlo, monospace; }
"""

JS_FILTERS = """
const q = document.getElementById('q'), sel = [...document.querySelectorAll('select[data-f]')];
const cards = [...document.querySelectorAll('details.card')], shown = document.getElementById('shown');
function apply() {
  const text = q.value.toLowerCase(); let n = 0;
  for (const c of cards) {
    let ok = !text || c.textContent.toLowerCase().includes(text);
    for (const s of sel) if (s.value && c.dataset[s.dataset.f] !== s.value) ok = false;
    c.hidden = !ok; if (ok) n++;
  }
  shown.textContent = n;
}
q.addEventListener('input', apply); sel.forEach(s => s.addEventListener('change', apply));
document.getElementById('expand').addEventListener('click', () => {
  const open = !cards.every(c => c.hidden || c.open); cards.forEach(c => { if (!c.hidden) c.open = open; });
});
"""

JS_RESULT_REVIEW = """
// Review form: drafts live in this browser; Export writes the review JSONL.
const META = JSON.parse(document.getElementById('review-init').textContent);
const KEY = 'cartwheel-review:' + META.file;
let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
const state = {};
for (const c of cards) { const sid = c.dataset.sid; state[sid] = saved[sid] || META.reviews[sid] || {}; }
const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
const BADGES = { failure: ['bad', 'confirmed failure'], invalid: ['warn', 'scenario invalid'],
                 pass: ['ok', 'reviewed: no failure'], unreviewed: ['', ''] };
function decision(r) {
  if (typeof r.scenario_valid !== 'boolean') return 'unreviewed';
  if (!r.scenario_valid) return 'invalid';
  return r.confirmed_failure === true ? 'failure' : r.confirmed_failure === false ? 'pass' : 'unreviewed';
}
function refresh(card) {
  const r = state[card.dataset.sid], d = decision(r), b = card.querySelector('.rvbadge');
  card.dataset.decision = d; b.className = 'badge rvbadge ' + BADGES[d][0]; b.textContent = BADGES[d][1];
  b.hidden = d === 'unreviewed';
  card.querySelector('.fail-row').classList.toggle('off', r.scenario_valid === false);
  card.querySelectorAll('input[data-k="confirmed_failure"]').forEach(i => i.disabled = r.scenario_valid === false);
}
function counts() {
  const ds = cards.map(c => c.dataset.decision);
  document.getElementById('reviewed').textContent = ds.filter(d => d !== 'unreviewed').length;
  document.getElementById('flagged').textContent = ds.filter(d => d === 'failure').length;
}
for (const c of cards) {
  const r = state[c.dataset.sid];
  c.querySelectorAll('.rv input[type=radio]').forEach(i => {
    i.checked = String(r[i.dataset.k]) === i.value;
    i.addEventListener('change', () => {
      r[i.dataset.k] = i.value === 'true';
      if (i.dataset.k === 'scenario_valid' && !r.scenario_valid) r.confirmed_failure = false;
      c.querySelectorAll('input[data-k="confirmed_failure"]').forEach(x => x.checked = String(r.confirmed_failure) === x.value);
      persist(); refresh(c); counts(); apply();
    });
  });
  c.querySelectorAll('.rv textarea').forEach(t => {
    t.value = r[t.dataset.k] || '';
    t.addEventListener('input', () => { r[t.dataset.k] = t.value; persist(); });
  });
  refresh(c);
}
counts();
function rows() {
  const out = [], problems = [];
  for (const c of cards) {
    const sid = c.dataset.sid, r = state[sid];
    if (typeof r.scenario_valid !== 'boolean') continue;
    const evidence = (r.evidence || '').trim(), change = (r.scenario_change || '').trim();
    if (typeof r.confirmed_failure !== 'boolean') problems.push(sid + ': confirmed_failure not set');
    if (!evidence) problems.push(sid + ': evidence is empty');
    out.push({ scenario_id: sid, scenario_valid: r.scenario_valid,
               confirmed_failure: r.scenario_valid ? r.confirmed_failure === true : false,
               evidence, scenario_change: change || null });
  }
  return { out, problems };
}
const msg = document.getElementById('msg');
document.getElementById('export').addEventListener('click', () => {
  const { out, problems } = rows();
  if (!out.length) { msg.textContent = 'Nothing reviewed yet.'; return; }
  if (problems.length && !confirm('Export anyway?\\n\\n' + problems.join('\\n'))) return;
  const blob = new Blob([out.map(r => JSON.stringify(r)).join('\\n') + '\\n'], { type: 'application/jsonl' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = META.file; a.click();
  const failures = out.filter(r => r.scenario_valid && r.confirmed_failure).length;
  msg.textContent = `Exported ${out.length} reviews (${failures} confirmed failures). Save it as scenarios/${META.file}.`
    + (problems.length ? ' Incomplete: ' + problems.join('; ') : '');
});
"""

JS_SCENARIO_REVIEW = """
// Scenario review form: decision, reason, change. Drafts live in this browser.
const META = JSON.parse(document.getElementById('review-init').textContent);
const KEY = 'cartwheel-review:' + META.file;
let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
const state = {};
for (const c of cards) { const sid = c.dataset.sid; state[sid] = saved[sid] || META.reviews[sid] || {}; }
const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
const BADGES = { accept: 'ok', revise: 'warn', reject: 'bad' };
function refresh(card) {
  const d = state[card.dataset.sid].decision || 'unreviewed', b = card.querySelector('.rvbadge');
  card.dataset.decision = d; b.className = 'badge rvbadge ' + (BADGES[d] || ''); b.textContent = d;
  b.hidden = d === 'unreviewed';
}
function counts() {
  const ds = cards.map(c => c.dataset.decision);
  document.getElementById('reviewed').textContent = ds.filter(d => d !== 'unreviewed').length;
  document.getElementById('flagged').textContent = ds.filter(d => d === 'revise' || d === 'reject').length;
}
for (const c of cards) {
  const r = state[c.dataset.sid];
  c.querySelectorAll('.rv input[type=radio]').forEach(i => {
    i.checked = r.decision === i.value;
    i.addEventListener('change', () => { r.decision = i.value; persist(); refresh(c); counts(); apply(); });
  });
  c.querySelectorAll('.rv textarea').forEach(t => {
    t.value = r[t.dataset.k] || '';
    t.addEventListener('input', () => { r[t.dataset.k] = t.value; persist(); });
  });
  refresh(c);
}
counts();
const msg = document.getElementById('msg');
document.getElementById('export').addEventListener('click', () => {
  const out = [], problems = [];
  for (const c of cards) {
    const sid = c.dataset.sid, r = state[sid];
    if (!r.decision) continue;
    const reason = (r.reason || '').trim(), change = (r.change || '').trim();
    if (!reason) problems.push(sid + ': reason is empty');
    if (r.decision !== 'accept' && !change) problems.push(sid + ': ' + r.decision + ' without a change');
    out.push({ scenario_id: sid, decision: r.decision, reason, change: change || null });
  }
  if (!out.length) { msg.textContent = 'Nothing reviewed yet.'; return; }
  if (problems.length && !confirm('Export anyway?\\n\\n' + problems.join('\\n'))) return;
  const blob = new Blob([out.map(r => JSON.stringify(r)).join('\\n') + '\\n'], { type: 'application/jsonl' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = META.file; a.click();
  const flagged = out.filter(r => r.decision !== 'accept').length;
  msg.textContent = `Exported ${out.length} decisions (${flagged} revise or reject). Save it as scenarios/${META.file}.`
    + (problems.length ? ' Incomplete: ' + problems.join('; ') : '');
});
"""


def _e(value: Any) -> str:
    return html.escape(str(value))


def _text(value: str) -> str:
    """Escape a reply and keep the agent's **bold** markup readable."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _e(value))


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return _e(value)
    return _e(json.dumps(value, indent=2, ensure_ascii=False))


def fetch_turn_traces(results: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], str]:
    """Return the newest traces per scenario (one per turn) and the Langfuse host."""
    from langfuse import Langfuse

    from observability.instrument import load_env
    from scenarios.export_langfuse import export_scenario_traces

    load_env()
    host = (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
            or "http://localhost:3000").rstrip("/")
    wanted = {r["scenario_id"]: len(r.get("turns") or []) for r in results}
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for trace in export_scenario_traces(set(wanted), Langfuse()):
        by_scenario.setdefault(trace["cartwheel_scenario_id"], []).append(trace)
    for sid, traces in by_scenario.items():
        traces.sort(key=lambda t: t.get("timestamp") or "")
        by_scenario[sid] = traces[-wanted[sid]:] if wanted[sid] else []
    return by_scenario, host


def _tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    observations = trace.get("observations") or []
    tools = [o for o in observations if o.get("type") == "TOOL"]
    return sorted(tools, key=lambda o: o.get("startTime") or "")


def _tool_status(output: Any) -> tuple[str, str]:
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return "", ""
    if isinstance(output, dict) and output.get("ok") is False:
        return "bad", output.get("error", "error")
    if isinstance(output, dict) and output.get("status"):
        return "ok", output["status"]
    return "ok", "ok"


def _review_form(sid: str) -> str:
    def radios(key: str) -> str:
        return "".join(f'<label><input type="radio" name="{_e(key)}-{_e(sid)}" data-k="{key}" value="{v}"> '
                       f'{label}</label>' for v, label in (("true", "yes"), ("false", "no")))
    return ('<div class="rv"><b>Your review</b>'
            f'<span>Scenario valid</span><span>{radios("scenario_valid")}</span>'
            f'<span class="fail-row">Confirmed failure</span><span class="fail-row">{radios("confirmed_failure")}</span>'
            '<span>Evidence</span><textarea data-k="evidence" placeholder="The database value, policy, '
            'tool result, or requirement that supports your decision"></textarea>'
            '<span>Scenario change</span><textarea data-k="scenario_change" placeholder="Leave empty for '
            'null (no revision needed)"></textarea></div>')


def _facts_block(facts: str | None, db_name: str) -> str:
    """The database record a scenario is based on; empty when no database was given."""
    if facts is None:
        return ""
    text = facts or "no single order or product (see the expected reason for the records involved)"
    return f'<div class="review"><b>Database record</b> <span class="chip">{_e(db_name)}</span> {_e(text)}</div>'


def render_card(result: dict[str, Any], scenario: dict[str, Any] | None,
                traces: list[dict[str, Any]] | None, host: str, facts: str | None = None,
                db_name: str = "") -> str:
    sid = result["scenario_id"]
    tuple_ = (scenario or {}).get("tuple", {})
    expected = result.get("expected") or {}
    turns = result.get("turns") or []
    status_cls = "ok" if result.get("status") == "completed" else "bad"
    data = {"sid": sid, "group": result.get("scenario_group", ""), "role": tuple_.get("role", ""),
            "intent": tuple_.get("intent", ""), "status": result.get("status", ""), "decision": "unreviewed"}
    attrs = " ".join(f'data-{k}="{_e(v)}"' for k, v in data.items())
    chips = "".join(f'<span class="chip">{_e(tuple_[k])}</span>' for k in
                    ("role", "intent", "access", "record_state", "difficulty", "user_style") if k in tuple_)
    first = turns[0]["user"] if turns else ""
    out = [f'<details class="card" {attrs}><summary>',
           f'<span class="sid">{_e(sid)}</span>',
           f'<span class="chip">{_e(result.get("scenario_group", ""))}</span>{chips}',
           f'<span class="badge {status_cls}">{_e(result.get("status", ""))}</span>'
           '<span class="badge rvbadge" hidden></span>',
           f'<span class="first">{_e(first)}</span></summary><div class="body">']

    label = expected.get("outcome") or expected.get("evaluation", "")
    detail = expected.get("reason") or expected.get("criterion") or ""
    source = expected.get("source") or {}
    out.append(f'<div class="expected"><b>Expected:</b> <code>{_e(label)}</code><br>{_e(detail)}')
    if source:
        out.append(f'<br><span class="chip">{_e(source.get("type", ""))}: {_e(source.get("reference", ""))}</span>')
    out.append("</div>")
    out.append(_facts_block(facts, db_name))
    if result.get("error"):
        out.append(f'<div class="review"><b>Run error:</b> {_e(result["error"])}</div>')

    for i, turn in enumerate(turns):
        trace = traces[i] if traces and i < len(traces) else None
        link = (f'<a href="{_e(host)}/project/{_e(trace.get("projectId", ""))}/traces/{_e(trace["id"])}" '
                f'target="_blank" rel="noopener">trace</a>') if trace else ""
        out.append(f'<div class="turn"><div class="who">User · turn {i + 1}</div>'
                   f'<div class="msg user">{_e(turn["user"])}</div>')
        if traces is not None:
            calls = _tool_calls(trace) if trace else []
            out.append('<div class="tools">')
            if not trace:
                out.append('<div class="none">no trace found for this turn</div>')
            elif not calls:
                out.append('<div class="none">no tool calls</div>')
            for call in calls:
                cls, word = _tool_status(call.get("output"))
                out.append(f'<details><summary>{_e(call.get("name", "tool"))} '
                           f'<span class="badge {cls}">{_e(word)}</span></summary>'
                           f'<pre>input:  {_pretty(call.get("input"))}</pre>'
                           f'<pre>output: {_pretty(call.get("output"))}</pre></details>')
            out.append("</div>")
        out.append(f'<div class="who">Agent {link}</div>'
                   f'<div class="msg agent">{_text(turn.get("agent") or "")}</div></div>')
    out.append(_review_form(sid))
    out.append("</div></details>")
    return "".join(out)


def _page(title: str, summary: str, counter: str, controls: str, cards: str, init: dict[str, Any],
          js: str, total: int) -> str:
    init_json = json.dumps(init).replace("</", "<\\/")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{_e(title)}</title><style>{CSS}</style></head><body>'
            f'<header><h1>{_e(title)}</h1><div class="summary">{_e(summary)} · showing '
            f'<span id="shown">{total}</span> · <span id="reviewed">0</span> reviewed, '
            f'<span id="flagged">0</span> {_e(counter)}</div>'
            f'<div class="controls">{controls}</div></header>'
            f'<main>{cards}</main><script type="application/json" id="review-init">{init_json}</script>'
            f'<script>{JS_FILTERS}{js}</script></body></html>')


def _options(field: str, values: list[str]) -> str:
    opts = "".join(f'<option value="{_e(v)}">{_e(v)}</option>' for v in sorted(set(values)) if v)
    return f'<select data-f="{field}"><option value="">all {field}s</option>{opts}</select>'


def render_report(results: list[dict[str, Any]], scenarios: dict[str, dict[str, Any]],
                  traces: dict[str, list[dict[str, Any]]] | None, host: str,
                  reviews: dict[str, dict[str, Any]], title: str, review_file: str,
                  facts: dict[str, str] | None = None, db_name: str = "") -> str:
    options = _options
    roles = [scenarios.get(r["scenario_id"], {}).get("tuple", {}).get("role", "") for r in results]
    status = Counter(r.get("status") for r in results)
    groups = Counter(r.get("scenario_group") for r in results)
    models = sorted({r.get("model", "") for r in results})
    summary = (f'{len(results)} scenarios · model {", ".join(models)} · '
               + " · ".join(f"{n} {k}" for k, n in sorted(groups.items()))
               + " · " + " · ".join(f"{n} {k}" for k, n in sorted(status.items())))
    if traces is None:
        summary += " · tool calls not loaded (--no-langfuse)"
    cards = "".join(render_card(r, scenarios.get(r["scenario_id"]),
                                None if traces is None else traces.get(r["scenario_id"], []), host,
                                None if facts is None else facts.get(r["scenario_id"], ""), db_name)
                    for r in results)
    controls = (f'<input id="q" type="search" placeholder="Search messages, tools, expected…">'
                + options("group", [r.get("scenario_group", "") for r in results])
                + options("role", roles)
                + options("intent", [scenarios.get(r["scenario_id"], {}).get("tuple", {}).get("intent", "")
                                     for r in results])
                + options("status", [r.get("status", "") for r in results])
                + options("decision", ["unreviewed", "pass", "failure", "invalid"])
                + '<button id="expand" type="button">expand / collapse</button>'
                + f'<button id="export" class="primary" type="button">Export {_e(review_file)}</button>'
                + '<div id="msg"></div>')
    return _page(title, summary, "confirmed failures", controls, cards,
                 {"file": review_file, "reviews": reviews}, JS_RESULT_REVIEW, len(results))


def record_facts(db: Path, scenario: dict[str, Any]) -> str:
    """One line of database facts for the scenario's order or product, read-only."""
    import sqlite3

    from scripts.check_scenarios import order_facts

    tuple_ = scenario.get("tuple", {})
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if tuple_.get("order_id"):
            o = order_facts(conn, tuple_["order_id"])
            if o is None:
                return f"order {tuple_['order_id']} not found"
            return (f"order {o['id']}: {o['title']} · {o['store']} · user {o['user_id']} · {o['status']} · "
                    f"${o['total_cents'] / 100:.2f} · ordered {o['ordered_at']} · delivered {o['delivered_at']} · "
                    f"age {o['age']} days · window {o['window']} days · eligible {o['eligible']} · "
                    f"needs approval {o['needs_approval']}")
        if tuple_.get("product_id") is not None:
            p = conn.execute("SELECT p.*, s.name AS store FROM products p JOIN stores s ON s.id = p.store_id "
                             "WHERE p.id = ?", (tuple_["product_id"],)).fetchone()
            if p is None:
                return f"product {tuple_['product_id']} not found"
            return f"product {p['id']}: '{p['title']}' · {p['store']} · ${p['price_cents'] / 100:.2f}"
    finally:
        conn.close()
    return ""


def _scenario_form(sid: str) -> str:
    radios = "".join(f'<label><input type="radio" name="decision-{_e(sid)}" data-k="decision" value="{v}"> '
                     f'{v}</label>' for v in ("accept", "revise", "reject"))
    return ('<div class="rv"><b>Your review</b>'
            f'<span>Decision</span><span>{radios}</span>'
            '<span>Reason</span><textarea data-k="reason" placeholder="Is the language realistic, do the '
            'followups make sense without the reply, does the metadata match the record?"></textarea>'
            '<span>Change</span><textarea data-k="change" placeholder="For revise: what to change. For reject: '
            'what the replacement should test. Leave empty for accept (null)."></textarea></div>')


def render_scenario_card(scenario: dict[str, Any], facts: str | None, db_name: str = "") -> str:
    sid, tuple_ = scenario["id"], scenario.get("tuple", {})
    expected = scenario.get("expected") or {}
    data = {"sid": sid, "group": scenario.get("scenario_group", ""), "role": tuple_.get("role", ""),
            "intent": tuple_.get("intent", ""), "decision": "unreviewed"}
    attrs = " ".join(f'data-{k}="{_e(v)}"' for k, v in data.items())
    chips = "".join(f'<span class="chip">{_e(k)}: {_e(tuple_[k])}</span>' for k in
                    ("role", "user_id", "intent", "access", "record_state", "policy", "tools_needed",
                     "difficulty", "user_style", "turn_count") if k in tuple_)
    messages = [scenario.get("opening_message", "")] + list(scenario.get("followups") or [])
    out = [f'<details class="card" {attrs}><summary>',
           f'<span class="sid">{_e(sid)}</span>',
           f'<span class="chip">{_e(scenario.get("scenario_group", ""))}</span>{chips}',
           '<span class="badge rvbadge" hidden></span>',
           f'<span class="first">{_e(messages[0])}</span></summary><div class="body">']
    label = expected.get("outcome") or expected.get("evaluation", "")
    detail = expected.get("reason") or expected.get("criterion") or ""
    source = expected.get("source") or {}
    out.append(f'<div class="expected"><b>Expected:</b> <code>{_e(label)}</code><br>{_e(detail)}')
    if source:
        out.append(f'<br><span class="chip">{_e(source.get("type", ""))}: {_e(source.get("reference", ""))}</span>')
    if scenario.get("data_quality_case_id"):
        out.append(f' <span class="chip">data quality: {_e(scenario["data_quality_case_id"])}</span>')
    out.append("</div>")
    out.append(_facts_block(facts, db_name))
    for i, text in enumerate(messages):
        kind = "opening" if i == 0 else f"followup {i}"
        out.append(f'<div class="turn"><div class="who">User · turn {i + 1} ({kind})</div>'
                   f'<div class="msg user">{_e(text)}</div></div>')
    out.append(_scenario_form(sid))
    out.append("</div></details>")
    return "".join(out)


def render_scenario_review(scenarios: list[dict[str, Any]], facts: dict[str, str] | None,
                           reviews: dict[str, dict[str, Any]], title: str, review_file: str,
                           db_name: str = "") -> str:
    groups = Counter(s.get("scenario_group") for s in scenarios)
    roles = Counter(s.get("tuple", {}).get("role") for s in scenarios)
    intents = {s.get("tuple", {}).get("intent") for s in scenarios}
    summary = (f"{len(scenarios)} scenarios · " + " · ".join(f"{n} {k}" for k, n in sorted(groups.items()))
               + " · " + " · ".join(f"{n} {k}" for k, n in sorted(roles.items()))
               + f" · {len(intents)} intents")
    cards = "".join(render_scenario_card(s, None if facts is None else facts.get(s["id"], ""), db_name)
                    for s in scenarios)
    controls = ('<input id="q" type="search" placeholder="Search messages, expected, records…">'
                + _options("group", [s.get("scenario_group", "") for s in scenarios])
                + _options("role", [s.get("tuple", {}).get("role", "") for s in scenarios])
                + _options("intent", [s.get("tuple", {}).get("intent", "") for s in scenarios])
                + _options("decision", ["unreviewed", "accept", "revise", "reject"])
                + '<button id="expand" type="button">expand / collapse</button>'
                + f'<button id="export" class="primary" type="button">Export {_e(review_file)}</button>'
                + '<div id="msg"></div>')
    return _page(title, summary, "revise or reject", controls, cards,
                 {"file": review_file, "reviews": reviews}, JS_SCENARIO_REVIEW, len(scenarios))


def default_review_name(results: Path) -> str:
    """pilot-results.jsonl -> pilot_review.jsonl, final-results.jsonl -> final_review.jsonl."""
    stem = results.stem
    return f"{stem[:-len('-results')] if stem.endswith('-results') else stem}_review.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render runner results as an HTML review page.")
    parser.add_argument("results", type=Path, help="runner results JSONL (scenario JSONL with --scenario-review)")
    parser.add_argument("--scenario-review", action="store_true",
                        help="review scenarios before a run: decision, reason, change")
    parser.add_argument("--ids", help="comma-separated scenario ids to include (--scenario-review)")
    parser.add_argument("--db", type=Path,
                        help="database for the record each scenario is based on; use a freshly seeded copy "
                             "after a run (default with --scenario-review: data/cartwheel.db; otherwise none)")
    parser.add_argument("--scenarios", type=Path, help="scenario JSONL, for the tuple chips and filters")
    parser.add_argument("--review", type=Path,
                        help="review JSONL to start the form from (and the name Export downloads as); "
                             "default name: <results name without -results>_review.jsonl (pilot-results.jsonl -> "
                             "pilot_review.jsonl), or support_review.jsonl with --scenario-review")
    parser.add_argument("--output", type=Path, help="default: reports/<results stem>.html")
    parser.add_argument("--no-langfuse", action="store_true", help="skip tool calls and trace links")
    parser.add_argument("--title")
    args = parser.parse_args()
    reviews = ({r["scenario_id"]: r for r in load_jsonl(args.review)}
               if args.review and args.review.exists() else {})

    if args.scenario_review:
        scenarios = load_jsonl(args.results)
        if args.ids:
            wanted = args.ids.split(",")
            by_id = {s["id"]: s for s in scenarios}
            missing = [i for i in wanted if i not in by_id]
            if missing:
                parser.error(f"unknown scenario ids: {', '.join(missing)}")
            scenarios = [by_id[i] for i in wanted]
        db = args.db or Path("data/cartwheel.db")
        facts = {s["id"]: record_facts(db, s) for s in scenarios} if db.exists() else None
        output = args.output or Path("reports") / f"{args.results.stem}-review.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_scenario_review(
            scenarios, facts, reviews, args.title or f"Cartwheel scenario review: {args.results.name}",
            args.review.name if args.review else "support_review.jsonl", str(db)))
        print(f"Wrote {output} ({len(scenarios)} scenarios{', record facts from ' + str(db) if facts is not None else ''})")
        return

    results = load_jsonl(args.results)
    scenarios = {s["id"]: s for s in load_jsonl(args.scenarios)} if args.scenarios else {}
    if args.db and not args.scenarios:
        parser.error("--db needs --scenarios (the record ids are in the scenario tuples)")
    if args.db and not args.db.exists():
        parser.error(f"database not found: {args.db}")
    facts = ({r["scenario_id"]: record_facts(args.db, scenarios.get(r["scenario_id"], {})) for r in results}
             if args.db else None)
    traces, host = (None, "") if args.no_langfuse else fetch_turn_traces(results)
    output = args.output or Path("reports") / f"{args.results.stem}.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(results, scenarios, traces, host, reviews,
                                    args.title or f"Cartwheel run: {args.results.name}",
                                    args.review.name if args.review else default_review_name(args.results),
                                    facts, str(args.db) if args.db else ""))
    found = "" if traces is None else f", traces for {sum(1 for t in traces.values() if t)} scenarios"
    found += f", record facts from {args.db}" if args.db else ""
    print(f"Wrote {output} ({len(results)} scenarios{found})")


if __name__ == "__main__":
    main()
