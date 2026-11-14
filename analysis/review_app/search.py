"""Depth searches over reviewed conversations, written as pending suggestions.

A search is a retrieval signal, not a label: every hit goes to
state/suggestions.json for the reviewer to accept or reject with a reason.

    uv run python -m analysis.review_app.search wrong_policy [--dry-run]
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.helpers import _state
from analysis.review_app import data

REPO = Path(__file__).resolve().parent.parent.parent
POLICY_ID = re.compile(r"\b(?:cw|store)-[a-z0-9-]*[a-z0-9]\b")


def policies_used(conv: dict[str, Any]) -> set[str]:
    """Policy ids the agent fetched with get_policy or named in a reply."""
    used: set[str] = set()
    for turn in conv["turns"]:
        for m in turn["messages"]:
            if m["role"] == "tool_call" and m["name"] == "get_policy":
                used.add(str((m.get("arguments") or {}).get("policy_id")))
            elif m["role"] == "assistant":
                used |= set(POLICY_ID.findall(m.get("text", "")))
    return used


def policies_required(conv: dict[str, Any]) -> set[str]:
    source = (conv.get("expected") or {}).get("source") or {}
    return set(POLICY_ID.findall(str(source.get("reference", "")))) if source.get("type") == "policy_document" else set()


def wrong_policy(conv: dict[str, Any]) -> str | None:
    """Revised wrong_policy_outcome: the answer rests on a policy other than the applicable one."""
    need, used = policies_required(conv), policies_used(conv)
    if need and used and not need & used:
        return f"needs {', '.join(sorted(need))}; agent used {', '.join(sorted(used))}"
    return None


SEARCHES = {"wrong_policy": ("wrong_policy_outcome", wrong_policy)}


def reviewed(convs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifest = _state.read_json(_state.state_path("sample_manifest.json"), default={"batches": []}) or {"batches": []}
    ids = {p["trace_id"] for b in manifest["batches"] for p in b["picks"]}
    return [c for c in convs if c["id"] in ids]


def run(name: str, convs: list[dict[str, Any]], dry_run: bool = False) -> list[dict[str, Any]]:
    mode, match = SEARCHES[name]
    path = _state.state_path("suggestions.json")
    existing = _state.read_json(path, default=[]) or []
    taken = {s["id"] for s in existing}
    now = datetime.now(timezone.utc).isoformat()
    hits = []
    for c in sorted(reviewed(convs), key=lambda c: c["scenario_id"]):
        why = match(c)
        sid = f"search-{name}-{c['scenario_id']}"
        if not why or sid in taken:
            continue
        hits.append({"id": sid, "trace_id": c["id"], "scenario_id": c["scenario_id"], "mode": mode, "kind": "mode_hit",
                     "text": f"Search '{name}' (revised {mode}): {why}. Accept if the answer rests on that policy; "
                             "reject with the boundary if not.",
                     "block": None, "quote": "", "retrieved_for": mode, "source": f"search:{name}",
                     "status": "pending", "created_at": now})
    if not dry_run and hits:
        _state.write_json(path, existing + hits)
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("search", choices=sorted(SEARCHES))
    parser.add_argument("--source", type=Path, default=REPO / "traces/support_traces.json")
    parser.add_argument("--scenarios", type=Path, default=REPO / "scenarios/support_scenarios.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    convs = data.build_conversations(data.traces_from_export(args.source), data.load_scenarios(args.scenarios))
    hits = run(args.search, convs, args.dry_run)
    print(f"{args.search}: {len(hits)} hit(s) among {len(reviewed(convs))} reviewed conversations")
    for h in hits:
        print(f"  {h['scenario_id']}  {h['text'].split(': ', 1)[1].split('. Accept')[0]}")


if __name__ == "__main__":
    main()
