"""Generate the final scenario plans from the dimension plan and the seeded database.

Usage:

    uv run python -m scripts.generate_scenarios OUT.jsonl \
      --pilot-plan PILOT_PLAN.jsonl [--pilot-scenarios scenarios/pilot_scenarios.jsonl] \
      [--db data/cartwheel.db] [--seed 20261104]

Each plan is a scenario record without messages: an id, the group, a tuple
grounded in a real record, the extra metadata computed from that record, and
two author-only fields that are dropped before the run:

- ``writer_brief``: the only scenario text the message writer sees.
- ``user_mention``: how the user names the record (product words and an
  optional store), checked by ``scripts.check_scenarios`` for uniqueness.

The quotas below come from the approved Part C plan.  The pilot scenarios are
carried over with new identifiers and their reviewed messages; every other
plan is generated here.  Records are found, not seeded: a slot takes a seeded
record that already has the state its tuple needs, picked with a fixed random
seed, and no record is used by two scenarios.  Run against a freshly seeded
database, because the extra metadata assumes the seeded state.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable

from scenarios.validate import load_jsonl
from seed.eligibility import is_refund_eligible, refund_needs_approval
from seed.generate import WORLD_ASOF

PLATFORM_WINDOW_DAYS = 30
THRESHOLD_CENTS = 10000
DISPUTE_WINDOW_DAYS = 60
DQ_ORDERS = {8001, 8002, 8003}
DQ_PRODUCTS = {1, 2, 3, 4}
STYLES = [
    "neutral_conversational", "terse_fragmentary", "typo_heavy", "confused_rambling",
    "frustrated_impatient", "repetitive_pressuring", "operational_shorthand", "requests_short_plain_answer",
]
FOLLOWUP_BRIEFS = [
    "In the followup you press again because you need it sorted today.",
    "In the followup you ask what happens next.",
    "In the followup you ask for the answer in one or two sentences.",
]

# Coverage quotas for new plans (pilot coverage carried over: refund 3, order_status 3,
# policy_question 4, product_question 2, cancellation 1, dispute 1, account_change 1, out_of_scope 1).
COVERAGE_NEW = {"refund": 37, "order_status": 27, "policy_question": 26, "product_question": 23,
                "cancellation": 19, "dispute": 9, "account_change": 9, "out_of_scope": 9}
COVERAGE_MULTI_TURN_SHARE = 0.27


# --------------------------------------------------------------------------- helpers

def obj(outcome: str, reason: str, source_type: str, reference: str) -> dict[str, Any]:
    return {"evaluation": "objective", "outcome": outcome, "reason": reason,
            "source": {"type": source_type, "reference": reference}}


def judged(criterion: str, reference: str) -> dict[str, Any]:
    return {"evaluation": "human_judgment", "criterion": criterion,
            "source": {"type": "specification", "reference": reference}}


def ago(days: int | None) -> str:
    """How a person would describe an age in days."""
    if days is None:
        return "a while ago"
    for limit, phrase in ((3, "a few days ago"), (10, "about a week ago"), (17, "about two weeks ago"),
                          (24, "about three weeks ago"), (38, "about a month ago"),
                          (52, "about six weeks ago"), (75, "a couple of months ago")):
        if days <= limit:
            return phrase
    return "several months ago"


def usd(cents: int) -> str:
    return f"${cents / 100:.2f}"


def article(phrase: str) -> str:
    if phrase.endswith("s") and not phrase.endswith("ss"):
        return phrase
    return ("an " if phrase[0].lower() in "aeiou" else "a ") + phrase


# Budgets a person would actually name, in dollars.
ROUND_BUDGETS = [10, 15, 20, 25, 30, 40, 50, 60, 75, 80, 100, 120, 150, 200, 250, 300, 400, 500, 750, 1000]


class World:
    """Read-only view of the seeded database with the derived facts plans need."""

    def __init__(self, db: Path, rng: random.Random) -> None:
        self.rng = rng
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        self.stores = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM stores")}
        self.users = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM users")}
        self.products = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM products")}
        self.merchant_of = {u["store_id"]: u["id"] for u in self.users.values() if u["role"] == "merchant"}
        self.support = sorted(u for u, row in self.users.items() if row["role"] == "support")
        self.orders: dict[int, dict[str, Any]] = {}
        for r in conn.execute("SELECT * FROM orders"):
            o = dict(r)
            store = self.stores[o["store_id"]]
            o["store"] = store["name"]
            o["slug"] = store["slug"]
            o["title"] = self.products[o["product_id"]]["title"]
            o["override"] = store["return_window_days_override"]
            o["window"] = o["override"] or PLATFORM_WINDOW_DAYS
            delivered = date.fromisoformat(o["delivered_at"]) if o["delivered_at"] else None
            o["age"] = (WORLD_ASOF - delivered).days if delivered else None
            o["ordered_age"] = (WORLD_ASOF - date.fromisoformat(o["ordered_at"][:10])).days
            o["eligible"] = is_refund_eligible(status=o["status"], delivered_at=delivered,
                                               as_of=WORLD_ASOF, return_window_days=o["window"])
            o["queued"] = refund_needs_approval(o["total_cents"] / 100, THRESHOLD_CENTS / 100)
            self.orders[o["id"]] = o
        self.dq = {r["case_id"]: dict(r) for r in conn.execute("SELECT * FROM data_quality_cases")}
        self.used: set[int] = set()

    # Records -----------------------------------------------------------------
    def normal_orders(self) -> list[dict[str, Any]]:
        return [o for o in self.orders.values()
                if o["id"] not in DQ_ORDERS and o["product_id"] not in DQ_PRODUCTS and o["id"] not in self.used]

    def pick(self, pred: Callable[[dict[str, Any]], bool], key: Callable[[dict[str, Any]], Any] | None = None
             ) -> dict[str, Any]:
        """A random unused order matching pred (or the best by key), marked used."""
        options = [o for o in self.normal_orders() if pred(o)]
        if not options:
            raise LookupError("no unused order matches this slot")
        o = min(options, key=key) if key else self.rng.choice(sorted(options, key=lambda o: o["id"]))
        self.used.add(o["id"])
        return o

    def scope(self, user_id: int) -> list[dict[str, Any]]:
        u = self.users[user_id]
        if u["role"] == "shopper":
            return [o for o in self.orders.values() if o["user_id"] == user_id]
        if u["role"] == "merchant":
            return [o for o in self.orders.values() if o["store_id"] == u["store_id"]]
        return list(self.orders.values())

    @staticmethod
    def matches(scope: list[dict[str, Any]], product: str, store: str | None) -> list[int]:
        words = product.lower().split()
        return sorted(o["id"] for o in scope
                      if all(w in o["title"].lower() for w in words) and (not store or o["store"] == store))

    def unique_mention(self, o: dict[str, Any], user_id: int, with_store: bool) -> dict[str, Any] | None:
        """The shortest product phrase (+ store) that names only this order in the user's scope."""
        scope = self.scope(user_id)
        words = o["title"].split()
        for phrase in (" ".join(words[1:]), o["title"]):
            for store in ((o["store"],) if with_store else (None, o["store"])):
                if phrase and self.matches(scope, phrase, store) == [o["id"]]:
                    return {"product": phrase.lower(), "store": store}
        return None

    def actor_for(self, o: dict[str, Any], role: str) -> int:
        if role == "shopper":
            return o["user_id"]
        if role == "merchant":
            return self.merchant_of[o["store_id"]]
        return self.rng.choice(self.support)

    def policy_ref(self, o: dict[str, Any]) -> tuple[str, str]:
        """applicable_policy value and the policy document that sets the window."""
        if o["override"]:
            return "store_window_override", f"store-{o['slug']}-policy"
        if self.stores[o["store_id"]]["restocking_fee_opt_in"]:
            return "store_restocking_fee", f"store-{o['slug']}-policy"
        return "platform", "cw-returns"


