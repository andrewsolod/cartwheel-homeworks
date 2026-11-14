# How the HW3 scenarios were generated

This file describes how `pilot_scenarios.jsonl`, `support_scenarios.jsonl` and `monitoring_scenarios.jsonl` were produced, which code produced them, and which steps a person reviewed. It follows the method in [skill/SKILL.md](skill/SKILL.md). Run all commands from the repository root.

## Overview

```
dimensions.yaml ──► scripts.generate_scenarios ──► plans (no messages)
  (approved)          seed DB, no LLM               tuple + record + expected + writer_brief
                                                            │
                                                            ▼
                      scripts.generate_messages ──► scenarios with messages
                      LLM writer (gpt-4.1-mini)     opening_message + followups
                                                            │
                      checks + human review ◄───────────────┘
                                                            │
                      scripts.select_monitoring ──► monitoring_scenarios.jsonl (50)
```

Three models are involved, and each one sees different information:

| Role | Model | Sees |
|---|---|---|
| Author | deterministic code (`scripts.generate_scenarios`) | the database, the policies, the dimension plan; writes `expected` |
| Message writer | `gpt-4.1-mini` | the actor, the `writer_brief`, the style, the turn count. It never sees the tuple, ids from the tuple, or `expected` |
| System under test | Cartwheel on `gpt-4o-mini` | only the messages, sent by `scenarios.runner` |

The writer is a different model from the agent, and it never sees `expected`, so the messages can't hint at the correct answer.

## Step 1: dimensions

[dimensions.yaml](dimensions.yaml) is the approved dimension plan: role, intent, access, record state, applicable policy, tools needed, difficulty, user style and turn count, with a reason for each value. Each dimension changes the expected behavior, the tool path or a requirement. Values that change none of these were rejected, for example product category (`search_products` has no category argument, and policy is set per store) and prompt version (agent configuration, which is held fixed in a run).

## Step 2: plans (no LLM)

**Pilot.** The 30 pilot plans were written by hand against the seed database, as recommended for a first set, and reviewed before any messages were written. Code: [scripts/pilot_plan.py](../scripts/pilot_plan.py), output [pilot_plan.jsonl](pilot_plan.jsonl).

```bash
uv run python -m scripts.pilot_plan scenarios/pilot_plan.jsonl
```

**Final set.** Code: [scripts/generate_scenarios.py](../scripts/generate_scenarios.py)

```bash
uv run python -m scripts.generate_scenarios PLAN.jsonl \
  --pilot-plan scenarios/pilot_plan.jsonl --db FRESH_DIR/cartwheel.db [--seed 20261104]
```

For each slot in the quotas (`COVERAGE_NEW` and the challenge builders) the script:

1. **Finds a record that already has the state the tuple needs.** It takes the record from a freshly seeded database with a fixed random seed and never uses a record twice, so one scenario's refund or cancellation can't change the record another scenario depends on.
2. **Checks access.** The user in `tuple.user_id` must be allowed (or deliberately not allowed) to see the record under the SPEC AUTH-1 rules.
3. **Computes `expected`** from the record and the policy rules (`seed.eligibility`, `facts.yaml` numbers), citing its source. Most expectations are `objective`; a few are `human_judgment`.
4. **Writes a `writer_brief`**: who the user is talking to, what they know and what they ask for. The brief mentions only what that user would know. If the user names a record by its description, the description must match exactly one record they can see, otherwise `expected` is "ask for clarification".

Pilot scenarios are carried over with new ids and their reviewed messages. The output plan has 250 rows: 30 carried from the pilot and 220 new, 175 coverage and 75 challenge, with 5 challenge scenarios for each of the 6 data quality cases.

The script is deterministic: the same seed and the same database give the same plan byte for byte. It must run against a **freshly seeded** database, because the extra metadata assumes the seeded state. Make a separate copy instead of resetting the dev database:

```bash
uv run python -m seed.generate --db FRESH_DIR/cartwheel.db --policies FRESH_DIR/policies
```

Check the plan before writing messages:

```bash
uv run python -m scripts.check_scenarios PLAN.jsonl --db FRESH_DIR/cartwheel.db   # content against the DB
uv run python -m scripts.check_dimensions PLAN.jsonl --strict                    # values, repetition
```

## Step 3: messages (LLM)

Code: [scripts/generate_messages.py](../scripts/generate_messages.py). The key is read from `.env` (`OPENAI_API_KEY`).

```bash
# preview the exact prompt for one plan, no API call
uv run python -m scripts.generate_messages PLAN.jsonl /dev/null /dev/null --dry-run support-0049

# write messages for every plan that has none yet
uv run python -m scripts.generate_messages PLAN.jsonl OUT.jsonl AUDIT.jsonl --no-critic

# rewrite only some ids, keep every other row of OUT unchanged
uv run python -m scripts.generate_messages PLAN.jsonl OUT.jsonl AUDIT.jsonl --only support-0137,support-0141 --no-critic
```

How it works:

