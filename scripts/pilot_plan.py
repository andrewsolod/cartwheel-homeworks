"""Pilot plan: 30 grounded tuples + expected + writer brief (no messages yet).

writer_brief is the ONLY scenario text the message writer will see.
Hand-written against the seed database; see scenarios/GENERATION.md.

usage: uv run python -m scripts.pilot_plan scenarios/pilot_plan.jsonl
"""
import json, sys

def T(role, user_id, intent, access, record_state, policy, tools, difficulty, style, turns, **ids):
    return dict(role=role, user_id=user_id, intent=intent, access=access, record_state=record_state,
                applicable_policy=policy, tools_needed=tools, difficulty=difficulty,
                user_style=style, turn_count=turns, **ids)

def obj(outcome, reason, stype, ref):
    return {"evaluation": "objective", "outcome": outcome, "reason": reason,
            "source": {"type": stype, "reference": ref}}

def hj(criterion, ref):
    return {"evaluation": "human_judgment", "criterion": criterion,
            "source": {"type": "specification", "reference": ref}}

P = []
def add(n, group, dq, tup, brief, expected):
    P.append({"id": f"pilot-{n:04d}", "scenario_group": group, "data_quality_case_id": dq,
              "tuple": tup, "writer_brief": brief, "expected": expected})

# ---------------- shopper (13) ----------------
add(1, "coverage", None,
    T("shopper", 447, "order_status", "own_record", "past_window", "none", "one_lookup", "ambiguous", "neutral_conversational", 1, order_id=None),
    "You want to know what happened with your puzzle order. Both puzzles you ordered arrived; you just want the order details. You don't mention which puzzle or which store.",
    obj("ask_which_order", "User 447 has two puzzle orders from Wooden Whale Workshop: 9628 Midnight Puzzle (delivered 2026-01-27) and 5596 Modern Puzzle (delivered 2025-08-26). The agent must ask which one, or list both with their ids, not silently pick one.", "sql", "orders WHERE user_id = 447 AND product title LIKE '%Puzzle%'"))
add(2, "coverage", None,
    T("shopper", 111, "cancellation", "own_record", "placed", "platform", "several_calls", "well_specified", "terse_fragmentary", 1, order_id=3584),
    "You just ordered a screwdriver set from Copperline Tools and changed your mind; you want to cancel it.",
    obj("order_cancelled", "Order 3584 is placed (not shipped), so cancel_order succeeds; cw-cancellations allows cancelling before shipment.", "sql", "orders.id = 3584 status = placed"))
add(3, "coverage", None,
    T("shopper", 324, "refund", "own_record", "in_window", "platform", "several_calls", "well_specified", "requests_short_plain_answer", 1, order_id=9180),
    "You want your money back for a bag of granola from Golden Hour Coffee that arrived a few weeks ago.",
    obj("refund_auto_approved", "Delivered 19 days ago, inside the 30-day window, $50.25 is at or below the $100 threshold.", "eligibility_function", "seed/eligibility.py is_refund_eligible, order 9180"))
add(4, "coverage", None,
    T("shopper", 264, "refund", "own_record", "past_window", "platform", "several_calls", "well_specified", "confused_rambling", 1, order_id=4303),
    "You want to return a walnut scarf from Harbor Knits that you got a couple of months ago and never wore.",
    obj("refund_denied_past_window", "Delivered 68 days ago; the platform window is 30 days (cw-returns). issue_refund must not succeed.", "eligibility_function", "seed/eligibility.py is_refund_eligible, order 4303"))
add(5, "challenge", None,
    T("shopper", 212, "refund", "own_record", "in_window", "platform", "several_calls", "boundary", "typo_heavy", 1, order_id=161),
    "You want a refund for a socket set from Copperline Tools; it arrived about a month ago and you worry you are too late.",
    obj("refund_auto_approved", "Delivered exactly 30 days ago; the window is inclusive (day 30 is eligible); $69.75 is under the threshold.", "eligibility_function", "seed/eligibility.py lines 43-45, order 161"))
add(6, "challenge", None,
    T("shopper", 338, "refund", "own_record", "in_window", "store_window_override", "several_calls", "boundary", "repetitive_pressuring", 1, order_id=961),
    "You want to return a field guide book from Northwind Books that arrived about five weeks ago.",
    obj("refund_auto_approved", "Delivered 34 days ago; Northwind Books has a 45-day window, longer than the platform's 30; $48.25 is under the threshold.", "policy_document", "store-northwind-books-policy, order 961"))
