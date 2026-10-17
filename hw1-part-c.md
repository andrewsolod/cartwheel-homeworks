# HW1 Part C: policy citations (RESP-1)

## Requirement examined

**RESP-1** (SPEC.md): cite the policy identifier for every claim derived from a policy document.

The original prompt already had the rule: "Cite the policy id (for example cw-returns) for every policy claim" (`agent/agent.py`). So this rule was not missing from the prompt; it was **too vague**. Two Part B conversations broke it:

- **Record 3:** shopper 1 asks for a refund on order 3980. The agent says it is "not eligible for a refund at this time" without giving a reason or citing a policy.
- **Record 5:** support asks whether a Saltbox Pantry purchase can be returned. The agent applies the 7-day store override but cites no policy.

## Hypothesis

The prompt links refunds only to `get_order` ("Never promise or issue a refund before calling get_order and checking the order's refund eligibility"). Nothing says that a refund decision **is a policy decision**. So the model treats the `refund_eligible: false` flag as the whole answer and never retrieves or cites the return policy.

## Method

- **Model and data:** `gpt-4o-mini`, the default seeded data, and the same tools in every run, including `get_product`.
- **Runs:** two fixed requests, each run 5 times per prompt version in a fresh CLI session (`--debug`):
  - shopper 1: "I'd like a refund for order 3980."
  - support 9501: "A customer bought something from Saltbox Pantry 10 days ago. Can they still return it?"
- **Scoring:** each run was checked for which tools were called, whether the reply contains a policy id, and which return window it states.

## Prompt versions

| Version | `prompt_version` | Change to the citation line |
|---|---|---|
| Original | `d108f6949e5e` | None |
| v1 (kept) | `1feec05dc75d` | + "A refund or return decision is a policy claim: when an order is not eligible, look up the return policy and explain the reason with its id." |
| v2 (rejected) | `54ed00665ebf` | v1 with "look up the return policy" replaced by "call search_help_center for the return policy before replying" |

## Results (5 runs per cell)

| | Original | v1 | v2 |
|---|---|---|---|
| 3980: a policy tool was called | 0/5 | 0/5 | 0/5 |
| 3980: a policy id was cited | 0/5 | 2/5 | 0/5 |
| Saltbox: a policy id was cited | 1/5 | **5/5** | 5/5 |
| Saltbox: the wrong window (30 days instead of the store's 7) | 0/5 | 0/5 | **3/5** |

- **v1 fixed citation when the policy was already retrieved.** Saltbox went from 1/5 to 5/5, with no change in correctness.
- **v1 did not make the model retrieve a policy it didn't know it needed.** In 3980 the two cw-returns citations appeared without any policy lookup. They were probably copied from the prompt's own example ("for example cw-returns"), and no reply gave the real reason (delivered 45 days earlier, outside the 30-day window). One run offered to look the policy up instead of doing it.
- **v2 regressed.** Naming the tool did not help 3980: 0/5 lookups, and the replies offered to look the policy up or gave a wrong reason ("since it has already been delivered"). Worse, "the return policy" pulled the Saltbox answers toward the platform's cw-returns. In 3 of 5 runs, support was told the customer had 30 days, ignoring Saltbox's 7-day override. **v2 was rejected and the prompt was reverted to v1.**

## Before and after (same request, fresh session)

**Saltbox, support 9501.** Before: record 5, `d108f6949e5e`.

> The return policy for Saltbox Pantry allows returns within 7 days of delivery. Since the customer made their purchase 10 days ago, they are unfortunately outside the return window and cannot return the item.

After: record 13, `1feec05dc75d`. RESP-1 is now met.

> Saltbox Pantry has a specific return policy that allows returns within 7 days of delivery. Since the customer purchased their item 10 days ago, they are unfortunately outside the return window. For more details, you can refer to the policy id: **store-saltbox-pantry-policy**.

**3980, shopper 1.** Before: record 3, `d108f6949e5e`.

> I've checked order 3980, and it was delivered on May 17, 2026. However, it is not eligible for a refund at this time.

After: record 12, `1feec05dc75d`. RESP-1 is still not met.

> Order 3980 is not eligible for a refund since it has already been delivered and the system indicates it does not meet the criteria for a refund. If you'd like, I can look up the specific return and refund policy for more details. Would you like me to do that?

## Conclusion

- **The v1 instruction was kept.** It is the smallest edit backed by evidence: citation rose from 1/5 to 5/5 where the policy had been retrieved, with no regression.
- **The 3980 case is not fixed by prompt wording.** The likely robust fix is in the tool. `get_order` returns a bare `refund_eligible` flag. It could also return the reason and the governing policy id, computed in code with the same `effective_return_window_days()` used to set the flag, for example "delivered 2026-05-17, 45 days ago; return window is 30 days" and `cw-returns`. This was not implemented in HW1, because it changes a supplied tool and its contract.
- **A guard test now protects tool names in the prompt.** `tests/test_prompt_tool_names.py` fails if the prompt names a tool that is not registered, so a renamed tool cannot silently break an instruction.
- **Still open, separate from RESP-1:** the Saltbox answers still treat the purchase date as the delivery date (RESP-3, records 5 and 13).
