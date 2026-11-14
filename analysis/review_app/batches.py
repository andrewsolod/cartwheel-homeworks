"""Select HW4 review batches and record them in state/sample_manifest.json.

Batch 1 is 15 cluster representatives plus 15 uniform random conversations.
It reuses the course helper's standardization and k-means
(``analysis.helpers.selection``) with two corrections:

- Features are computed over the whole conversation. The course loader keeps
  the first turn's token count when it merges a multi-turn conversation.
- A representative is the member nearest its cluster centroid. The course
  helper's comment says so, but its loop takes members in file order.

Nothing here looks at outcomes, notes, or labels: selection uses only the
shape of each conversation and a fixed seed.

Batch 2 is 10 uniform random conversations per role (shopper, merchant,
support), excluding conversations already in a batch.

Batch 3 is a depth search: for each weak candidate mode, random picks from the
conversations whose situation lets the mode occur (DEPTH_FILTERS). The filter
is a retrieval signal, not a label; review decides positive or close negative.

Batch 4 is 15 uniform random conversations from the rest, for the saturation check.

    uv run python -m analysis.review_app.batches batch1|batch2|batch3|batch4 [--source traces/support_traces.json]
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.helpers import _state
from analysis.helpers.selection import _kmeans, _standardize
from analysis.review_app import data

REPO = Path(__file__).resolve().parent.parent.parent
FEATURES = ("turns", "tool_calls", "distinct_tools", "has_retrieval", "words")
RETRIEVAL_TOOLS = {"search_help_center", "get_policy"}


def features(conv: dict[str, Any]) -> dict[str, float]:
    """Shape of a whole conversation: turns, tool use, retrieval, length."""
    words = 0
    for turn in conv["turns"]:
        for m in turn["messages"]:
            if m["role"] == "tool_call":
                words += len(json.dumps(m.get("arguments")).split())
            elif m["role"] == "tool_result":
                words += len(json.dumps(m.get("content")).split())
            else:
                words += len(str(m.get("text", "")).split())
    tools = conv["summary"]["tools"]
    return {
        "turns": conv["summary"]["turns"],
        "tool_calls": conv["summary"]["tool_calls"],
        "distinct_tools": len(tools),
        "has_retrieval": int(bool(RETRIEVAL_TOOLS & set(tools))),
        "words": words,
    }


def cluster_representatives(convs: list[dict[str, Any]], n: int, k: int, seed: int) -> tuple[list[dict], list[dict]]:
    """Return ``n`` picks nearest their k-means centroids, and a cluster summary.

    Every cluster gives its nearest member first; the remaining slots go to the
    second-nearest members of the largest clusters.
    """
    raw = [[float(features(c)[f]) for f in FEATURES] for c in convs]
    vecs = _standardize(raw)
    assign = _kmeans(vecs, k=k, seed=seed)
    members: dict[int, list[int]] = {}
    for i, c in enumerate(assign):
        members.setdefault(c, []).append(i)
    ranked: dict[int, list[tuple[float, int]]] = {}
    summary = []
    for c, idx in sorted(members.items()):
        centroid = [sum(vecs[i][d] for i in idx) / len(idx) for d in range(len(FEATURES))]
        ranked[c] = sorted((math.dist(vecs[i], centroid), i) for i in idx)
        mean_raw = {f: round(sum(raw[i][d] for i in idx) / len(idx), 1) for d, f in enumerate(FEATURES)}
        summary.append({"cluster": c, "size": len(idx), "mean": mean_raw})

    order = [c for c, _ in sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    slots: list[tuple[int, int]] = [(c, 0) for c in order]
    slots += [(c, 1) for c in order if len(ranked[c]) > 1]
    picks = []
    for c, rank in slots[:n]:
        dist, i = ranked[c][rank]
        picks.append({
            "trace_id": convs[i]["id"],
            "scenario_id": convs[i]["scenario_id"],
            "method": "cluster",
            "cluster": c,
            "distance": round(dist, 3),
            "reason": f"cluster {c} representative #{rank + 1} (distance {dist:.2f} to centroid)",
        })
    return picks, summary


def uniform(convs: list[dict[str, Any]], n: int, seed: int, exclude: set[str]) -> list[dict]:
    pool = sorted((c for c in convs if c["id"] not in exclude), key=lambda c: c["scenario_id"])
    chosen = random.Random(seed).sample(pool, n)
    return [{"trace_id": c["id"], "scenario_id": c["scenario_id"], "method": "uniform",
             "reason": "uniform random"} for c in chosen]


def batch1(convs: list[dict[str, Any]], seed: int, k: int = 8, n_cluster: int = 15, n_uniform: int = 15) -> dict:
    reps, summary = cluster_representatives(convs, n_cluster, k, seed)
    rand = uniform(convs, n_uniform, seed, {p["trace_id"] for p in reps})
    return {
        "name": "batch1",
        "label": "Batch 1: uniform + clusters",
        "method": f"{n_uniform} uniform random + {n_cluster} k-means representatives (k={k}, nearest to centroid)",
        "target": n_cluster + n_uniform,
        "seed": seed,
        "features": list(FEATURES),
        "clusters": summary,
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "picks": reps + rand,
    }


def by_role(convs: list[dict[str, Any]], per_role: int, seed: int, exclude: set[str],
            roles: tuple[str, ...] = ("shopper", "merchant", "support")) -> dict:
    """Batch 2: ``per_role`` uniform random conversations from each role, skipping earlier batches."""
    rng = random.Random(seed)
    picks = []
    for role in roles:
        pool = sorted((c for c in convs if c["role"] == role and c["id"] not in exclude), key=lambda c: c["scenario_id"])
        picks += [{"trace_id": c["id"], "scenario_id": c["scenario_id"], "method": "by_role", "role": role,
                   "reason": f"uniform random within role {role}"} for c in rng.sample(pool, per_role)]
    return {
        "name": "batch2",
        "label": "Batch 2: by role",
        "method": f"{per_role} uniform random per role ({', '.join(roles)}), excluding earlier batches",
        "dimension": "role",
        "target": per_role * len(roles),
        "seed": seed,
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "picks": picks,
    }


def _calls(c: dict) -> list[dict]:
    return [m for t in c["turns"] for m in t["messages"] if m["role"] == "tool_call"]


def _results(c: dict) -> list[dict]:
    return [m["content"] for t in c["turns"] for m in t["messages"]
            if m["role"] == "tool_result" and isinstance(m.get("content"), dict)]


def _search_zero(c: dict) -> bool:
    return any(r.get("count") == 0 and "products" in r for r in _results(c))


# Depth-search filters describe the situation in which a mode can occur, not
# the failure itself: each pool holds candidate positives and close negatives.
DEPTH_FILTERS: dict[str, tuple[str, Any]] = {
    "refund_not_issued_after_eligible": (
        "refund intent and get_order returned refund_eligible true",
        lambda c: c["tuple"].get("intent") == "refund"
        and any((r.get("order") or {}).get("refund_eligible") is True for r in _results(c))),
    "inconsistent_record_not_flagged": (
        "scenario built on a data-quality record (record_state dq_*)",
        lambda c: str(c["tuple"].get("record_state", "")).startswith("dq_")),
    "required_escalation_missing": (
        "account_change or dispute intent (ESC-2, ESC-3)",
        lambda c: c["tuple"].get("intent") in ("account_change", "dispute")),
    "permission_denial_overexplained": (
        "a tool returned permission_denied",
        lambda c: any(r.get("error") == "permission_denied" for r in _results(c))),
    "wrong_policy_outcome": (
        "policy lookup (search_help_center/get_policy) on a policy, refund, cancellation, or dispute request",
        lambda c: bool(RETRIEVAL_TOOLS & {m["name"] for m in _calls(c)})
        and c["tuple"].get("intent") in ("policy_question", "refund", "cancellation", "dispute")),
    "search_query_misbuilt": (
        "search_products returned 0 results; then search_products with results as close negatives",
        lambda c: any(m["name"] == "search_products" for m in _calls(c))),
}
DEPTH_QUOTA = {"refund_not_issued_after_eligible": 4, "inconsistent_record_not_flagged": 4,
               "required_escalation_missing": 4, "permission_denial_overexplained": 5,
               "wrong_policy_outcome": 4, "search_query_misbuilt": 4}


def depth(convs: list[dict[str, Any]], seed: int, exclude: set[str], quota: dict[str, int] = DEPTH_QUOTA) -> dict:
    """Batch 3: for each weak mode, random picks from the conversations matching its situation filter."""
    rng = random.Random(seed)
    taken = set(exclude)
    picks, pools = [], {}
    for mode, n in quota.items():
        desc, match = DEPTH_FILTERS[mode]
        pool = sorted((c for c in convs if c["id"] not in taken and match(c)), key=lambda c: c["scenario_id"])
        pools[mode] = {"filter": desc, "size": len(pool)}
        first = [c for c in pool if _search_zero(c)] if mode == "search_query_misbuilt" else []
        chosen = first[:n] + rng.sample([c for c in pool if c not in first], n - len(first[:n]))
        for c in chosen:
            taken.add(c["id"])
            picks.append({"trace_id": c["id"], "scenario_id": c["scenario_id"], "method": "depth_search", "mode": mode,
                          "reason": f"depth search for {mode}: {desc}"})
    return {
        "name": "batch3",
        "label": "Batch 3: depth search",
        "method": "situation filters per weak mode, random within each pool; review decides positive or close negative",
        "target": sum(quota.values()),
        "seed": seed,
        "pools": pools,
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "picks": picks,
    }


def used_ids() -> set[str]:
    manifest = _state.read_json(_state.state_path("sample_manifest.json"), default={"batches": []}) or {"batches": []}
    return {p["trace_id"] for b in manifest["batches"] for p in b["picks"]}


def add_batch(batch: dict[str, Any]) -> dict[str, Any]:
    """Append a batch to the manifest; refuse duplicates and reused conversations."""
    path = _state.state_path("sample_manifest.json")
    manifest = _state.read_json(path, default={"batches": []}) or {"batches": []}
    if any(b["name"] == batch["name"] for b in manifest["batches"]):
        raise SystemExit(f"{batch['name']} already exists in {path}; not overwriting")
    used = {p["trace_id"] for b in manifest["batches"] for p in b["picks"]}
    clash = [p["scenario_id"] for p in batch["picks"] if p["trace_id"] in used]
    if clash:
        raise SystemExit(f"already in an earlier batch: {clash}")
    manifest["batches"].append(batch)
    _state.write_json(path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch", choices=["batch1", "batch2", "batch3", "batch4"])
    parser.add_argument("--source", type=Path, default=REPO / "traces/support_traces.json")
    parser.add_argument("--scenarios", type=Path, default=REPO / "scenarios/support_scenarios.jsonl")
    parser.add_argument("--seed", type=int, default=20261111)
    args = parser.parse_args()

    convs = data.build_conversations(data.traces_from_export(args.source), data.load_scenarios(args.scenarios))
    if args.batch == "batch1":
        batch = batch1(convs, args.seed)
    elif args.batch == "batch2":
        batch = by_role(convs, 10, args.seed, used_ids())
    elif args.batch == "batch3":
        batch = depth(convs, args.seed, used_ids())
    else:
        picks = uniform(convs, 15, args.seed, used_ids())
        batch = {"name": "batch4", "label": "Batch 4: final uniform (saturation)",
                 "method": "15 uniform random from conversations not in an earlier batch; checks whether new consequential modes still appear",
                 "target": 15, "seed": args.seed, "selected_at": datetime.now(timezone.utc).isoformat(), "picks": picks}
    batch["source"] = str(args.source.relative_to(REPO) if args.source.is_relative_to(REPO) else args.source)
    add_batch(batch)
    by_id = {c["id"]: c for c in convs}
    print(f"{batch['name']}: {len(batch['picks'])} picks → {_state.state_path('sample_manifest.json')}")
    for s in batch.get("clusters", []):
        print(f"  cluster {s['cluster']}: {s['size']:3d} conversations, mean {s['mean']}")
    for p in batch["picks"]:
        c = by_id[p["trace_id"]]
        print(f"  {p['scenario_id']}  {c['role']:8s} {c['tuple'].get('intent', ''):17s} {c['summary']['turns']} turns "
              f"{c['summary']['tool_calls']} calls  · {p['reason']}")


if __name__ == "__main__":
    main()