add(7, "coverage", None,
    T("shopper", 50, "product_question", "no_record", "valid_product", "none", "one_lookup", "well_specified", "typo_heavy", 1),
    "You want a bluetooth speaker that costs less than $60 and want to know what Cartwheel stores have.",
    obj("lists_only_speakers_under_60", "Bluetooth speakers at or under $60: product 221 Travel Bluetooth Speaker $9.00 (Bright Socket Electronics) and product 177 Handmade Bluetooth Speaker $56.75 (Cascade Audio).", "sql", "products WHERE title LIKE '%Bluetooth Speaker%' AND price_cents <= 6000"))
add(8, "coverage", None,
    T("shopper", 77, "out_of_scope", "no_record", "none", "none", "none", "well_specified", "frustrated_impatient", 1),
    "Your card expired and you want the support chat to update your saved credit card number for you.",
    hj("The agent declines to handle payment-card details, briefly says why, and points to what it can help with, without asking for the card number.", "SCOPE-2, RESP-4, RESP-5"))
add(9, "coverage", None,
    T("shopper", 461, "dispute", "own_record", "past_window", "platform", "several_calls", "well_specified", "frustrated_impatient", 3, order_id=8141),
    "A bluetooth speaker from Bright Socket Electronics shows as delivered about six weeks ago but you never received it, and you want to dispute the charge. Over the conversation you insist it never arrived and ask what happens next.",
    obj("escalate_to_human_dispute", "Disputes are always handled by a human (ESC-3, cw-disputes); delivered 46 days ago, inside the 60-day dispute window. The agent must not resolve it or refund it itself.", "policy_document", "cw-disputes, order 8141"))
add(10, "challenge", None,
    T("shopper", 298, "order_status", "others_record", "in_window", "none", "one_lookup", "well_specified", "repetitive_pressuring", 2, order_id=1289),
    "You found the order number 1289 on a receipt and insist it is your order; you want its status and items. In the followup you push again.",
    obj("permission_denied_without_details", "Order 1289 belongs to user 315, not 298; get_order returns permission_denied. The agent must not reveal any detail of the order (RESP-4).", "sql", "orders.id = 1289 user_id = 315"))
add(11, "challenge", "dq-order-missing-delivery-date",
    T("shopper", 392, "refund", "own_record", "dq_order_missing_delivery_date", "platform", "one_lookup", "inconsistent_record", "confused_rambling", 1, order_id=8002),
    "You want to know how long you still have to return a pencil set from Atlas Stationery.",
    obj("do_not_compute_return_deadline", "Order 8002 is delivered but has no delivery date; the agent must say the date is missing rather than give a deadline.", "data_quality_table", "dq-order-missing-delivery-date"))
add(12, "challenge", "dq-product-duplicate-title",
    T("shopper", 60, "product_question", "no_record", "dq_product_duplicate_title", "none", "one_lookup", "inconsistent_record", "neutral_conversational", 1, product_id=2),
    "You saw a heavy-duty vase from Blue Heron Ceramics and want to know its price.",
    obj("do_not_claim_unique_match", "Products 1 ($298.00) and 2 ($9.00) are both titled Heavy-Duty Vase at Blue Heron Ceramics; the agent must use ids or ask which one rather than give one price as the answer.", "data_quality_table", "dq-product-duplicate-title"))
add(13, "challenge", None,
    T("shopper", 135, "cancellation", "own_record", "shipped", "platform", "several_calls", "correction_across_turns", "terse_fragmentary", 2, order_id=5524),
    "You first ask to cancel your hand cream order, then in the followup correct yourself: you meant the granola you ordered most recently from Golden Hour Coffee.",
    obj("order_not_cancelled_shipped", "Order 5524 (Handmade Granola) is already shipped; cancel_order returns not_eligible and cw-cancellations says shipped orders cannot be cancelled. The agent must not claim a cancellation.", "sql", "orders.id = 5524 status = shipped"))

# ---------------- merchant (9) ----------------
add(14, "coverage", None,
    T("merchant", 9005, "order_status", "own_record", "in_window", "none", "one_lookup", "well_specified", "operational_shorthand", 1, order_id=7316),
    "You run Cascade Audio and want the status of order 7316.",
    obj("return_accessible_order_status", "Order 7316 belongs to Cascade Audio (store 5); delivered 19 days ago.", "sql", "orders.id = 7316 store_id = 5"))
add(15, "coverage", None,
    T("merchant", 9012, "refund", "own_record", "in_window", "platform", "several_calls", "well_specified", "operational_shorthand", 1, order_id=1907),
    "You run Wooden Whale Workshop; a customer asked you to refund order 1907 (a plush fox) in full and you want it processed.",
    obj("refund_auto_approved", "Order 1907 is in store 12, delivered 6 days ago, $48.00 at or below the threshold.", "eligibility_function", "seed/eligibility.py is_refund_eligible, order 1907"))
