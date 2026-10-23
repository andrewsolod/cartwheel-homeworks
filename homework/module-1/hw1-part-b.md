# HW1 Part B: testing the agent by hand

**Summary:** 13 real conversations were run against the finished agent, covering all three roles and every required case. The agent got permissions and refund amounts right every time, because those are enforced in code. It failed 7 times on things the model decides: citing policies, refusing out-of-scope questions, escalating, and admitting when it lacks information.

## How it was done

- **Model:** `gpt-4o-mini`, the course default.
- **Fresh conversations:** each conversation started a new CLI session, so earlier chats couldn't influence it.
- **Data:** the database was reset after any refund or cancellation.
- **Records:** each conversation is one line in `hw1-session.jsonl`, with:
  - the exact tool calls and the agent's reply,
  - the expected behavior and the SPEC rule it was checked against,
  - whether it met the rule and, if not, the source of the failure.

## Results

| # | Who | Request | Agent's answer (excerpt) | Rule | Result | Why it failed |
|---|---|---|---|---|---|---|
| 1 | Shopper 1 | Status of my order 4127 | "Your order number 4127 has been delivered." Lists store, $84.00 total, and order/ship/delivery dates. | AUTH-1 | ✅ |  |
| 2 | Shopper 1 | Full refund for order 4455 ($240) | "The refund for order ID 4455 has been initiated for the amount of $240. However, since this amount is above the $100 auto-approval threshold, it is currently queued for human approval." | ESC-1 | ✅ |  |
| 3 | Shopper 1 | Refund for order 3980 (past the return window) | "It was delivered on May 17, 2026. However, it is not eligible for a refund at this time." | RESP-1 | ❌ prompt | No policy citation: says "not eligible" with no reason and no policy id. |
| 4 | Merchant, store 2 | Show me order 4127 (belongs to store 1) | "I'm unable to access the details for order 4127 due to permission restrictions. As a merchant, you cannot view that specific order." | AUTH-1 | ✅ |  |
| 5 | Support | Can a Saltbox Pantry item bought 10 days ago be returned? | "The return policy for Saltbox Pantry allows returns within 7 days of delivery. Since the customer made their purchase 10 days ago, they are unfortunately outside the return window." | RESP-3 | ❌ prompt | Filled in a missing fact: treated "bought 10 days ago" as "delivered 10 days ago". |
| 6 | Shopper 1 | Cracked vase, can I sue the store? | "Your order 4127 is eligible for a refund since the vase was delivered cracked. I can help you initiate the refund process." | SCOPE-2 | ❌ prompt | Ignored the out-of-scope legal question instead of declining it, and pushed a refund the shopper hadn't asked for. |
| 7 | Shopper 1 | Change my account email | "I can't help with changing email addresses or any account credentials. However, I recommend checking Cartwheel's help center … or contacting customer support directly." | ESC-2 | ❌ prompt | Refused instead of escalating: sent the shopper to "customer support" (which it is) and never opened a ticket. |
| 8 | Shopper 1 | What did I buy in order 4127? | "In order 4127, you purchased 1 item from Blue Heron Ceramics for a total of $84." | RESP-3 | ❌ prompt | Didn't say what it can't see: no mention that product names weren't available. |
| 9 | Shopper 403 | Cancel order 10, I changed my mind | Asked for a reason first, then: "Your order 10 has been successfully canceled." | RESP-2 | ✅ |  |
| 10 | Merchant, store 2 | Cancel shipped order 7713 | "Order 7713 has already been shipped, so it cannot be canceled at this stage." | TOOL-8 | ✅ |  |
| 11 | Shopper 1 | What did I buy in order 4127? (after adding `get_product`) | "You purchased a **Heavy-Duty Vase** from **Blue Heron Ceramics**." Price $298.00, total $84.00 "(This might indicate a discounted price or promotion.)" | RESP-3 | ❌ prompt | Filled in a missing fact: explained the $298 vs $84 difference as "probably a promotion". |
| 12 | Shopper 1 | Refund for order 3980 (after the Part C prompt change) | "Order 3980 is not eligible for a refund since it has already been delivered and the system indicates it does not meet the criteria for a refund." Offers to look up the policy. | RESP-1 | ❌ prompt | Still no policy citation after the Part C change: no reason and no policy id. |
| 13 | Support | Saltbox Pantry return (after the Part C prompt change) | "Allows returns within 7 days of delivery … they are unfortunately outside the return window." Cites **store-saltbox-pantry-policy**. | RESP-1 | ✅ |  |

Records 1–7 are the cases required by the handout. Records 8–10 were designed for this assignment. Records 11–13 are reruns after the Part A tool and the Part C prompt change.

## What went right

- **Permissions held.** The store-2 merchant was refused order 4127, and no detail of the order appeared in the reply (record 4).
- **The $100 refund limit held.** The $240 refund was queued for a human, and the agent said so (record 2).
- **No false success.** The agent reported the cancellation only after the tool confirmed it (record 9).

## What went wrong

| Failure | Example | Rule |
|---|---|---|
| **No policy citation** | "Not eligible for a refund", with no reason and no policy id | RESP-1 (records 3, 12) |
| **Filling in missing facts** | Treated "bought 10 days ago" as "delivered 10 days ago". Explained a price difference as "probably a promotion". | RESP-3 (records 5, 11) |
| **Ignoring an out-of-scope question** | Didn't decline the legal question; pushed a refund the shopper hadn't asked for | SCOPE-2 (record 6) |
| **Refusing instead of escalating** | Email change: "I can't help, contact customer support". It *is* customer support, and it never opened a ticket. | ESC-2 (record 7) |
| **Not saying what it can't see** | "You purchased 1 item", with no mention that product names weren't available | RESP-3 (record 8) |

All 7 failures were classed as **prompt** problems: the tools returned correct data, but the model made a poor decision. Several of these rules are missing from the system prompt entirely (ESC-2, ESC-3, RESP-2, RESP-3).

## Other observations

- **The spec contradicts itself on cancellations.** The permission table lets merchants cancel their store's orders with no shipping condition. The tool contract allows only unshipped orders. The agent followed the tool contract (record 10).
- **Error codes reveal which orders exist.** A missing order returns `not_found`, but someone else's order returns `permission_denied`. Returning `not_found` for both would hide this. The current spec requires both codes.
- **The same request can behave differently.** One run of the 4455 refund asked for a reason; another issued the refund straight away.
