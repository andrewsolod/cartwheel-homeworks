"""Build review conversations from Cartwheel traces, scenarios, and the seed DB.

Cartwheel writes one Langfuse trace per user turn. The review unit is the
conversation (one scenario, one session), so this module groups the per-turn
traces by ``cartwheel.scenario_id`` and keeps every turn's own trace id and
Langfuse link. The conversation id is the earliest turn's trace id, which is
the id ``analysis.helpers.selection.load_traces`` gives the merged record, so
labels written here join with the Module 2 helpers used in Homework 5.

Each conversation also carries its scenario (tuple, expected outcome, group)
and the database facts of the record the scenario is about, read from a fresh
seed copy rather than the database the run changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analysis.helpers.normalization import normalize_trace

DEFAULT_PROJECT = "cartwheel-dev"


def load_scenarios(path: Path) -> dict[str, dict[str, Any]]:
    """Return scenarios by id from a JSONL scenario file."""
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["id"]] = row
    return out


def traces_from_export(path: Path) -> list[dict[str, Any]]:
    """Normalize every trace in a Module 1 export, keeping its Langfuse path."""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data["traces"] if isinstance(data, dict) else data
    out = []
    for record in raw:
        trace = normalize_trace(record)
        trace["html_path"] = record.get("htmlPath")
        out.append(trace)
    return out


def traces_from_langfuse(limit: int = 2000) -> list[dict[str, Any]]:
    """Pull and normalize the traces that carry a scenario id from Langfuse."""
    from analysis.helpers import langfuse_io

    return langfuse_io.fetch_traces(limit=limit)


def _trace_url(trace: dict[str, Any], host: str, project: str) -> str:
    if trace.get("html_path"):
        return host.rstrip("/") + trace["html_path"]
    return f"{host.rstrip('/')}/project/{project}/traces/{trace['trace_id']}"


def _status(content: Any) -> dict[str, Any]:
    """Summarize a tool result: ok flag, error code, and row count if present."""
    if not isinstance(content, dict):
        return {"ok": None}
    status: dict[str, Any] = {"ok": content.get("ok")}
    if content.get("error"):
        status["error"] = content["error"]
    for key in ("count", "status"):
        if key in content and not isinstance(content[key], (dict, list)):
            status[key] = content[key]
    return status


def _parts_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(p.get("content", "") if isinstance(p, dict) else str(p) for p in content)
    return "" if content is None else str(content)


def system_prompt(trace: dict[str, Any]) -> str | None:
    """Return the system message the agent model received in this trace."""
    gens = sorted((o for o in trace.get("observations", []) if o.get("type") == "GENERATION"),
                  key=lambda o: o.get("start_time") or "")
    for gen in gens:
        inp = gen.get("input")
        msgs = inp if isinstance(inp, list) else (inp or {}).get("messages", []) if isinstance(inp, dict) else []
        for m in msgs or []:
            if isinstance(m, dict) and m.get("role") == "system":
                return _parts_text(m.get("content") if m.get("content") is not None else m.get("parts"))
    return None


# The server injects these per-user lines into an otherwise fixed template.
_SESSION_LINE = re.compile(r"^- (User role|User id|Store id):.*$", re.MULTILINE)


def prompt_template_id(prompt: str | None) -> str | None:
    """Hash of the system prompt with the per-user session lines blanked."""
    if prompt is None:
        return None
    return hashlib.sha1(_SESSION_LINE.sub("", prompt).encode()).hexdigest()[:10]


def _messages(trace: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for m in trace.get("trace", []):
        role = m.get("role")
        if role == "tool_call":
            out.append({"role": role, "name": m.get("name"), "arguments": m.get("arguments")})
        elif role == "tool_result":
            out.append({"role": role, "name": m.get("name"), "content": m.get("content"),
                        "status": _status(m.get("content"))})
        else:
            out.append({"role": role, "text": m.get("text", ""), "label": m.get("label")})
    return out


def build_conversations(
    traces: list[dict[str, Any]],
    scenarios: dict[str, dict[str, Any]],
    facts: Any = None,
    host: str | None = None,
    project: str = DEFAULT_PROJECT,
) -> list[dict[str, Any]]:
    """Group per-turn traces into conversations for the scenarios given.

    Traces whose scenario id is not in ``scenarios`` (pilot, smoke) are left
    out. ``facts`` is a callable ``scenario -> str`` for the record panel.
    """
    host = host or os.environ.get("LANGFUSE_HOST") or "http://localhost:3000"
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        sid = trace.get("meta", {}).get("scenario_id")
        if sid in scenarios:
            by_scenario[sid].append(trace)

    conversations = []
    for sid, group in by_scenario.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        scenario = scenarios[sid]
        tuple_ = scenario.get("tuple", {})
        turns = [
            {
                "n": i + 1,
                "trace_id": t["trace_id"],
                "url": _trace_url(t, host, project),
                "timestamp": t.get("timestamp"),
                "messages": _messages(t),
            }
            for i, t in enumerate(group)
        ]
        tool_names = [m["name"] for t in turns for m in t["messages"] if m["role"] == "tool_call"]
        errors = [m["status"].get("error") for t in turns for m in t["messages"]
                  if m["role"] == "tool_result" and m["status"].get("error")]
        prompt = system_prompt(group[0])
        first_user = next((m["text"] for m in turns[0]["messages"] if m["role"] == "user"), "")
        conversations.append({
            "id": group[0]["trace_id"],
            "scenario_id": sid,
            "session_id": group[0].get("meta", {}).get("session_id"),
            "role": tuple_.get("role") or group[0].get("meta", {}).get("role"),
            "user_id": tuple_.get("user_id"),
            "group": scenario.get("scenario_group"),
            "data_quality_case_id": scenario.get("data_quality_case_id"),
            "tuple": tuple_,
            "writer_brief": scenario.get("writer_brief"),
            "expected": scenario.get("expected"),
            "system_prompt": prompt,
            "prompt_template_id": prompt_template_id(prompt),
            "first_message": first_user,
            "record_facts": facts(scenario) if facts else "",
            "models": sorted({m for t in group for m in t.get("models", [])}),
            "prompt_version": group[0].get("meta", {}).get("prompt_version"),
            "turns": turns,
            "summary": {"turns": len(turns), "tool_calls": len(tool_names),
                        "tools": sorted(set(tool_names)), "errors": errors},
        })
    shared = Counter(c["prompt_template_id"] for c in conversations)
    for c in conversations:
        c["prompt_template_shared"] = shared[c["prompt_template_id"]]
    conversations.sort(key=lambda c: c["scenario_id"])
    return conversations


def facts_reader(db: Path | None):
    """Return a ``scenario -> str`` reader over a seed DB, or None without one."""
    if db is None:
        return None
    from scripts.scenarios_renderer import record_facts

    def read(scenario: dict[str, Any]) -> str:
        return record_facts(db, scenario)

    return read
