"""Check scenario tuples against the approved dimension plan.

``scenarios.validate`` checks the scenario contract but only a few tuple
values (role and user style).  This check reads ``scenarios/dimensions.yaml``
and reports every tuple value that is not in the plan, the count of each
value per scenario group, and the planned values no scenario uses.  Unknown
values fail the check; unused values are reported and fail only with
``--strict``.

It also reports repetition: scenarios with an identical tuple and record,
records changed by more than one scenario (a refund or cancellation consumes
the record, so the second scenario's extra metadata would be wrong), and the
most similar opening messages.  A reused changed record fails the check.
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from scenarios.validate import load_jsonl

DEFAULT_PLAN = Path("scenarios/dimensions.yaml")
# Expected outcomes whose tool call changes the order record.
STATE_CHANGING_OUTCOMES = {"refund_auto_approved", "refund_queued_for_approval", "order_cancelled"}
SIMILARITY_REPORT = 0.8


def check_dimensions(
    scenarios: list[dict[str, Any]], plan: dict[str, Any]
) -> tuple[list[str], dict[str, dict[str, Counter[str]]], dict[str, list[str]]]:
    """Return unknown-value errors, counts per dimension and group, and unused values."""
    dimensions = plan["dimensions"]
    errors: list[str] = []
    counts: dict[str, dict[str, Counter[str]]] = {name: {} for name in dimensions}
    for scenario in scenarios:
        label = scenario.get("id", "<missing id>")
        group = scenario.get("scenario_group", "<no group>")
        tuple_ = scenario.get("tuple", {})
        for name, dimension in dimensions.items():
            value = tuple_.get(name)
            if value not in dimension["values"]:
                errors.append(f"{label}: tuple.{name}={value!r} is not in the dimension plan")
                continue
            counts[name].setdefault(group, Counter())[value] += 1
    unused = {
        name: [
            value
            for value in dimension["values"]
            if not any(value in by_group for by_group in counts[name].values())
        ]
        for name, dimension in dimensions.items()
    }
    return errors, counts, unused


def find_repetition(
    scenarios: list[dict[str, Any]], plan: dict[str, Any]
) -> tuple[list[str], list[list[str]], list[tuple[float, str, str]]]:
    """Return reused changed records (errors), identical tuples, and similar openings."""
    names = list(plan["dimensions"])
    same_tuple: dict[tuple[Any, ...], list[str]] = {}
    changed: dict[Any, list[str]] = {}
    for scenario in scenarios:
        tuple_ = scenario.get("tuple", {})
        key = tuple(tuple_.get(name) for name in names) + (tuple_.get("order_id"), tuple_.get("product_id"))
        same_tuple.setdefault(key, []).append(scenario["id"])
        if (scenario.get("expected") or {}).get("outcome") in STATE_CHANGING_OUTCOMES:
            changed.setdefault(tuple_.get("order_id"), []).append(scenario["id"])
    errors = [f"order {order} is changed by several scenarios: {', '.join(ids)}"
              for order, ids in changed.items() if len(ids) > 1]
    identical = [ids for ids in same_tuple.values() if len(ids) > 1]
    openings = [(s["id"], str(s["opening_message"]).lower()) for s in scenarios if s.get("opening_message")]
    similar = []
    for (a, text_a), (b, text_b) in itertools.combinations(openings, 2):
        matcher = difflib.SequenceMatcher(None, text_a, text_b)
        if matcher.real_quick_ratio() >= SIMILARITY_REPORT and matcher.quick_ratio() >= SIMILARITY_REPORT:
            ratio = matcher.ratio()
            if ratio >= SIMILARITY_REPORT:
                similar.append((ratio, a, b))
    return errors, identical, sorted(similar, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check scenario tuples against dimensions.yaml.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--strict", action="store_true", help="fail when a planned value is unused")
    args = parser.parse_args()
    plan = yaml.safe_load(args.plan.read_text())
    scenarios = load_jsonl(args.path)
    errors, counts, unused = check_dimensions(scenarios, plan)
    reused, identical, similar = find_repetition(scenarios, plan)
    errors += reused

    for name, by_group in counts.items():
        print(name)
        for group in sorted(by_group):
            values = ", ".join(f"{value} {n}" for value, n in by_group[group].most_common())
            print(f"  {group}: {values}")
        if unused[name]:
            print(f"  unused: {', '.join(unused[name])}")
    print(f"\nrepetition: {len(identical)} groups with an identical tuple and record, "
          f"{len(similar)} opening pairs at or above {SIMILARITY_REPORT:.0%} similar")
    for ids in identical:
        print(f"  same tuple: {', '.join(ids)}")
    for ratio, a, b in similar[:10]:
        print(f"  {ratio:.0%} similar openings: {a} / {b}")
    if errors:
        print("\nerrors:\n- " + "\n- ".join(errors))
    if errors or (args.strict and any(unused.values())):
        sys.exit(1)


if __name__ == "__main__":
    main()
