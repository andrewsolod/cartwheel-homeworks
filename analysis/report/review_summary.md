# Review summary

I reviewed 100 distinct Cartwheel conversations from the Module 1 trace store (250 conversations, 322 traces; one trace per user turn) in the review app (`analysis/review_app/`). The unit of review is the conversation, identified by the id of its first trace, so labels join with the HW5 helpers. Selection, notes, the taxonomy and labels are in `analysis/state/`.

## Reviewed sample

| Batch | Method | Size | Shopper / merchant / support | Coded by |
|---|---|---|---|---|
| 1 | 15 k-means representatives (k = 8 on conversation shape: turns, tool calls, distinct tools, retrieval, words; nearest member to each centroid) + 15 uniform random | 30 | 13 / 9 / 8 | me |
| 2 | 10 uniform random per role (role chosen as the dimension before looking at outcomes) | 30 | 10 / 10 / 10 | me for 6; 24 from coding-agent suggestions that I accepted (18) or rejected and recoded (6) |
| 3 | Depth search: for each weak candidate mode, random picks from the conversations whose situation lets the mode occur (e.g. a refund request with an eligible `get_order`); quotas 4/4/4/5/4/4 | 25 | 14 / 5 / 6 | coding-agent suggestions, each accepted (21) or rejected (4) by me |
| 4 | 15 uniform random from the rest (saturation check) | 15 | 8 / 4 / 3 | me, without suggestions |
| **Total** | seed 20261111; no conversation in two batches | **100** | **45 / 28 / 27** | |

Intents: refund 28, product question 19, order status 14, policy question 13, cancellation 11, dispute 6, account change 5, out of scope 4. 68 coverage and 32 challenge scenarios; 26 multi-turn conversations.

Selection never used outcomes. Batch 3 filters describe a situation, not a failure, so each pool held candidate positives and close negatives.

## Final taxonomy

Eight binary modes (`analysis/state/patterns.json`, all `confirmed`). Positives and close negatives are the examples the mode was built and checked from; Fail labels are the Part E judgments on all 100 conversations.

| Mode | Requirement | Evaluator | Positives / close negatives | Fail labels (sample fraction) |
|---|---|---|---|---|
| `guesses_order_without_lookup`: the agent names, picks or rules out an order without `find_order`/`get_order` returning it (`list_my_orders` has no titles) | RESP-3 | code check | 12 / 3 | 13 (13%) |
| `policy_stated_without_lookup`: a policy claim with no lookup supporting it, or beyond what the lookup returned | RESP-1, RESP-3 | LLM judge | 8 / 4 | 11 (11%) |
| `inconsistent_record_not_flagged`: a retrieved record is contradictory (missing delivery date, reversed dates, negative price, duplicate titles, store mismatch) and the reply presents it as normal | RESP-3 | LLM judge | 8 / 1 | 9 (9%) |
| `required_escalation_missing`: an account change (ESC-2) or dispute (ESC-3) without an `escalate_to_human` call; only offering counts as missing | ESC-2, ESC-3 | code check + LLM judge to classify the case | 8 / 0 | 9 (9%) |
| `eligible_action_not_taken`: an eligible refund or cancellation the user asked for, but no `issue_refund`/`cancel_order` call | RESP-6 (revision) | code check | 6 / 4 | 8 (8%) |
| `wrong_policy_applied`: the answer rests on the wrong policy or misapplies the right one | RESP-1 | code check + LLM judge | 5 / 3 | 6 (6%) |
| `permission_denial_mishandled`: after `permission_denied`, more than a short refusal in the user's terms (system wording, speculation about ownership, offering to act anyway) | RESP-4, AUTH-1 | LLM judge | 5 / 3 | 5 (5%) |
| `search_query_misbuilt`: `search_products` with a query or filters that cannot match existing products (plural form, store name inside the query) | TOOL-3 | code check | 3 / 3 | 3 (3%) |

These are sample fractions, not prevalence: batch 2 balanced roles and batch 3 searched for the weak modes, so the sample over-represents them. 47 conversations have no mode, 42 one, 11 two. Boundaries against the nearest mode are recorded per mode in `patterns.json`; for example, `guesses_order_without_lookup` applies only when the agent never retrieved the specific order, while a lookup with a bad query is `search_query_misbuilt`.

Close negatives: the reviewed data contain only one for `inconsistent_record_not_flagged` (0221) and none for `required_escalation_missing`. The only correctly handled required escalation in all 250 conversations (0194) was not in the sample.

## Taxonomy revision

`permission_denial_overexplained` → `permission_denial_mishandled`. The mode started in batch 1 from two notes, 0017 ("too much technicality") and 0184 ("too much technical details, assumptions"), defined as explaining a denial in technical terms or speculating about the order. The batch 3 depth search for `permission_denied` results returned 0208, where the agent, after the denial, offers to cancel the order anyway if the user confirms it has not shipped. That is not overexplaining, but one fix (after a denial, give a short plain refusal and take no action on the order) corrects it together with 0017 and 0184, so I broadened and renamed the mode instead of opening a new one. The boundary came from a rejected suggestion: 0224 ("can't view orders that aren't linked to your account") explains the denial in the user's terms and passes.

Other revisions, each recorded in the mode's history:

