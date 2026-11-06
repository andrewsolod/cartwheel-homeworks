"""Check scenario plans against the seeded database before any messages are written.

Usage:

    uv run python -m scripts.check_scenarios PLAN.jsonl [--db data/cartwheel.db] [--rows]

A plan is a scenario record that still carries the author-only fields
``writer_brief`` and ``user_mention`` (see ``scripts.generate_scenarios``).
The check opens the database read-only and reports:

- users that do not exist or have another role;
- access values that disagree with the access matrix (AUTH-1);
- record states that the record does not have (window, threshold, status);
- refund and cancellation outcomes that disagree with the eligibility rules;
- data-quality scenarios that point at the wrong record or source;
- policy documents cited in ``expected.source`` that do not exist;
- user mentions that match no record, or more than one record when the
  scenario is not ambiguous (ambiguous scenarios need two or more);
- numbers in a shopper's brief other than the order id or a stated budget.

Repetition and dimension values are checked by ``scripts.check_dimensions``.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from scenarios.validate import load_jsonl
from seed.eligibility import is_refund_eligible, refund_needs_approval
from seed.generate import WORLD_ASOF

DEFAULT_DB = Path("data/cartwheel.db")
PLATFORM_WINDOW_DAYS = 30
THRESHOLD_USD = 100
DQ_RECORD_STATES = {
    "dq-order-reversed-dates": "dq_order_reversed_dates",
    "dq-order-missing-delivery-date": "dq_order_missing_delivery_date",
    "dq-order-store-mismatch": "dq_order_store_mismatch",
    "dq-product-duplicate-title": "dq_product_duplicate_title",
    "dq-product-missing-title": "dq_product_missing_title",
    "dq-product-invalid-price": "dq_product_invalid_price",
}
REFUND_OUTCOMES = {
    "refund_auto_approved": "auto",
    "refund_queued_for_approval": "queued",
}


def order_facts(conn: sqlite3.Connection, order_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT o.*, s.name AS store, s.return_window_days_override AS override, p.title "
        "FROM orders o JOIN stores s ON s.id = o.store_id JOIN products p ON p.id = o.product_id "
        "WHERE o.id = ?", (order_id,)).fetchone()
    if row is None:
        return None
    facts = dict(row)
    delivered = date.fromisoformat(facts["delivered_at"]) if facts["delivered_at"] else None
    facts["window"] = facts["override"] or PLATFORM_WINDOW_DAYS
    facts["age"] = (WORLD_ASOF - delivered).days if delivered else None
    facts["eligible"] = is_refund_eligible(status=facts["status"], delivered_at=delivered,
                                           as_of=WORLD_ASOF, return_window_days=facts["window"])
    facts["needs_approval"] = refund_needs_approval(facts["total_cents"] / 100, THRESHOLD_USD)
    return facts


def mention_matches(conn: sqlite3.Connection, user: sqlite3.Row, mention: dict[str, Any]) -> list[int]:
    """Orders in the actor's scope whose title holds every mention word (and the store, if named)."""
    if user["role"] == "shopper":
        where, arg = "o.user_id = ?", user["id"]
    elif user["role"] == "merchant":
        where, arg = "o.store_id = ?", user["store_id"]
    else:
        where, arg = "1 = ?", 1
    rows = conn.execute(
        "SELECT o.id, s.name AS store, p.title FROM orders o JOIN stores s ON s.id = o.store_id "
        f"JOIN products p ON p.id = o.product_id WHERE {where} ORDER BY o.id", (arg,)).fetchall()
    words = mention["product"].lower().split()
    return [r["id"] for r in rows
            if all(w in r["title"].lower() for w in words)
            and (not mention.get("store") or r["store"] == mention["store"])]


