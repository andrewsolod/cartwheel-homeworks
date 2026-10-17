# HW1 Part C: getting the agent to cite policies

**Summary:** the agent often answered return and refund questions without citing the policy behind the answer. A one-sentence prompt edit fixed this when the agent had already looked up the policy, but not when it never looked the policy up. A second, more explicit edit made answers worse and was rejected.

## The problem

SPEC rule **RESP-1**: *cite the policy id for every claim that comes from a policy.*

The prompt already said "Cite the policy id … for every policy claim", but two Part B conversations ignored it:

| Conversation | What the agent did |
|---|---|
| Shopper asks for a refund on order 3980 (record 3) | Said "not eligible for a refund". Gave no reason and cited no policy. |
| Support asks if a Saltbox Pantry item can be returned (record 5) | Found the store's 7-day policy, but didn't cite it. |

## Why (hypothesis)

The prompt connects refunds only to the order lookup (`get_order`). It never says that a refund decision *is* a policy decision. So the agent treats "not eligible" as order data and doesn't look for a policy to cite.

## The change

One sentence added to the tool guidance in the system prompt (`agent/agent.py`):

```diff
 - Cite the policy id (for example cw-returns) for every policy claim.
+  A refund or return decision is a policy claim: when an order is not
+  eligible, look up the return policy and explain the reason with its id.
```

## How it was tested

Each of the two conversations was run 5 times before and 5 times after the change (`gpt-4o-mini`, fresh session each time). A second version (v2) named the search tool explicitly ("call search_help_center for the return policy") and was tested the same way.

## Results

| | Before | After (v1, kept) | v2 (rejected) |
|---|---|---|---|
| Saltbox: policy cited | 1 of 5 | **5 of 5** | 5 of 5 |
| Saltbox: correct return window (7 days) | 5 of 5 | 5 of 5 | **2 of 5** |
| Order 3980: policy looked up | 0 of 5 | 0 of 5 | 0 of 5 |

- **Saltbox improved.** The agent already had the policy in front of it; the new sentence made it cite the policy every time.
- **Order 3980 did not improve.** The agent still never looked up the return policy. It sometimes cited `cw-returns`, probably copied from the example in the prompt, but never gave the real reason (delivered 45 days ago, past the 30-day window).
- **v2 made things worse.** Mentioning "the return policy" pushed the agent toward the platform's general 30-day policy. In 3 of 5 runs it told support the customer had 30 days, ignoring Saltbox's 7-day rule. v2 was reverted.

## Before and after

**Saltbox:** fixed.

> **Before:** "…returns within 7 days of delivery. Since the customer made their purchase 10 days ago, they are … outside the return window…"
>
> **After:** "…returns within 7 days of delivery. … outside the return window. For more details, you can refer to the policy id: **store-saltbox-pantry-policy**."

**Order 3980:** not fixed.

> **Before:** "…it is not eligible for a refund at this time."
>
> **After:** "…not eligible for a refund since it has already been delivered… I can look up the specific return and refund policy for more details. Would you like me to do that?"

## Takeaways

1. **Keep v1.** It is the smallest change supported by evidence, and it broke nothing.
2. **A prompt edit can break a case that worked.** v2 was only caught because the Saltbox case was rerun too.
3. **Order 3980 needs a code fix, not a prompt fix.** `get_order` returns only `refund_eligible: false`. If it also returned the reason and policy id (for example "delivered 45 days ago; return window is 30 days", `cw-returns`), the agent could simply repeat them. This was not built for HW1.

**Still open (separate issue):** in the Saltbox case, the agent treats the purchase date as the delivery date (RESP-3).

Records: before = 3 and 5 (prompt `d108f6949e5e`); after = 12 and 13 (prompt `1feec05dc75d`) in `hw1-session.jsonl`. A test (`tests/test_prompt_tool_names.py`) checks that any tool named in the prompt actually exists.