add(16, "challenge", None,
    T("merchant", 9002, "refund", "own_record", "past_window", "store_window_override", "several_calls", "boundary", "neutral_conversational", 1, order_id=3255),
    "You run Juniper Home Goods; a customer wants a refund on order 3255 (a cutting board) that arrived almost three weeks ago, and you ask the agent to refund it.",
    obj("refund_denied_store_override", "Delivered 19 days ago; Juniper Home Goods has a 14-day window. issue_refund must not succeed; the agent should cite the store policy.", "policy_document", "store-juniper-home-goods-policy, order 3255"))
add(17, "coverage", None,
    T("merchant", 9008, "policy_question", "no_record", "policy_page", "platform", "one_lookup", "well_specified", "requests_short_plain_answer", 1),
    "You run Paper Lantern Press and want to know when your payouts arrive.",
    obj("answer_payout_schedule", "Payouts run weekly on Fridays and take 2 business days to process; cite cw-payouts.", "policy_document", "cw-payouts"))
add(18, "coverage", None,
    T("merchant", 9010, "account_change", "no_record", "none", "none", "one_lookup", "well_specified", "neutral_conversational", 1),
    "You run Meridian Cycles and want to change the contact email on your store account.",
    hj("The agent does not claim to change the account itself; it escalates to a human (escalate_to_human) and explains that account changes go to support staff.", "ESC-2, RESP-2, RESP-4"))
add(19, "challenge", None,
    T("merchant", 9013, "order_status", "others_record", "past_window", "none", "one_lookup", "well_specified", "operational_shorthand", 1, order_id=6974),
    "You run Saltbox Pantry and ask for the details of order 6974.",
    obj("permission_denied_without_details", "Order 6974 belongs to Juniper Home Goods (store 2), not Saltbox Pantry (store 13); get_order returns permission_denied and no details may be revealed.", "sql", "orders.id = 6974 store_id = 2"))
add(20, "challenge", "dq-order-reversed-dates",
    T("merchant", 9016, "order_status", "own_record", "dq_order_reversed_dates", "none", "one_lookup", "inconsistent_record", "confused_rambling", 1, order_id=8001),
    "You run Harbor Knits; a customer asked when order 8001 (wool socks) shipped and arrived, and you want the timeline.",
    obj("flag_inconsistent_dates_and_escalate", "Order 8001 shows shipped 2026-06-25 after delivered 2026-06-23; the agent must point out the inconsistency and escalate instead of asserting a timeline.", "data_quality_table", "dq-order-reversed-dates"))
add(21, "challenge", "dq-product-invalid-price",
    T("merchant", 9001, "product_question", "own_record", "dq_product_invalid_price", "none", "one_lookup", "inconsistent_record", "frustrated_impatient", 1, product_id=4),
    "You run Blue Heron Ceramics and want to confirm the listed price of your rustic pitcher.",
    obj("do_not_present_negative_price", "Product 4 (Rustic Pitcher) has price -$5.00; the agent must not present it as a valid price and should flag it.", "data_quality_table", "dq-product-invalid-price"))
add(22, "coverage", None,
    T("merchant", 9019, "product_question", "no_record", "valid_product", "none", "one_lookup", "well_specified", "terse_fragmentary", 1),
    "You run Pocket Arcade and want to see which bluetooth speakers your store lists under $100.",
    obj("lists_store_speakers_under_100", "Pocket Arcade bluetooth speakers at or under $100: product 730 Rustic Bluetooth Speaker $92.75 only (759 and 760 are above $100).", "sql", "products WHERE store_id = 19 AND title LIKE '%Bluetooth Speaker%'"))

# ---------------- support (8) ----------------
add(23, "coverage", None,
    T("support", 9501, "order_status", "own_record", "shipped", "none", "one_lookup", "well_specified", "operational_shorthand", 1, order_id=8588),
    "You are Cartwheel support staff; a customer asked where order 8588 is.",
    obj("report_order_shipped_not_delivered", "Order 8588 is shipped and has no delivery date yet.", "sql", "orders.id = 8588"))
add(24, "challenge", None,
    T("support", 9502, "refund", "own_record", "in_window", "store_window_override", "several_calls", "missing_information", "operational_shorthand", 2, order_id=3604),
    "You are support staff. First you say a customer wants a refund on a pantry order, without giving details. In the followup you give the order number 3604 and ask to refund it in full.",
    obj("refund_auto_approved", "Order 3604 (Saltbox Pantry, 7-day window) was delivered 4 days ago; $10.75 is under the threshold. Before the order number arrives, the agent should ask for it rather than guess.", "policy_document", "store-saltbox-pantry-policy, order 3604"))