- `wrong_policy_outcome` was broadened from "misapplied the policy it retrieved" (0079) to "answer rests on the wrong policy or misapplies the right one" after 0072 (a dispute answered with the return policy). It was renamed `wrong_policy_applied` after the AgentDebug comparison, because in 0072 the outcome was right and the policy was wrong.
- `refund_not_issued_after_eligible` was broadened to `eligible_action_not_taken` after 0026 (an eligible cancellation where the agent asks for a reason instead of calling `cancel_order`).
- Dropped: `reply_style` (0249, 0220, 0107, 0221). Its only requirement source was RESP-5, which is too vague for a binary decision.
- Merged: "misapplied policy" (0079) and "dispute treated as a refund" (0051) in axial pass 1; 0051 later moved to `required_escalation_missing`, where one fix (always escalate ESC-2/ESC-3 cases) also covers 0012 and 0203.

## SPEC revision

I added **RESP-6** to `SPEC.md` §6:

> When the user asks for a refund or a cancellation and `get_order` reports the order eligible (refund-eligible, or `placed` for a cancellation), call `issue_refund` for the order total or `cancel_order`. Do not ask for a reason or amount the user has not given; use a short generic reason. The tool decides between auto-approval and queueing (ESC-1).

Motivating annotations: `amwcg1hxk78nn` on support-0033 (the agent says "I'll proceed to issue the refund now" and makes no call), and `amwewzpg8cxov` on support-0026 (the cancellation extension). No existing requirement said an eligible action must be taken. Editing the specification does not change the running agent.

Relationship between a mode and `SPEC.md`: `required_escalation_missing` is ESC-2 and ESC-3 made checkable. ESC-3 says disputes always go to a human, and in 0051, 0066 and 0081 the agent instead offers a refund, sends the user to their bank, or only offers to escalate.

## Searches and the rejected suggestion

The coding agent proposed batch 2 and batch 3 readings as suggestions shown apart from my notes; 39 were accepted and 10 rejected. After broadening `wrong_policy_outcome`, I ran the search again (`analysis/review_app/search.py`, `wrong_policy`: the expected answer rests on a policy document and the agent fetched or cited only other policies) over the 85 conversations reviewed by then. It returned five hits; I accepted 0032, 0066, 0072 and 0081.

Rejected: 0035. The search flagged it because the agent cited `cw-restocking-fees` and not `store-cascade-audio-policy`. The scenario's requirement allows either, so the boundary is: **citing an alternative policy that the requirement explicitly allows is not `wrong_policy_applied`.** 0035 is now a close negative for the mode.

A recheck of earlier batches for modes found later added two cases I had coded before those modes existed: 0086 (bank-account change, not escalated) and 0107 (a return deadline computed from the shipped date because the delivery date is missing).

## Saturation: the final 15

**One** of the final 15 conversations produced a previously unseen consequential failure: 0078 answers an out-of-scope request (cover-letter tips) instead of declining (SCOPE-2). It is one case, and the other three out-of-scope conversations (0172, 0196, 0205) decline correctly, so it is recorded without a mode (`unseen_failures`) and is a candidate for HW5. The other new notes fit existing modes (0114, 0014, 0065, 0177), extended one (0026, see above), or were not agent failures (0046 scenario defect, 0226 tool defect). I judged the taxonomy reasonably stable and did not review another batch.

## Comparison with AgentDebug

Mapped to the AgentDebug taxonomy (Zhu et al., 2025): `eligible_action_not_taken` is a planning–action disconnect, `search_query_misbuilt` a parameter error, `required_escalation_missing` constraint ignorance, `policy_stated_without_lookup` hallucination or retrieval failure, `wrong_policy_applied` retrieval of the wrong document, and `inconsistent_record_not_flagged` / `permission_denial_mishandled` outcome misinterpretation. The comparison exposed one unclear name (`wrong_policy_outcome`, renamed). I considered causal misattribution (a false reason for a limitation: 0095 "privacy policies", 0080 blaming the bank) and did not add it, because only 0095 is not already covered by another mode. No system failures (step limits, tool errors) appeared.

## Not agent failures

- Scenario defects (excluded from modes, still labeled): 0015, 0122, 0143 (a writer brief whose "sorted today" follow-up is ambiguous), 0042, 0094, 0046.
- Tool defect: 0226. `search_products` also matches descriptions, so "atlas" returned Atlas Stationery items, and `limit=5` by price hid the two real atlases. The fix belongs in the tool's ranking.

## Labels

All 800 conversation × mode judgments are in `analysis/state/labels/<mode>.jsonl` and as Langfuse scores on the conversation id (verified: 800 present, 0 mismatches).

- **659 are mine:** 655 confirmed in bulk where my records already answered the cell (54 coded positives, 21 close negatives, 580 where the mode's situation cannot occur), plus 4 individual judgments.
- **141 were judged by the coding agent at my request**, with evidence on each row (`source: claude_on_request`); 8 of these are Fail.

## Gaps for Homework 5

Every mode has at least 87 Pass labels and **fewer than 30 Fail labels**, so all eight need targeted synthetic scenarios before a judge can be split and validated:

| Mode | Fail | Fail still needed |
|---|---|---|
| `guesses_order_without_lookup` | 13 | 17 |
| `policy_stated_without_lookup` | 11 | 19 |
| `inconsistent_record_not_flagged` | 9 | 21 |
| `required_escalation_missing` | 9 | 21 |
| `eligible_action_not_taken` | 8 | 22 |
| `wrong_policy_applied` | 6 | 24 |
| `permission_denial_mishandled` | 5 | 25 |
| `search_query_misbuilt` | 3 | 27 |
