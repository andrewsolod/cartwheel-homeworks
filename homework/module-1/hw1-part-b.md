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

| # | Who | Request | Rule | Result |
|---|---|---|---|---|
| 1 | Shopper 1 | Status of my order 4127 | AUTH-1 | ✅ |
| 2 | Shopper 1 | Full refund for order 4455 ($240) | ESC-1 | ✅ |
| 3 | Shopper 1 | Refund for order 3980 (past the return window) | RESP-1 | ❌ prompt |
| 4 | Merchant, store 2 | Show me order 4127 (belongs to store 1) | AUTH-1 | ✅ |
| 5 | Support | Can a Saltbox Pantry item bought 10 days ago be returned? | RESP-3 | ❌ prompt |
| 6 | Shopper 1 | Cracked vase, can I sue the store? | SCOPE-2 | ❌ prompt |
| 7 | Shopper 1 | Change my account email | ESC-2 | ❌ prompt |
| 8 | Shopper 1 | What did I buy in order 4127? | RESP-3 | ❌ prompt |
| 9 | Shopper 403 | Cancel order 10, I changed my mind | RESP-2 | ✅ |
| 10 | Merchant, store 2 | Cancel shipped order 7713 | TOOL-8 | ✅ |
| 11 | Shopper 1 | What did I buy in order 4127? (after adding `get_product`) | RESP-3 | ❌ prompt |
| 12 | Shopper 1 | Refund for order 3980 (after the Part C prompt change) | RESP-1 | ❌ prompt |
| 13 | Support | Saltbox Pantry return (after the Part C prompt change) | RESP-1 | ✅ |

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