- **One call per scenario**, 4 workers, temperature 0.9, JSON output. The call is retried up to 3 times if the number of messages doesn't match the turn count.
- **Writer rules** (`WRITER_SYSTEM`): write only what the person types; no ids, prices, dates or policy details unless the brief says the person knows them; never state time limits or amounts; no spoken fillers.
- **Followup rule** (`FOLLOWUP_RULE`): followups are sent in order whatever the agent replies, so each one must make sense without seeing the reply (a correction, an added detail, or pressing again). Never "yes, go ahead".
- **Critic** (`CRITIC_SYSTEM`, temperature 0.2): an optional second call that checks for invented facts, templated wording and style drift, and rewrites the wording. It was used for the pilot and turned off with `--no-critic` for the final set, because in the pilot it removed typos from a typo-heavy message and reported problems that weren't real.
- **Output:** the author-only fields (`user_mention`, `brief_numbers`, `carried_from`) are dropped, `opening_message` and `followups` are added, and `writer_brief` and `expected` are copied from the plan. Plans that already have an `opening_message` are skipped, so carried pilot scenarios keep their reviewed text.
- **`writer_brief` in the scenario files (added in HW4):** the HW3 run dropped the brief. For the HW4 review it was merged back into `support_scenarios.jsonl` and `monitoring_scenarios.jsonl` from a plan regenerated with the same seed against a fresh seed database. Before merging, every row's `tuple`, `expected`, `scenario_group` and `data_quality_case_id` were checked against the regenerated plan (all 250 equal), and every other byte of each line was kept. The runner never sends the brief to the agent. For support-0051 the brief is the original one; its opening message was rewritten by hand during the HW3 review.
- **Audit:** every prompt and reply goes to the audit file, so you can show what the writer saw.

The writer is not deterministic. Running it again gives different wording, so the committed `support_scenarios.jsonl` is the record of the messages, not a regenerated file.

## Step 4: checks and fixes

After generation the output was scanned for invented facts, rule hints, fillers and role confusion. Problems were fixed **in the brief templates** in `generate_scenarios.py`, not by editing messages by hand:

- The merchant cancel brief had no request ("…and you ask the agent to cancel it" was added).
- Merchant and support refund briefs said "You ordered it" ("The customer ordered" is used for them now).
- The missing-info merchant brief and the merchant brief for data quality case dq-8003 didn't say what was asked.
- Support-0137 had a filler "uh" and an invented age.

The plan was regenerated (only those 9 briefs changed) and the affected 10 messages were rewritten with `--only`. Then:

```bash
uv run python -m scripts.check_scenarios scenarios/support_scenarios.jsonl --db FRESH_DIR/cartwheel.db
uv run python -m scenarios.validate scenarios/support_scenarios.jsonl --final
uv run python -m scripts.check_dimensions scenarios/support_scenarios.jsonl --strict
```

| Check | What it checks |
|---|---|
| `scenarios.validate` | the file format and the scenario contract (fields, ids, 175/75 split with `--final`) |
| `scripts.check_scenarios` | plan content against the database: the record exists, the user can see it, the description is unique, `expected` matches the record |
| `scripts.check_dimensions` | tuple values against `dimensions.yaml`, repeated tuples and records, similar openings |

## Step 5: human review

A sample of 15 scenarios was reviewed in the browser. The sample covered both groups, all 3 roles, all 8 intents, the longest conversation and 2 similar-opening pairs.

```bash
uv run python -m scripts.scenarios_renderer scenarios/support_scenarios.jsonl --scenario-review \
  --ids support-0049,support-0222,support-0244,support-0238,support-0245,support-0212,support-0156,support-0116,support-0025,support-0030,support-0051,support-0058,support-0043,support-0203,support-0031 \
  --db FRESH_DIR/cartwheel.db --output support-review.html
```

The decisions are in [support_review.jsonl](support_review.jsonl): 14 accepted, 1 revised. For support-0051 the reviewer's change text replaced the opening message directly. The writer was not run again for it, so don't regenerate 0051.

## Step 6: monitoring set

Code: [scripts/select_monitoring.py](../scripts/select_monitoring.py)

```bash
uv run python -m scripts.select_monitoring scenarios/support_scenarios.jsonl scenarios/monitoring_scenarios.jsonl
```

This is a seeded random draw (seed 20261104) of 50 scenarios, stratified by group and role in proportion to the full set (largest-remainder quotas: coverage 18 shopper / 9 merchant / 8 support, challenge 7 / 5 / 3). If an intent is missing, a scenario from the same group and role is swapped in; none were needed. The rows are copied byte for byte from `support_scenarios.jsonl`. The draw has no `others_record` access scenario and no multi-turn correction scenario; it was kept as drawn. Running the command again reproduces the committed file.

## After generation

The scenarios are run with `scenarios.runner` on `gpt-4o-mini` against a freshly reset database (`uv run python -m seed.generate`), with Langfuse healthy first. Results go to `final-results.jsonl`, and traces are exported with `scenarios.export_langfuse`. That part is described in the HW3 handout, [homework/module-1/hw3.md](../homework/module-1/hw3.md).

## What is not in the repository

- **The audit files** (every writer and critic prompt and reply). They are large and generated, so they are kept outside git.
- **The fresh seed copy.** Recreate it with the `seed.generate --db … --policies …` command above.