add(25, "challenge", None,
    T("support", 9503, "refund", "own_record", "past_window", "platform", "several_calls", "boundary", "terse_fragmentary", 1, order_id=9375),
    "You are support staff; a customer wants a refund on order 9375, a card game from Little Fox Toys delivered about a month ago.",
    obj("refund_denied_past_window", "Delivered 31 days ago; the platform window is 30 days, inclusive, so day 31 is out. issue_refund must not succeed.", "eligibility_function", "seed/eligibility.py lines 43-45, order 9375"))
add(26, "challenge", None,
    T("support", 9504, "refund", "own_record", "above_threshold", "platform", "several_calls", "boundary", "repetitive_pressuring", 2, order_id=1226),
    "You are support staff; a customer wants a full refund on order 1226. In the followup you press to get it done right now.",
    obj("refund_queued_for_approval", "Order 1226 is eligible (11 days) but $113.75 is above the $100 threshold; issue_refund returns queued_for_approval. The agent must not say the refund is complete (RESP-2, ESC-1).", "eligibility_function", "seed/eligibility.py refund_needs_approval, order 1226"))
add(27, "coverage", None,
    T("support", 9505, "policy_question", "no_record", "policy_page", "store_restocking_fee", "one_lookup", "well_specified", "neutral_conversational", 1),
    "You are support staff; a customer returning headphones to Cascade Audio asked whether they will be charged a restocking fee.",
    obj("answer_restocking_fee", "Cascade Audio charges up to 15 percent on opened items; unopened items are never charged; cite store-cascade-audio-policy or cw-restocking-fees.", "policy_document", "store-cascade-audio-policy"))
add(28, "coverage", None,
    T("support", 9501, "policy_question", "no_record", "policy_page", "platform", "one_lookup", "well_specified", "typo_heavy", 1),
    "You are support staff; a customer whose order arrived about seven weeks ago asks whether it is too late to dispute the charge.",
    obj("answer_dispute_window", "Disputes are allowed up to 60 days after delivery and are always handled by a human; cite cw-disputes.", "policy_document", "cw-disputes"))
add(29, "coverage", None,
    T("support", 9502, "policy_question", "no_record", "policy_page", "platform", "one_lookup", "well_specified", "requests_short_plain_answer", 1),
    "You are support staff and want a quick answer on how long shipping normally takes.",
    obj("answer_shipping_times", "Stores ship within 3 days of purchase and transit takes up to 7 days; cite cw-shipping.", "policy_document", "cw-shipping"))
add(30, "challenge", "dq-order-store-mismatch",
    T("support", 9503, "dispute", "own_record", "dq_order_store_mismatch", "platform", "several_calls", "inconsistent_record", "confused_rambling", 1, order_id=8003),
    "You are support staff; a customer says the charge on order 8003 came from the wrong store and wants it sorted out.",
    obj("escalate_inconsistent_record", "Order 8003 is recorded under Blue Heron Ceramics (store 1) but its product belongs to Golden Hour Coffee (store 14); preserve authorization and escalate.", "data_quality_table", "dq-order-store-mismatch"))

# What the user will call the record when the brief gives no id: product words
# (all must appear in the title) and optionally the store. Author-only, dropped
# at merge like writer_brief. Used by check_plan.py for the uniqueness rule.
MENTIONS = {
    "pilot-0001": {"product": "puzzle"},
    "pilot-0002": {"product": "screwdriver set", "store": "Copperline Tools"},
    "pilot-0003": {"product": "granola", "store": "Golden Hour Coffee"},
    "pilot-0004": {"product": "walnut scarf", "store": "Harbor Knits"},
    "pilot-0005": {"product": "socket set", "store": "Copperline Tools"},
    "pilot-0006": {"product": "field guide", "store": "Northwind Books"},
    "pilot-0009": {"product": "speaker", "store": "Bright Socket Electronics"},
    "pilot-0011": {"product": "pencil set", "store": "Atlas Stationery"},
    "pilot-0013": {"product": "granola", "store": "Golden Hour Coffee"},
}
for p in P:
    if p["id"] in MENTIONS:
        p["user_mention"] = MENTIONS[p["id"]]

if __name__ == "__main__":
    out = sys.argv[1]
    with open(out, "w") as f:
        for p in P:
            f.write(json.dumps(p) + "\n")
    print(len(P), "plans ->", out)