def check_plan(conn: sqlite3.Connection, plan: dict[str, Any], docs: set[str],
               dq_cases: dict[str, sqlite3.Row]) -> tuple[list[str], str]:
    """Return the problems with one plan and a one-line description of its record."""
    pid, t, expected = plan["id"], plan["tuple"], plan["expected"]
    problems: list[str] = []
    user = conn.execute("SELECT * FROM users WHERE id = ?", (t.get("user_id"),)).fetchone()
    if user is None or user["role"] != t["role"]:
        return [f"{pid}: user {t.get('user_id')} is not a {t['role']}"], ""
    info = ""
    outcome = expected.get("outcome", "")

    mention = plan.get("user_mention")
    if mention:
        found = mention_matches(conn, user, mention)
        if t["difficulty"] == "ambiguous":
            if len(found) < 2:
                problems.append(f"{pid}: ambiguous, but the mention matches {found}")
        elif found != [t.get("order_id")]:
            problems.append(f"{pid}: the mention matches {found}, expected only [{t.get('order_id')}]")

    if t.get("order_id"):
        o = order_facts(conn, t["order_id"])
        if o is None:
            return problems + [f"{pid}: order {t['order_id']} does not exist"], ""
        allowed = (t["role"] == "support"
                   or (t["role"] == "shopper" and o["user_id"] == user["id"])
                   or (t["role"] == "merchant" and o["store_id"] == user["store_id"]))
        if allowed != (t["access"] == "own_record"):
            problems.append(f"{pid}: access={t['access']} but the access matrix gives allowed={allowed}")
        info = (f"#{o['id']} {o['title']} @ {o['store']} {o['status']} ${o['total_cents'] / 100:.2f} "
                f"age={o['age']} window={o['window']} eligible={o['eligible']}")
        state = t["record_state"]
        if state == "in_window" and not (o["eligible"] and not o["needs_approval"]):
            problems.append(f"{pid}: not in_window at or below the threshold: {info}")
        if state == "past_window" and not (o["age"] is not None and o["age"] > o["window"]):
            problems.append(f"{pid}: not past_window: {info}")
        if state == "above_threshold" and not (o["eligible"] and o["needs_approval"]):
            problems.append(f"{pid}: not above_threshold: {info}")
        if state in ("placed", "shipped", "cancelled", "refunded") and o["status"] != state:
            problems.append(f"{pid}: status {o['status']} is not {state}")
        if allowed and outcome in REFUND_OUTCOMES:
            want = "queued" if o["needs_approval"] else "auto"
            if not o["eligible"] or REFUND_OUTCOMES[outcome] != want:
                problems.append(f"{pid}: {outcome} disagrees with the eligibility rules: {info}")
        if allowed and outcome.startswith("refund_denied") and o["eligible"]:
            problems.append(f"{pid}: {outcome} but the order is eligible: {info}")
        if allowed and outcome == "order_cancelled" and o["status"] != "placed":
            problems.append(f"{pid}: order_cancelled but the status is {o['status']}")
        if outcome == "permission_denied_without_details" and allowed:
            problems.append(f"{pid}: permission_denied expected, but the caller may view the order")

    if t.get("product_id") is not None:
        p = conn.execute("SELECT * FROM products WHERE id = ?", (t["product_id"],)).fetchone()
        if p is None:
            problems.append(f"{pid}: product {t['product_id']} does not exist")
        else:
            info = f"product {p['id']} '{p['title']}' store {p['store_id']} ${p['price_cents'] / 100:.2f}"

    dq = plan.get("data_quality_case_id")
    if dq:
        case = dq_cases.get(dq)
        if case is None:
            problems.append(f"{pid}: unknown data_quality_case_id {dq}")
        else:
            key = "order_id" if case["entity_type"] == "order" else "product_id"
            if str(t.get(key)) != str(case["entity_id"]):
                problems.append(f"{pid}: {dq} is about {key} {case['entity_id']}, the tuple has {t.get(key)}")
            if t["record_state"] != DQ_RECORD_STATES[dq]:
                problems.append(f"{pid}: record_state should be {DQ_RECORD_STATES[dq]}")
            if case["entity_type"] == "order" and t["access"] != "own_record":
                problems.append(f"{pid}: the damaged order must be accessible to the caller")
            source = expected.get("source", {})
            if source.get("type") != "data_quality_table" or source.get("reference") != dq:
                problems.append(f"{pid}: expected.source must be data_quality_table / {dq}")

    source = expected.get("source", {})
    if source.get("type") == "policy_document":
        for ref in re.findall(r"\b(?:cw|store)-[a-z]+(?:-[a-z]+)*", source.get("reference", "")):
            if ref not in docs:
                problems.append(f"{pid}: unknown policy document {ref}")

    if t["role"] == "shopper" and t["access"] == "own_record":
        allowed_numbers = {str(t.get("order_id"))} | {str(n) for n in plan.get("brief_numbers", [])}
        for number in re.findall(r"\d+", plan.get("writer_brief", "")):
            if number not in allowed_numbers:
                problems.append(f"{pid}: shopper brief contains a number the shopper may not know: {number}")
    return problems, info


def main() -> None:
    parser = argparse.ArgumentParser(description="Check scenario plans against the seeded database.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--rows", action="store_true", help="print one line per plan")
    args = parser.parse_args()

    plans = load_jsonl(args.path)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    docs = {p.stem for p in (args.db.parent / "policies").glob("*.md")}
    dq_cases = {r["case_id"]: r for r in conn.execute("SELECT * FROM data_quality_cases")}
    problems: list[str] = []
    for plan in plans:
        found, info = check_plan(conn, plan, docs, dq_cases)
        problems += found
        if args.rows:
            t = plan["tuple"]
            print(" | ".join(str(x) for x in (plan["id"], plan["scenario_group"][:3], t["role"], t["user_id"],
                                              t["intent"], t["record_state"], t["difficulty"],
                                              t["turn_count"], plan["expected"].get("outcome", "judgment"), info)))
    print(f"{len(plans)} plans · " + " · ".join(f"{n} {g}" for g, n in sorted(Counter(p['scenario_group'] for p in plans).items())))
    for key in ("role", "intent", "difficulty"):
        print(f"  {key}: " + ", ".join(f"{v} {n}" for v, n in Counter(p["tuple"][key] for p in plans).most_common()))
    print(f"  multi-turn: {sum(p['tuple']['turn_count'] > 1 for p in plans)}")
    if problems:
        print("\nproblems:\n- " + "\n- ".join(problems))
        sys.exit(1)
    print("no problems")


if __name__ == "__main__":
    main()