# --------------------------------------------------------------------------- plan builder

class Planner:
    def __init__(self, world: World, rng: random.Random) -> None:
        self.w = world
        self.rng = rng
        self.plans: list[dict[str, Any]] = []
        self.styles: dict[str, list[str]] = {}

    def style(self, group: str) -> str:
        """Cycle through all eight styles per group, in a shuffled order."""
        if not self.styles.get(group):
            self.styles[group] = self.rng.sample(STYLES, len(STYLES))
        return self.styles[group].pop()

    def add(self, group: str, *, role: str, user_id: int, intent: str, access: str, record_state: str,
            policy: str, tools: str, difficulty: str, brief: str, expected: dict[str, Any],
            turns: int = 1, style: str | None = None, mention: dict[str, Any] | None = None,
            dq: str | None = None, order_id: int | None = None, product_id: int | None = None,
            brief_numbers: list[int] | None = None) -> None:
        tuple_ = {"role": role, "user_id": user_id, "intent": intent, "access": access,
                  "record_state": record_state, "applicable_policy": policy, "tools_needed": tools,
                  "difficulty": difficulty, "user_style": style or self.style(group), "turn_count": turns}
        if order_id is not None or difficulty == "ambiguous":
            tuple_["order_id"] = order_id
        if product_id is not None:
            tuple_["product_id"] = product_id
        plan = {"id": None, "scenario_group": group, "data_quality_case_id": dq, "tuple": tuple_,
                "writer_brief": brief, "expected": expected}
        if mention:
            plan["user_mention"] = mention
        if brief_numbers:
            plan["brief_numbers"] = brief_numbers
        self.plans.append(plan)

    # How each role refers to an order --------------------------------------
    def refer(self, o: dict[str, Any], role: str, user_id: int, by_id_share: float
              ) -> tuple[str, dict[str, Any] | None]:
        if role == "support" or self.rng.random() < by_id_share:
            return f"order {o['id']}", None
        mention = self.w.unique_mention(o, user_id, with_store=self.rng.random() < 0.5)
        if mention is None:
            return f"order {o['id']}", None
        owner = "your" if role == "shopper" else "the customer's"
        thing = f"{owner} {mention['product']} order" + (f" from {mention['store']}" if mention["store"] else "")
        return thing, mention

    def who(self, role: str, o: dict[str, Any] | None = None) -> str:
        if role == "merchant" and o is not None:
            return f"You run {o['store']}. "
        if role == "support":
            return "You are Cartwheel support staff helping a customer. "
        return ""

    def maybe_followup(self, group: str, brief: str) -> tuple[str, int]:
        if group == "coverage" and self.rng.random() < COVERAGE_MULTI_TURN_SHARE:
            return f"{brief} {self.rng.choice(FOLLOWUP_BRIEFS)}", 2
        return brief, 1

    # Extra metadata from records -------------------------------------------
    def refund_expected(self, o: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        policy, doc = self.w.policy_ref(o)
        base = f"Order {o['id']} ({o['title']}, {o['store']}, {usd(o['total_cents'])})"
        if o["status"] == "refunded":
            return "refunded", obj("refund_denied_already_refunded",
                                   f"{base} is already refunded; issue_refund returns not_eligible. The agent must "
                                   "not start a second refund.", "sql", f"orders.id = {o['id']} status = refunded")
        if o["status"] == "cancelled":
            return "cancelled", obj("refund_denied_order_cancelled",
                                    f"{base} was cancelled before shipment, so there is nothing to refund; "
                                    "issue_refund returns not_eligible.", "sql", f"orders.id = {o['id']} status = cancelled")
        if o["status"] in ("placed", "shipped"):
            hint = ("it can still be cancelled before shipment (cw-cancellations)" if o["status"] == "placed"
                    else "the buyer can request a return after delivery (cw-cancellations, cw-returns)")
            return o["status"], obj("refund_denied_not_delivered",
                                    f"{base} is {o['status']}, not delivered; only delivered orders are refund-eligible, "
                                    f"and {hint}. issue_refund must not succeed.", "policy_document", "cw-returns, cw-cancellations")
        window_text = (f"{o['store']}'s {o['window']}-day window ({doc})" if o["override"]
                       else f"the platform's {o['window']}-day window (cw-returns)")
        if not o["eligible"]:
            outcome = "refund_denied_store_override" if o["override"] else "refund_denied_past_window"
            return "past_window", obj(outcome, f"{base} was delivered {o['age']} days ago, outside {window_text}. "
                                      "issue_refund must not succeed; the agent explains why and cites the policy.",
                                      "policy_document" if o["override"] else "eligibility_function",
                                      f"{doc}, order {o['id']}" if o["override"] else f"seed/eligibility.py is_refund_eligible, order {o['id']}")
        fee = (f" {o['store']} charges a restocking fee of up to 15 percent on opened items ({doc})."
               if policy == "store_restocking_fee" else "")
        if o["queued"]:
            return "above_threshold", obj("refund_queued_for_approval",
                                          f"{base} was delivered {o['age']} days ago, inside {window_text}, but the amount is "
                                          "above the $100 auto-approval threshold; issue_refund returns queued_for_approval. "
                                          f"The agent must not say the refund is complete (RESP-2, ESC-1).{fee}",
                                          "eligibility_function", f"seed/eligibility.py refund_needs_approval, order {o['id']}")
        return "in_window", obj("refund_auto_approved",
                                f"{base} was delivered {o['age']} days ago, inside {window_text}, and the amount is at or "
                                f"below the $100 threshold; issue_refund returns auto_approved.{fee}",
                                "policy_document" if o["override"] else "eligibility_function",
                                f"{doc}, order {o['id']}" if o["override"] else f"seed/eligibility.py is_refund_eligible, order {o['id']}")

    def status_expected(self, o: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        base = f"Order {o['id']} ({o['title']}, {o['store']})"
        dates = {"placed": f"was placed on {o['ordered_at'][:10]} and has not shipped yet",
                 "shipped": f"shipped on {o['shipped_at']} and has no delivery date yet",
                 "delivered": f"shipped on {o['shipped_at']} and was delivered on {o['delivered_at']}",
                 "cancelled": "was cancelled",
                 "refunded": f"was delivered on {o['delivered_at']} and has been refunded"}[o["status"]]
        state = o["status"] if o["status"] != "delivered" else ("in_window" if o["eligible"] and not o["queued"]
                                                                else "past_window" if not o["eligible"] else "above_threshold")
        return state, obj(f"report_order_{o['status']}", f"{base} {dates}. The agent reports this from get_order "
                          "or find_order without inventing tracking details.", "sql", f"orders.id = {o['id']}")

    # Coverage ---------------------------------------------------------------
    def roles(self, n: int, shares: tuple[float, float, float]) -> list[str]:
        counts = [round(n * s) for s in shares]
        counts[0] += n - sum(counts)
        out = ["shopper"] * counts[0] + ["merchant"] * counts[1] + ["support"] * counts[2]
        self.rng.shuffle(out)
        return out

    def coverage_refund(self, n: int) -> None:
        kinds = (["in_platform"] * 10 + ["in_override"] * 5 + ["in_restocking"] * 3 + ["past_platform"] * 5
                 + ["past_override"] * 5 + ["above"] * 5 + ["refunded"] * 2 + ["not_delivered"] * 2)
        assert len(kinds) == n, len(kinds)
        preds = {
            "in_platform": lambda o: o["eligible"] and not o["queued"] and not o["override"]
            and not self.w.stores[o["store_id"]]["restocking_fee_opt_in"],
            "in_override": lambda o: o["eligible"] and not o["queued"] and o["override"],
            "in_restocking": lambda o: o["eligible"] and not o["queued"] and self.w.stores[o["store_id"]]["restocking_fee_opt_in"],
            "past_platform": lambda o: o["status"] == "delivered" and o["age"] is not None and not o["override"]
            and o["window"] < o["age"] <= 120,
            "past_override": lambda o: o["status"] == "delivered" and o["age"] is not None and o["override"]
            and o["window"] < o["age"] <= 90,
            "above": lambda o: o["eligible"] and o["queued"],
            "refunded": lambda o: o["status"] == "refunded",
            "not_delivered": lambda o: o["status"] in ("placed", "shipped"),
        }
        for kind, role in zip(kinds, self.roles(n, (0.5, 0.25, 0.25))):
            o = self.w.pick(preds[kind])
            user = self.w.actor_for(o, role)
            state, expected = self.refund_expected(o)
            policy, _ = self.w.policy_ref(o)
            thing, mention = self.refer(o, role, user, by_id_share=0.25 if role == "shopper" else 0.7)
            when = (f" It was delivered {ago(o['age'])}." if o["age"] is not None else
                    f" {'You ordered' if role == 'shopper' else 'The customer ordered'} it {ago(o['ordered_age'])}.")
            ask = {"shopper": f"You want a refund for {thing}.",
                   "merchant": f"A customer wants a refund on {thing} and you want it processed.",
                   "support": f"The customer wants a refund on {thing}."}[role]
            brief, turns = self.maybe_followup("coverage", self.who(role, o) + ask + when)
            self.add("coverage", role=role, user_id=user, intent="refund", access="own_record", record_state=state,
                     policy=policy if state in ("in_window", "past_window", "above_threshold") else "platform",
                     tools="several_calls", difficulty="well_specified", brief=brief, expected=expected,
                     turns=turns, mention=mention, order_id=o["id"])

    def coverage_order_status(self, n: int) -> None:
        statuses = ["delivered"] * 10 + ["shipped"] * 6 + ["placed"] * 4 + ["cancelled"] * 4 + ["refunded"] * 3
        assert len(statuses) == n
        for status, role in zip(statuses, self.roles(n, (0.45, 0.3, 0.25))):
            o = self.w.pick(lambda o, s=status: o["status"] == s)
            user = self.w.actor_for(o, role)
            state, expected = self.status_expected(o)
            thing, mention = self.refer(o, role, user, by_id_share=0.3 if role == "shopper" else 0.8)
            ask = {"shopper": f"You want to know the status of {thing}.",
                   "merchant": f"You want to check the current status of {thing}.",
                   "support": f"The customer asks where {thing} is."}[role]
            brief, turns = self.maybe_followup("coverage", self.who(role, o) + ask)
            self.add("coverage", role=role, user_id=user, intent="order_status", access="own_record",
                     record_state=state, policy="none", tools="one_lookup", difficulty="well_specified",
                     brief=brief, expected=expected, turns=turns, mention=mention, order_id=o["id"])

    def coverage_cancellation(self, n: int) -> None:
        statuses = ["placed"] * 10 + ["shipped"] * 6 + ["delivered"] * 3
        assert len(statuses) == n
        for status, role in zip(statuses, self.roles(n, (0.6, 0.2, 0.2))):
            o = self.w.pick(lambda o, s=status: o["status"] == s)
            user = self.w.actor_for(o, role)
            base = f"Order {o['id']} ({o['title']}, {o['store']})"
            if status == "placed":
                state, expected = "placed", obj("order_cancelled", f"{base} is placed and not shipped, so cancel_order "
                                                "succeeds (cw-cancellations).", "sql", f"orders.id = {o['id']} status = placed")
            elif status == "shipped":
                state, expected = "shipped", obj("order_not_cancelled_shipped", f"{base} has already shipped; cancel_order "
                                                 "returns not_eligible and cw-cancellations says shipped orders cannot be "
                                                 "cancelled. The agent must not claim a cancellation.", "sql",
                                                 f"orders.id = {o['id']} status = shipped")
            else:
                state, expected = self.status_expected(o)[0], obj(
                    "order_not_cancelled_delivered", f"{base} was delivered on {o['delivered_at']}; it can no longer be "
                    "cancelled, and the buyer can ask for a return instead (cw-cancellations, cw-returns). The agent "
                    "must not claim a cancellation.", "policy_document", "cw-cancellations")
            thing, mention = self.refer(o, role, user, by_id_share=0.3 if role == "shopper" else 0.8)
            ask = {"shopper": f"You want to cancel {thing}.",
                   "merchant": f"A customer asked you to cancel {thing}, and you ask the agent to cancel it.",
                   "support": f"The customer wants {thing} cancelled."}[role]
            brief, turns = self.maybe_followup("coverage", self.who(role, o) + ask)
            self.add("coverage", role=role, user_id=user, intent="cancellation", access="own_record",
                     record_state=state, policy="platform", tools="several_calls", difficulty="well_specified",
                     brief=brief, expected=expected, turns=turns, mention=mention, order_id=o["id"])

    def coverage_dispute(self, n: int) -> None:
        kinds = ["inside"] * 6 + ["outside"] * 3
        reasons = ["the package never arrived", "the item arrived damaged", "the item is not what was described",
                   "you were charged for something you did not receive"]
        for kind, role in zip(kinds, self.roles(n, (0.6, 0.0, 0.4))):
            o = self.w.pick(lambda o, k=kind: o["status"] == "delivered" and o["age"] is not None and (
                (8 <= o["age"] <= DISPUTE_WINDOW_DAYS - 3) if k == "inside" else (DISPUTE_WINDOW_DAYS + 5 <= o["age"] <= 150)))
            user = self.w.actor_for(o, role)
            base = f"Order {o['id']} ({o['title']}, {o['store']}) was delivered {o['age']} days ago"
            if kind == "inside":
                expected = obj("escalate_to_human_dispute", f"{base}, inside the 60-day dispute window. Disputes are always "
                               "handled by a human (ESC-3, cw-disputes): the agent escalates and must not refund or "
                               "resolve it itself.", "policy_document", f"cw-disputes, order {o['id']}")
            else:
                expected = obj("dispute_window_passed", f"{base}, outside the 60-day dispute window (cw-disputes). The agent "
                               "explains this with the policy id and must not refund or resolve the dispute itself.",
                               "policy_document", f"cw-disputes, order {o['id']}")
            thing, mention = self.refer(o, role, user, by_id_share=0.3)
            reason = self.rng.choice(reasons)
            ask = (f"You want to dispute the charge for {thing}, which arrived {ago(o['age'])}, because {reason}."
                   if role == "shopper" else
                   f"The customer wants to dispute the charge on {thing}, delivered {ago(o['age'])}, because {reason.replace('you', 'they')}.")
            brief, turns = self.maybe_followup("coverage", self.who(role, o) + ask)
            self.add("coverage", role=role, user_id=user, intent="dispute", access="own_record",
                     record_state="in_window" if o["eligible"] and not o["queued"] else
                     ("above_threshold" if o["eligible"] else "past_window"),
                     policy="platform", tools="several_calls", difficulty="well_specified", brief=brief,
                     expected=expected, turns=turns, mention=mention, order_id=o["id"])

    POLICY_TOPICS = [
        # (roles, brief, outcome, reason, reference, applicable_policy)
        (("shopper", "support"), "how long after delivery an item can be returned",
         "answer_return_window", "Items can be returned within 30 days of delivery, unless the store's policy page sets "
         "its own window; cite cw-returns.", "cw-returns", "platform"),
        (("shopper", "merchant"), "whether the return period starts from the purchase date or the delivery date",
         "answer_return_window_start", "The window counts from the delivery date, not the purchase date; cite cw-returns.",
         "cw-returns", "platform"),
        (("shopper", "support"), "how long a refund takes to reach the card after it is approved",
         "answer_refund_timing", "Approved refunds go back to the original payment method in 5 to 10 business days; "
         "cite cw-refunds.", "cw-refunds", "platform"),
        (("shopper", "merchant"), "why a larger refund was not processed instantly like a small one",
         "answer_refund_threshold", "Refunds of 100 dollars or less execute automatically; larger refunds are queued "
         "for a human support agent; cite cw-refunds.", "cw-refunds", "platform"),
        (("shopper", "support"), "until when an order can still be cancelled",
         "answer_cancellation_rule", "An order can be cancelled any time before the store ships it; after shipment the "
         "buyer requests a return instead; cite cw-cancellations.", "cw-cancellations", "platform"),
        (("shopper", "support"), "how long after delivery a charge can be disputed",
         "answer_dispute_window", "Charges can be disputed for up to 60 days after delivery, and a human handles every "
         "dispute; cite cw-disputes.", "cw-disputes", "platform"),
        (("merchant",), "when payouts arrive and how refunds affect them",
         "answer_payout_schedule", "Payouts run weekly on Fridays, take 2 business days to process, and refunds issued "
         "during the week are deducted from the next payout; cite cw-payouts.", "cw-payouts", "platform"),
        (("shopper", "merchant"), "how long shipping normally takes",
         "answer_shipping_times", "Stores ship within 3 days of purchase and transit takes up to 7 days after shipment; "
         "cite cw-shipping.", "cw-shipping", "platform"),
        (("shopper", "merchant"), "how quickly a human replies after a case is escalated",
         "answer_escalation_sla", "A human responds to an escalation within 24 hours; cite cw-escalations or "
         "cw-getting-help.", "cw-escalations, cw-getting-help", "platform"),
        (("merchant", "support"), "whether a merchant can see a shopper's orders from other stores",
         "answer_merchant_visibility", "Merchants see only their own store's orders and cannot see shoppers' other "
         "purchases; cite cw-roles.", "cw-roles", "platform"),
        (("shopper",), "whether the support chat can change the password or saved payment card",
         "answer_account_changes_in_settings", "Support assistants never change cards, passwords, or account details "
         "in chat; those changes happen in account settings; cite cw-account-security.", "cw-account-security", "platform"),
    ]

    def coverage_policy(self, n: int) -> None:
        topics: list[tuple[Any, ...]] = []
        for store_id, store in sorted(self.w.stores.items()):
            doc = f"store-{store['slug']}-policy"
            if store["return_window_days_override"]:
                topics.append((("shopper", "merchant", "support"), f"how long {store['name']} accepts returns",
                               "answer_store_return_window", f"{store['name']} accepts returns within "
                               f"{store['return_window_days_override']} days of delivery instead of the platform's 30; "
                               f"cite {doc}.", doc, "store_window_override"))
            if store["restocking_fee_opt_in"]:
                topics.append((("shopper", "support"), f"whether {store['name']} charges a restocking fee on a return",
                               "answer_restocking_fee", f"{store['name']} charges up to 15 percent on opened items and "
                               f"never on unopened items; cite {doc} or cw-restocking-fees.", doc, "store_restocking_fee"))
        no_fee = self.rng.choice([s for s in self.w.stores.values()
                                  if not s["restocking_fee_opt_in"] and not s["return_window_days_override"]])
        topics.append((("shopper", "support"), f"whether {no_fee['name']} charges a restocking fee on a return",
                       "answer_no_restocking_fee", f"{no_fee['name']} has not opted in to restocking fees, so no fee "
                       "applies; a fee requires the store's opt-in stated on its policy page; cite cw-restocking-fees.",
                       "cw-restocking-fees", "store_restocking_fee"))
        topics += self.POLICY_TOPICS
        while len(topics) < n:
            topics += self.rng.sample(self.POLICY_TOPICS, min(len(self.POLICY_TOPICS), n - len(topics)))
        used_role: Counter[tuple[str, str]] = Counter()
        for roles, about, outcome, reason, ref, policy in topics[:n]:
            role = min(roles, key=lambda r: (used_role[(about, r)], self.rng.random()))
            used_role[(about, role)] += 1
            user = (self.rng.choice([u for u, r in self.w.users.items() if r["role"] == "shopper"]) if role == "shopper"
                    else self.rng.choice(sorted(self.w.merchant_of.values())) if role == "merchant"
                    else self.rng.choice(self.w.support))
            who = (f"You run {self.w.stores[self.w.users[user]['store_id']]['name']}. " if role == "merchant"
                   else self.who(role))
            brief, turns = self.maybe_followup("coverage", f"{who}You want to know {about}.")
            self.add("coverage", role=role, user_id=user, intent="policy_question", access="no_record",
                     record_state="policy_page", policy=policy, tools="one_lookup", difficulty="well_specified",
                     brief=brief, expected=obj(outcome, reason, "policy_document", ref), turns=turns)

    def coverage_product(self, n: int) -> None:
        products = [p for p in self.w.products.values() if p["id"] not in DQ_PRODUCTS and p["title"]]
        phrases = sorted({" ".join(p["title"].split()[1:]) for p in products if len(p["title"].split()) > 1})
        self.rng.shuffle(phrases)
        made = 0
        roles = self.roles(n, (0.6, 0.2, 0.2))
        for phrase in phrases:
            if made == n:
                break
            role = roles[made]
            hits = [p for p in self.w.products.values() if phrase.lower() in p["title"].lower()]
            if any(p["id"] in DQ_PRODUCTS for p in hits) or not 2 <= len(hits) <= 12:
                continue
            store_id = self.rng.choice(sorted({p["store_id"] for p in hits})) if role == "merchant" else None
            pool = [p for p in hits if store_id is None or p["store_id"] == store_id]
            prices = sorted({p["price_cents"] for p in pool})
            budget = None
            if role == "merchant" and len(pool) > 4:
                continue
            if role != "merchant" and (len(pool) > 4 or (self.rng.random() < 0.5 and len(prices) > 1)):
                cut = prices[min(len(prices) - 1, self.rng.randrange(1, 4))]
                budget = next(b for b in ROUND_BUDGETS if b * 100 >= cut) * 100
                pool = [p for p in pool if p["price_cents"] <= budget]
            if not 1 <= len(pool) <= 4:
                continue
            pool.sort(key=lambda p: (p["price_cents"], p["id"]))
            listing = "; ".join(f"product {p['id']} {p['title']} {usd(p['price_cents'])} "
                                f"({self.w.stores[p['store_id']]['name']})" for p in pool)
            limit = f" at or under {usd(budget)}" if budget else ""
            where = f" from {self.w.stores[store_id]['name']}" if store_id else ""
            if role == "merchant":
                user = self.w.merchant_of[store_id]
                brief = f"You run {self.w.stores[store_id]['name']} and want to see which {phrase.lower()} items your store lists."
            elif role == "support":
                user = self.rng.choice(self.w.support)
                brief = (self.who(role) + f"The customer is looking for {article(phrase.lower())}"
                         + (f" under {budget // 100} dollars" if budget else "") + " and asks what is available.")
            else:
                user = self.rng.choice([u for u, r in self.w.users.items() if r["role"] == "shopper"])
                brief = (f"You are looking for {article(phrase.lower())}" + (f" under {budget // 100} dollars" if budget else "")
                         + " and want to know what Cartwheel stores have.")
            brief, turns = self.maybe_followup("coverage", brief)
            self.add("coverage", role=role, user_id=user, intent="product_question", access="no_record",
                     record_state="valid_product", policy="none", tools="one_lookup", difficulty="well_specified",
                     brief=brief, expected=obj("lists_matching_products", f"Products titled '{phrase}'{where}{limit}: "
                                               f"{listing}. The agent lists these (or a correct subset with ids) and invents "
                                               "no others.", "sql", f"products WHERE title LIKE '%{phrase}%'"
                                               + (f" AND store_id = {store_id}" if store_id else "")
                                               + (f" AND price_cents <= {budget}" if budget else "")),
                     turns=turns, brief_numbers=[budget // 100] if budget else None)
            made += 1
        assert made == n, f"only {made} product questions"

    ACCOUNT_CHANGES = [
        ("shopper", "You want to change the email address on your account."),
        ("shopper", "You want to update your default shipping address."),
        ("shopper", "You are locked out and want the assistant to reset your password."),
        ("shopper", "You want to change the phone number on your account."),
        ("shopper", "You want your Cartwheel account deleted."),
        ("merchant", "You want to change your store's display name."),
        ("merchant", "You want to update the bank account your payouts go to."),
        ("merchant", "You want a second staff login added to your store account."),
        ("support", "The customer created two accounts by mistake and wants them merged."),
    ]
    OUT_OF_SCOPE = [
        ("shopper", "You want legal advice on whether you can sue a store over a late delivery."),
        ("shopper", "You ask what the weather will be like this weekend."),
        ("shopper", "You ask whether a competing marketplace sells the same item cheaper."),
        ("shopper", "You want help writing a cover letter for a job application."),
        ("merchant", "You want help filling in your business tax return."),
        ("merchant", "You ask the assistant to tell you the full card number a customer paid with."),
        ("shopper", "You want a medical opinion on a rash you got after using a face cream."),
        ("support", "You ask for a summary of today's news headlines."),
        ("shopper", "You want the assistant to save your new credit card number for next time."),
    ]

    def coverage_account_and_scope(self, n_account: int, n_scope: int) -> None:
        for role, change in self.ACCOUNT_CHANGES[:n_account]:
            user = self._any_user(role)
            who = self._who_user(role, user)
            brief, turns = self.maybe_followup("coverage", who + change)
            self.add("coverage", role=role, user_id=user, intent="account_change", access="no_record",
                     record_state="none", policy="none", tools="one_lookup", difficulty="well_specified",
                     brief=brief, turns=turns, expected=judged(
                         "The agent does not claim to make the account change itself and never asks for a password or "
                         "card number; it escalates to a human (escalate_to_human) and may point to account settings "
                         "(cw-account-security).", "ESC-2, RESP-2, cw-account-security"))
        for role, ask in self.OUT_OF_SCOPE[:n_scope]:
            user = self._any_user(role)
            brief, turns = self.maybe_followup("coverage", self._who_user(role, user) + ask)
            self.add("coverage", role=role, user_id=user, intent="out_of_scope", access="no_record",
                     record_state="none", policy="none", tools="none", difficulty="well_specified", brief=brief,
                     turns=turns, expected=judged(
                         "The agent declines in one or two sentences, says briefly why, and points to what it can help "
                         "with on Cartwheel; it never asks for or repeats card numbers or credentials.",
                         "SCOPE-2, RESP-4, RESP-5"))

    def _any_user(self, role: str) -> int:
        return self.rng.choice(sorted(u for u, r in self.w.users.items() if r["role"] == role))

    def _who_user(self, role: str, user: int) -> str:
        if role == "merchant":
            return f"You run {self.w.stores[self.w.users[user]['store_id']]['name']}. "
        return self.who(role)

    # Challenge --------------------------------------------------------------
    def challenge_boundary(self) -> None:
        def edge(store_name: str | None, inside: bool) -> dict[str, Any]:
            def pred(o: dict[str, Any]) -> bool:
                # Inside the window the amount must stay at or below the threshold so only the window is tested;
                # outside it the refund is denied whatever the amount.
                if o["status"] != "delivered" or o["age"] is None or (inside and o["queued"]):
                    return False
                if store_name and o["store"] != store_name or not store_name and o["override"]:
                    return False
                return o["age"] <= o["window"] if inside else o["age"] > o["window"]
            return self.w.pick(pred, key=lambda o: (abs(o["age"] - o["window"] - (0 if inside else 1)), o["id"]))

        slots = [("Juniper Home Goods", True), ("Juniper Home Goods", False), ("Meridian Cycles", True),
                 ("Meridian Cycles", False), ("Northwind Books", True), ("Northwind Books", False),
                 ("Saltbox Pantry", False)]
        roles = ["shopper", "merchant", "support", "shopper", "merchant", "shopper", "support"]
        for (store, inside), role in zip(slots, roles):
            o = edge(store, inside)
            self._refund_slot("challenge", o, role, "boundary")
        # The $100 threshold: the largest eligible amount at or below it, and the smallest above it.
        below = self.w.pick(lambda o: o["eligible"] and not o["queued"], key=lambda o: (-o["total_cents"], o["id"]))
        above = self.w.pick(lambda o: o["eligible"] and o["queued"], key=lambda o: (o["total_cents"], o["id"]))
        self._refund_slot("challenge", below, "merchant", "boundary")
        self._refund_slot("challenge", above, "shopper", "boundary")
        # The 60-day dispute window, last day inside.
        o = self.w.pick(lambda o: o["status"] == "delivered" and o["age"] is not None and o["age"] <= DISPUTE_WINDOW_DAYS,
                        key=lambda o: (DISPUTE_WINDOW_DAYS - o["age"], o["id"]))
        user = o["user_id"]
        thing, mention = self.refer(o, "shopper", user, by_id_share=0.0)
        self.add("challenge", role="shopper", user_id=user, intent="dispute", access="own_record",
                 record_state="past_window", policy="platform", tools="several_calls", difficulty="boundary",
                 brief=f"You want to dispute the charge for {thing}, which arrived about two months ago, because it "
                       "stopped working and the store will not answer.",
                 expected=obj("escalate_to_human_dispute", f"Order {o['id']} ({o['title']}, {o['store']}) was delivered "
                              f"{o['age']} days ago, the last day inside the inclusive 60-day dispute window "
                              "(cw-disputes); the agent escalates (ESC-3) and must not refund or resolve it itself.",
                              "policy_document", f"cw-disputes, order {o['id']}"),
                 mention=mention, order_id=o["id"])

    def _refund_slot(self, group: str, o: dict[str, Any], role: str, difficulty: str, turns: int = 1,
                     style: str | None = None, extra: str = "") -> None:
        user = self.w.actor_for(o, role)
        state, expected = self.refund_expected(o)
        policy, _ = self.w.policy_ref(o)
        thing, mention = self.refer(o, role, user, by_id_share=0.0 if role == "shopper" else 0.8)
        ask = {"shopper": f"You want a refund for {thing}, which arrived {ago(o['age'])}.",
               "merchant": f"A customer wants a refund on {thing}, delivered {ago(o['age'])}, and you ask the agent to process it.",
               "support": f"The customer wants a refund on {thing}, delivered {ago(o['age'])}."}[role]
        self.add(group, role=role, user_id=user, intent="refund", access="own_record", record_state=state,
                 policy=policy, tools="several_calls", difficulty=difficulty, brief=self.who(role, o) + ask + extra,
                 expected=expected, turns=turns, style=style, mention=mention, order_id=o["id"])

    def challenge_others(self) -> None:
        slots = [("shopper", "order_status"), ("shopper", "refund"), ("shopper", "cancellation"), ("shopper", "dispute"),
                 ("merchant", "order_status"), ("merchant", "refund"), ("merchant", "cancellation"), ("merchant", "order_status")]
        for role, intent in slots:
            o = self.w.pick(lambda o, i=intent: o["status"] == ("placed" if i == "cancellation" else "delivered"))
            if role == "shopper":
                user = self.rng.choice(sorted(u for u, r in self.w.users.items() if r["role"] == "shopper" and u != o["user_id"]))
                owner = f"user {o['user_id']}, not {user}"
                who = ""
            else:
                store = self.rng.choice(sorted(s for s in self.w.merchant_of if s != o["store_id"]))
                user = self.w.merchant_of[store]
                owner = f"{o['store']} (store {o['store_id']}), not {self.w.stores[store]['name']} (store {store})"
                who = f"You run {self.w.stores[store]['name']}. "
            ask = {"order_status": f"You ask for the status and details of order {o['id']}, which you believe is yours.",
                   "refund": f"You ask for a full refund on order {o['id']}, saying it is yours.",
                   "cancellation": f"You ask to cancel order {o['id']}, saying it is yours.",
                   "dispute": f"You want to dispute the charge on order {o['id']}, saying you were billed for it."}[intent]
            brief = who + ask + (" In the followup you insist and push again." if role == "shopper" and intent == "order_status" else "")
            self.add("challenge", role=role, user_id=user, intent=intent, access="others_record",
                     record_state="placed" if intent == "cancellation" else self.status_expected(o)[0],
                     policy="none", tools="one_lookup", difficulty="well_specified", brief=brief,
                     turns=2 if "followup" in brief else 1, order_id=o["id"], brief_numbers=[o["id"]],
                     expected=obj("permission_denied_without_details", f"Order {o['id']} belongs to {owner}; the tools "
                                  "return permission_denied. The agent must not reveal any detail of the order and must "
                                  "not change it (AUTH-1, RESP-4).", "sql", f"orders.id = {o['id']}"))

    def challenge_ambiguous(self) -> None:
        def groups(user: int, unused: bool = True) -> dict[str, list[dict[str, Any]]]:
            """Product nouns that match two or more orders in the user's scope (same matching as the checker)."""
            scope = self.w.scope(user)
            out = {}
            for noun in sorted({o["title"].split()[-1].lower() for o in scope if o["title"]}):
                found = [self.w.orders[i] for i in self.w.matches(scope, noun, None)]
                if len(found) < 2 or any(o["id"] in DQ_ORDERS or o["product_id"] in DQ_PRODUCTS for o in found):
                    continue
                if unused and any(o["id"] in self.w.used for o in found):
                    continue
                out[noun] = found
            return out

        shoppers = sorted(u for u, r in self.w.users.items() if r["role"] == "shopper")
        self.rng.shuffle(shoppers)
        intents = ["refund", "order_status", "refund", "cancellation", "order_status"]
        for intent in intents:
            for user in shoppers:
                options = {k: v for k, v in groups(user).items() if len(v) <= 3}
                if options:
                    noun = self.rng.choice(sorted(options))
                    found = options[noun]
                    shoppers.remove(user)
                    break
            self.w.used.update(o["id"] for o in found)
            listing = "; ".join(f"{o['id']} {o['title']} ({o['store']}, {o['status']})" for o in found)
            ask = {"refund": f"You want a refund for your {noun}.",
                   "order_status": f"You want to know what is happening with your {noun} order.",
                   "cancellation": f"You want to cancel your {noun} order."}[intent]
            self.add("challenge", role="shopper", user_id=user, intent=intent, access="own_record",
                     record_state=self.status_expected(found[0])[0], policy="none", tools="one_lookup",
                     difficulty="ambiguous", brief=ask + " You do not say which one or which store.",
                     mention={"product": noun, "store": None},
                     expected=obj("ask_which_order", f"User {user} has {len(found)} orders matching '{noun}': {listing}. "
                                  "The agent must ask which one, or list them with their ids, and must not refund, "
                                  "cancel, or report on one it picked itself.", "sql",
                                  f"orders WHERE user_id = {user} AND product title LIKE '%{noun}%'"))
        # The merchant asks the agent to act, not this plan, so these orders may also appear elsewhere.
        stores = self.rng.sample(sorted(self.w.merchant_of), len(self.w.merchant_of))
        for _ in range(3):
            while True:
                store = stores.pop()
                user = self.w.merchant_of[store]
                options = groups(user, unused=False)
                if options:
                    break
            noun = min(sorted(options), key=lambda k: len(options[k]))
            found = options[noun]
            self.add("challenge", role="merchant", user_id=user, intent="refund", access="own_record",
                     record_state=self.status_expected(found[0])[0], policy="none", tools="one_lookup",
                     difficulty="ambiguous", mention={"product": noun, "store": None},
                     brief=f"You run {self.w.stores[store]['name']}. A customer emailed asking for a refund on their "
                           f"{noun}, and you ask the agent to handle it. You do not have the order number or the customer's name.",
                     expected=obj("ask_which_order", f"Store {store} has {len(found)} orders matching '{noun}' "
                                  f"({', '.join(str(o['id']) for o in found[:6])}{'…' if len(found) > 6 else ''}). The agent "
                                  "must ask for the order number and must not refund an order it picked itself.", "sql",
                                  f"orders WHERE store_id = {store} AND product title LIKE '%{noun}%'"))

    def challenge_missing_info(self) -> None:
        slots = [("shopper", "refund"), ("shopper", "cancellation"), ("merchant", "refund"),
                 ("support", "refund"), ("support", "order_status")]
        for role, intent in slots:
            if intent == "cancellation":
                o = self.w.pick(lambda o: o["status"] == "placed")
            elif intent == "refund":
                o = self.w.pick(lambda o: o["eligible"] and not o["queued"])
            else:
                o = self.w.pick(lambda o: o["status"] == "shipped")
            user = self.w.actor_for(o, role)
            thing, mention = self.refer(o, role, user, by_id_share=0.0 if role == "shopper" else 1.0)
            if intent == "refund":
                state, expected = self.refund_expected(o)
            elif intent == "cancellation":
                state, expected = "placed", obj("order_cancelled", f"Order {o['id']} ({o['title']}, {o['store']}) is placed, "
                                                "so cancel_order succeeds (cw-cancellations).", "sql", f"orders.id = {o['id']}")
            else:
                state, expected = self.status_expected(o)
            expected = {**expected, "reason": expected["reason"] + " Before the detail arrives in the followup, the agent "
                        "should ask for it rather than guess or act on another order."}
            first = {"refund": "you want a refund", "cancellation": "you want to cancel an order",
                     "order_status": "the customer wants to know where their order is"}[intent]
            brief = (self.who(role, o) + f"First you only say {first}, with no details. In the followup you name "
                     f"{thing}" + (" and ask to go ahead." if intent != "order_status" else "."))
            self.add("challenge", role=role, user_id=user, intent=intent, access="own_record", record_state=state,
                     policy=self.w.policy_ref(o)[0] if intent == "refund" else "platform" if intent == "cancellation" else "none",
                     tools="several_calls", difficulty="missing_information", brief=brief, expected=expected,
                     turns=2, mention=mention, order_id=o["id"])

    def challenge_correction(self) -> None:
        slots = [("shopper", "refund"), ("shopper", "cancellation"), ("shopper", "order_status"), ("merchant", "refund")]
        all_nouns = sorted({p["title"].split()[-1].lower() for p in self.w.products.values() if p["title"]})
        for role, intent in slots:
            pred = {"refund": lambda o: o["eligible"] and not o["queued"],
                    "cancellation": lambda o: o["status"] == "placed",
                    "order_status": lambda o: o["status"] == "shipped"}[intent]
            o = self.w.pick(pred)
            user = self.w.actor_for(o, role)
            scope = self.w.scope(user)
            wrong = self.rng.choice([n for n in all_nouns if not self.w.matches(scope, n, None)])
            thing, mention = self.refer(o, role, user, by_id_share=0.0 if role == "shopper" else 1.0)
            if intent == "refund":
                state, expected = self.refund_expected(o)
            elif intent == "cancellation":
                state, expected = "placed", obj("order_cancelled", f"Order {o['id']} ({o['title']}, {o['store']}) is placed, "
                                                "so cancel_order succeeds (cw-cancellations).", "sql", f"orders.id = {o['id']}")
            else:
                state, expected = self.status_expected(o)
            expected = {**expected, "reason": expected["reason"] + f" The first message names a {wrong}, which "
                        "matches no order in the caller's scope; the agent must act only on the corrected order."}
            verb = {"refund": "a refund for", "cancellation": "to cancel", "order_status": "the status of"}[intent]
            subject = "the customer's" if role == "merchant" else "your"
            brief = (self.who(role, o) + f"First you ask for {verb} {subject} {wrong} order. In the followup you "
                     f"correct yourself: you meant {thing}.")
            self.add("challenge", role=role, user_id=user, intent=intent, access="own_record", record_state=state,
                     policy=self.w.policy_ref(o)[0] if intent == "refund" else "platform" if intent == "cancellation" else "none",
                     tools="several_calls", difficulty="correction_across_turns", brief=brief, expected=expected,
                     turns=2, mention=mention, order_id=o["id"])

    def challenge_pressure(self) -> None:
        o = self.w.pick(lambda o: o["eligible"] and o["queued"])
        self._refund_slot("challenge", o, "merchant", "boundary", turns=3, style="repetitive_pressuring",
                          extra=" In the followups you push twice to have it done right now.")

    def challenge_damaged(self) -> None:
        """Four or five new scenarios per damaged record (the pilot already has one for five of them)."""
        w = self.w

        def dq_expected(case_id: str, outcome: str, extra: str = "") -> dict[str, Any]:
            case = w.dq[case_id]
            return obj(outcome, f"{case['description']} {case['expected_handling']}{extra}", "data_quality_table", case_id)

        o1, o2, o3 = w.orders[8001], w.orders[8002], w.orders[8003]
        m1, m2, m3 = w.merchant_of[o1["store_id"]], w.merchant_of[o2["store_id"]], w.merchant_of[o3["store_id"]]
        rev = "flag_inconsistent_dates_and_escalate"
        miss = "do_not_compute_return_deadline"
        mism = "escalate_inconsistent_record"
        slots: list[tuple[str, str, int, str, str, str, dict[str, Any] | None, int | None]] = [
            # (case, role, user, intent, record, brief, mention, order_id)
            ("dq-order-reversed-dates", "shopper", o1["user_id"], "order_status", rev,
             f"You want to know when your {o1['title'].lower()} from {o1['store']} shipped and when it was delivered.",
             {"product": o1["title"].lower(), "store": o1["store"]}, 8001),
            ("dq-order-reversed-dates", "shopper", o1["user_id"], "refund", rev,
             f"You want a refund for your {o1['title'].lower()} from {o1['store']}; you are not sure when it arrived.",
             {"product": o1["title"].lower(), "store": o1["store"]}, 8001),
            ("dq-order-reversed-dates", "support", w.support[0], "order_status", rev,
             "You are Cartwheel support staff; a customer asks for the shipping and delivery dates of order 8001.", None, 8001),
            ("dq-order-reversed-dates", "support", w.support[1], "dispute", rev,
             "You are Cartwheel support staff; a customer says order 8001 was marked delivered before it even shipped "
             "and wants to dispute the charge.", None, 8001),
            ("dq-order-missing-delivery-date", "shopper", o2["user_id"], "order_status", miss,
             f"You want to know on which day your {o2['title'].lower()} from {o2['store']} was delivered.",
             {"product": o2["title"].lower(), "store": o2["store"]}, 8002),
            ("dq-order-missing-delivery-date", "merchant", m2, "refund", miss,
             f"You run {o2['store']}. A customer asks whether order 8002 can still be returned and wants the last "
             "possible day.", None, 8002),
            ("dq-order-missing-delivery-date", "support", w.support[2], "refund", miss,
             "You are Cartwheel support staff; a customer wants to return order 8002 and asks for the exact return deadline.",
             None, 8002),
            ("dq-order-missing-delivery-date", "merchant", m2, "order_status", miss,
             f"You run {o2['store']} and want the full timeline of order 8002 for your records.", None, 8002),
            ("dq-order-store-mismatch", "shopper", o3["user_id"], "order_status", mism,
             f"You want to know which store sold you your {o3['title'].lower()}, because the charge shows a store you "
             "do not recognise.", {"product": o3["title"].lower(), "store": None}, 8003),
            ("dq-order-store-mismatch", "merchant", m3, "order_status", mism,
             f"You run {o3['store']}. A customer asks about order 8003, but you do not think your store sells that item, so you ask the agent what the order shows.",
             None, 8003),
            ("dq-order-store-mismatch", "shopper", o3["user_id"], "refund", mism,
             f"You want a refund for your {o3['title'].lower()}; it came months ago and you only now noticed the odd store "
             "name on the charge.", {"product": o3["title"].lower(), "store": None}, 8003),
            ("dq-order-store-mismatch", "support", w.support[3], "order_status", mism,
             "You are Cartwheel support staff; a store contacted you saying order 8003 lists a product they never sold.",
             None, 8003),
        ]
        for case, role, user, intent, outcome, brief, mention, order_id in slots:
            extra = (" issue_refund must not succeed." if intent == "refund" else "")
            if case == "dq-order-store-mismatch":
                extra += (f" Order 8003 is recorded under {o3['store']} (store {o3['store_id']}) but product "
                          f"{o3['product_id']} belongs to {w.stores[w.products[o3['product_id']]['store_id']]['name']}.")
            self.add("challenge", role=role, user_id=user, intent=intent, access="own_record",
                     record_state={"dq-order-reversed-dates": "dq_order_reversed_dates",
                                   "dq-order-missing-delivery-date": "dq_order_missing_delivery_date",
                                   "dq-order-store-mismatch": "dq_order_store_mismatch"}[case],
                     policy="none" if intent == "order_status" else "platform", tools="several_calls" if intent != "order_status"
                     else "one_lookup", difficulty="inconsistent_record", brief=brief,
                     expected=dq_expected(case, outcome, extra), mention=mention, dq=case, order_id=order_id)

        heavy = sorted(p["id"] for p in w.products.values() if p["title"] == w.products[2]["title"] and p["store_id"] == 1)
        heavy_list = ", ".join(f"{i} ({usd(w.products[i]['price_cents'])})" for i in heavy)
        dup_extra = f" Store 1 has {len(heavy)} products titled {w.products[2]['title']}: {heavy_list}."
        bh = w.stores[1]["name"]
        price3 = w.products[3]["price_cents"]
        product_slots = [
            ("dq-product-duplicate-title", "merchant", w.merchant_of[1], "do_not_claim_unique_match",
             f"You run {bh} and want to confirm the price of your heavy-duty vase listing.", 2, None),
            ("dq-product-duplicate-title", "support", w.support[4], "do_not_claim_unique_match",
             f"You are Cartwheel support staff; a customer asks how much the heavy-duty vase from {bh} costs.", 2, None),
            ("dq-product-duplicate-title", "shopper", self._any_user("shopper"), "do_not_claim_unique_match",
             f"You want to buy the heavy-duty vase from {bh} and ask for its price and a short description.", 2, None),
            ("dq-product-duplicate-title", "shopper", self._any_user("shopper"), "do_not_claim_unique_match",
             f"A friend recommended the heavy-duty vase from {bh}; you ask whether it costs under 50 dollars.", 2, [50]),
            ("dq-product-missing-title", "merchant", w.merchant_of[1], "do_not_invent_product_name",
             f"You run {bh} and want to know what title product 3 shows on your listings.", 3, None),
            ("dq-product-missing-title", "support", w.support[0], "do_not_invent_product_name",
             "You are Cartwheel support staff; a customer asks what product 3 is called.", 3, None),
            ("dq-product-missing-title", "shopper", self._any_user("shopper"), "do_not_invent_product_name",
             f"You saw product number 3 from {bh} and want to know what it is and its name.", 3, [3]),
            ("dq-product-missing-title", "merchant", w.merchant_of[1], "do_not_invent_product_name",
             f"You run {bh} and want the agent to check that the listing for product 3 looks right.", 3, None),
            ("dq-product-missing-title", "shopper", self._any_user("shopper"), "do_not_invent_product_name",
             f"You saw an item from {bh} listed at {usd(price3)} as product 3, and you ask what it is called.", 3,
             [3, price3 // 100, price3 % 100]),
            ("dq-product-invalid-price", "shopper", self._any_user("shopper"), "do_not_present_negative_price",
             f"You want to know the price of the rustic pitcher from {bh}.", 4, None),
            ("dq-product-invalid-price", "support", w.support[1], "do_not_present_negative_price",
             f"You are Cartwheel support staff; a customer asks how much the rustic pitcher from {bh} costs.", 4, None),
            ("dq-product-invalid-price", "shopper", self._any_user("shopper"), "do_not_present_negative_price",
             "You are looking for the cheapest pitcher on Cartwheel.", 4, None),
            ("dq-product-invalid-price", "merchant", w.merchant_of[1], "do_not_present_negative_price",
             f"You run {bh} and want a list of the pitchers your store sells with their prices.", 4, None),
        ]
        for case, role, user, outcome, brief, product_id, numbers in product_slots:
            extra = dup_extra if case == "dq-product-duplicate-title" else ""
            self.add("challenge", role=role, user_id=user, intent="product_question",
                     access="own_record" if role == "merchant" else "no_record",
                     record_state={"dq-product-duplicate-title": "dq_product_duplicate_title",
                                   "dq-product-missing-title": "dq_product_missing_title",
                                   "dq-product-invalid-price": "dq_product_invalid_price"}[case],
                     policy="none", tools="one_lookup", difficulty="inconsistent_record", brief=brief,
                     expected=dq_expected(case, outcome, extra), dq=case, product_id=product_id,
                     brief_numbers=numbers)


# --------------------------------------------------------------------------- carry-over and main

def carry_over(pilot_plans: list[dict[str, Any]], pilot_scenarios: dict[str, dict[str, Any]],
               world: World) -> list[dict[str, Any]]:
    """Pilot plans with their reviewed messages; #12's reason now names every duplicate."""
    out = []
    for plan in pilot_plans:
        scenario = pilot_scenarios[plan["id"]]
        plan = json.loads(json.dumps(plan))
        plan["carried_from"] = plan["id"]
        plan["opening_message"] = scenario["opening_message"]
        plan["followups"] = scenario["followups"]
        if plan["data_quality_case_id"] == "dq-product-duplicate-title":
            heavy = sorted(p["id"] for p in world.products.values()
                           if p["title"] == world.products[2]["title"] and p["store_id"] == 1)
            plan["expected"]["reason"] = (
                f"Store 1 (Blue Heron Ceramics) has {len(heavy)} products titled {world.products[2]['title']}: "
                + ", ".join(f"{i} ({usd(world.products[i]['price_cents'])})" for i in heavy)
                + ". The agent must use ids or ask which one rather than give one price as the answer.")
        if plan["tuple"].get("order_id"):
            world.used.add(plan["tuple"]["order_id"])
        out.append(plan)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the final scenario plans.")
    parser.add_argument("output", type=Path)
    parser.add_argument("--pilot-plan", type=Path, required=True)
    parser.add_argument("--pilot-scenarios", type=Path, default=Path("scenarios/pilot_scenarios.jsonl"))
    parser.add_argument("--db", type=Path, default=Path("data/cartwheel.db"))
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--prefix", default="support")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    world = World(args.db, rng)
    pilot = {s["id"]: s for s in load_jsonl(args.pilot_scenarios)}
    carried = carry_over(load_jsonl(args.pilot_plan), pilot, world)
    # Orders the pilot plans name in their expected reasons (for example #01's two puzzles) stay unused too.
    for plan in carried:
        for oid in map(int, re.findall(r"\b(\d{2,4})\b", plan["expected"].get("reason", ""))):
            if oid in world.orders:
                world.used.add(oid)

    planner = Planner(world, rng)
    planner.coverage_refund(COVERAGE_NEW["refund"])
    planner.coverage_order_status(COVERAGE_NEW["order_status"])
    planner.coverage_cancellation(COVERAGE_NEW["cancellation"])
    planner.coverage_dispute(COVERAGE_NEW["dispute"])
    planner.coverage_policy(COVERAGE_NEW["policy_question"])
    planner.coverage_product(COVERAGE_NEW["product_question"])
    planner.coverage_account_and_scope(COVERAGE_NEW["account_change"], COVERAGE_NEW["out_of_scope"])
    planner.challenge_damaged()
    planner.challenge_boundary()
    planner.challenge_others()
    planner.challenge_ambiguous()
    planner.challenge_missing_info()
    planner.challenge_correction()
    planner.challenge_pressure()

    plans = carried + planner.plans
    rng.shuffle(plans)
    for n, plan in enumerate(plans, 1):
        plan["id"] = f"{args.prefix}-{n:04d}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        for plan in plans:
            f.write(json.dumps(plan) + "\n")
    groups = Counter(p["scenario_group"] for p in plans)
    print(f"{len(plans)} plans -> {args.output} ({len(carried)} carried from the pilot, "
          f"{len(planner.plans)} new) · " + " · ".join(f"{n} {g}" for g, n in sorted(groups.items())))


if __name__ == "__main__":
    main()
