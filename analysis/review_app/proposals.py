"""Label proposals for HW4 Part E: one per reviewed conversation and confirmed mode.

A proposal is not a label. It records what evidence already exists for a cell,
so the reviewer can confirm the obvious cells in bulk and judge the rest:

- ``coded_positive``: the conversation is a positive of the mode in patterns.json.
- ``close_negative``: recorded close negative of the mode.
- ``situation_absent``: the mode cannot occur here (e.g. no permission_denied
  result for permission_denial_mishandled), so the answer is Pass.
- ``check_flagged``: the situation is present and a code check fires; proposed
  Fail, reviewer must judge.
- ``check``: the situation is present and no check fires; proposed Pass,
  reviewer must judge.

    uv run python -m analysis.review_app.proposals
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from analysis.helpers import _state
from analysis.review_app import data, search
from analysis.review_app.batches import RETRIEVAL_TOOLS, _calls, _results

REPO = Path(__file__).resolve().parent.parent.parent


def _names(c: dict) -> list[str]:
    return [m["name"] for m in _calls(c)]


def _replies(c: dict) -> str:
    return " ".join(m.get("text", "") for t in c["turns"] for m in t["messages"] if m["role"] == "assistant").lower()


def _intent(c: dict) -> str:
    return str(c["tuple"].get("intent", ""))


def _eligible(c: dict) -> bool:
    orders = [r["order"] for r in _results(c) if isinstance(r.get("order"), dict)]
    if _intent(c) == "refund":
        return any(o.get("refund_eligible") is True for o in orders)
    if _intent(c) == "cancellation":
        return any(o.get("status") == "placed" for o in orders)
    return False


# mode -> (situation present?, code check fires?, what the check looks for)
Rule = tuple[Callable[[dict], bool], Callable[[dict], bool], str]
RULES: dict[str, Rule] = {
    "guesses_order_without_lookup": (
        lambda c: "list_my_orders" in _names(c) or bool(c["tuple"].get("order_id")),
        lambda c: "list_my_orders" in _names(c) and not {"find_order", "get_order"} & set(_names(c)),
        "list_my_orders without find_order/get_order"),
    "policy_stated_without_lookup": (
        lambda c: _intent(c) in ("policy_question", "refund", "cancellation", "dispute", "account_change")
        or (c.get("expected") or {}).get("source", {}).get("type") == "policy_document",
        lambda c: not RETRIEVAL_TOOLS & set(_names(c))
        and any(w in _replies(c) for w in ("policy", "window", "business days", " days")),
        "policy wording in a reply with no search_help_center/get_policy call"),
    "wrong_policy_applied": (
        lambda c: bool(search.policies_required(c)) or bool(RETRIEVAL_TOOLS & set(_names(c))),
        lambda c: search.wrong_policy(c) is not None,
        "cited/fetched policy differs from the applicable one (search.wrong_policy)"),
    "search_query_misbuilt": (
        lambda c: "search_products" in _names(c),
        lambda c: any(r.get("count") == 0 and "products" in r for r in _results(c)),
        "search_products returned 0 results"),
    "permission_denial_mishandled": (
        lambda c: any(r.get("error") == "permission_denied" for r in _results(c)),
        lambda c: False,
        "LLM-judge mode: read the reply after permission_denied"),
    "eligible_action_not_taken": (
        lambda c: _eligible(c),
        lambda c: not {"issue_refund", "cancel_order"} & set(_names(c)),
        "eligible order and no issue_refund/cancel_order call"),
    "inconsistent_record_not_flagged": (
        lambda c: str(c["tuple"].get("record_state", "")).startswith("dq_"),
        lambda c: False,
        "LLM-judge mode: read whether the reply flags the bad record"),
    "required_escalation_missing": (
        lambda c: _intent(c) in ("account_change", "dispute"),
        lambda c: "escalate_to_human" not in _names(c),
        "account change/dispute and no escalate_to_human call"),
}


def propose(convs: list[dict[str, Any]], patterns: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in sorted(search.reviewed(convs), key=lambda c: c["scenario_id"]):
        for m in patterns["modes"]:
            situation, fires, what = RULES[m["name"]]
            if c["id"] in m.get("positives", []):
                basis, label, why = "coded_positive", 1, "positive in patterns.json (your coded evidence)"
            elif c["id"] in m.get("close_negatives", []):
                basis, label, why = "close_negative", 0, "recorded close negative"
            elif not situation(c):
                basis, label, why = "situation_absent", 0, "the situation for this mode does not occur in the conversation"
            elif fires(c):
                basis, label, why = "check_flagged", 1, f"code check fires: {what}"
            else:
                basis, label, why = "check", 0, f"situation present, check silent ({what})"
            out.append({"trace_id": c["id"], "scenario_id": c["scenario_id"], "mode": m["name"],
                        "proposed": label, "basis": basis, "reason": why})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=REPO / "traces/support_traces.json")
    parser.add_argument("--scenarios", type=Path, default=REPO / "scenarios/support_scenarios.jsonl")
    args = parser.parse_args()
    convs = data.build_conversations(data.traces_from_export(args.source), data.load_scenarios(args.scenarios))
    props = propose(convs, _state.read_json(_state.state_path("patterns.json")))
    _state.write_json(_state.state_path("label_proposals.json"), props)
    from collections import Counter
    print(f"{len(props)} proposals → {_state.state_path('label_proposals.json')}")
    for (basis, label), n in sorted(Counter((p["basis"], p["proposed"]) for p in props).items()):
        print(f"  {basis:17s} proposed {'Fail' if label else 'Pass'}: {n}")


if __name__ == "__main__":
    main()
