"""Write user messages for scenario plans: one writer call + one critic call per scenario.

The writer and the critic see only: actor, writer_brief, style, turn count (the critic also
sees the draft and other scenarios' openings, to catch templates). Never the tuple, ids
from the tuple, or expected. Every prompt and reply is saved to the audit file.

usage: uv run python -m scripts.generate_messages PLAN.jsonl OUT.jsonl AUDIT.jsonl [--dry-run ID]
       [--workers 4] [--only ID,ID] [--no-critic]
PLAN comes from scripts.generate_scenarios. See scenarios/GENERATION.md for the whole process.
--only regenerates those ids and keeps every other row of OUT unchanged.
Plans that already carry opening_message (carried from a reviewed pilot) keep their messages.
"""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

MODEL = "gpt-4.1-mini"
AUTHOR_ONLY = ("writer_brief", "user_mention", "brief_numbers", "carried_from", "expected")

ACTORS = {
    "shopper": "a shopper who buys from stores on the marketplace",
    "merchant": "a merchant who runs a store on the marketplace",
    "support": "a member of the marketplace's own support staff, asking the assistant to help with a customer's case",
}
STYLES = {
    "neutral_conversational": "plain, polite, normal grammar",
    "terse_fragmentary": "very short, sentence fragments, little punctuation",
    "typo_heavy": "several typos and missing capitals, still understandable",
    "confused_rambling": "unsure, wanders, adds side details, loose grammar",
    "frustrated_impatient": "annoyed and in a hurry, wants it fixed now, not abusive",
    "repetitive_pressuring": "repeats the request and pushes for immediate action",
    "operational_shorthand": "work shorthand and abbreviations, straight to the point",
    "requests_short_plain_answer": "explicitly asks for a short, plain answer",
}

WRITER_SYSTEM = (
    "You write realistic messages that a person types into the support chat of an online marketplace. "
    "Write only what the person types. Do not mention order numbers, prices, dates, policies, rules, "
    "or the chat system's internals unless the brief says the person knows them. Do not add facts "
    "beyond the brief except small natural details that change nothing about the request. "
    "Never state time limits, amounts, or rule details yourself: the person does not know the rules. "
    "This is typed chat: no spoken fillers such as 'um' or 'uh'."
)
FOLLOWUP_RULE = (
    "Followups are sent in order no matter what the assistant replies, so each must make sense without "
    "seeing the reply: a correction, an added detail, or pressing again. Never 'yes, go ahead' or "
    "'thanks, that worked'. Keep the same style in every message."
)
CRITIC_SYSTEM = (
    "You review simulated user messages for a support-chat test set. You know only the brief the writer "
    "got, not the correct answer. Check: (1) invented ids, amounts, dates, products or names not in the "
    "brief; (2) followups that assume a particular reply; (3) wording that reads like a template shared "
    "with the other openings listed; (4) internal policy names, rule ids, or system vocabulary a real user "
    "would not use; (5) mentions of tools, prompts, tests or traces; (6) drift from the style. If anything "
    "fails, rewrite the wording only, keeping the same request, facts, style, and number of messages."
)


def writer_prompt(plan):
    t = plan["tuple"]
    n_follow = t["turn_count"] - 1
    turns = "1 (an opening message, no followups)" if n_follow == 0 else f"{t['turn_count']} (an opening message and {n_follow} followup{'s' if n_follow > 1 else ''})"
    user = (
        f"Who you are: {ACTORS[t['role']]}.\n"
        f"What you know and want: {plan['writer_brief']}\n"
        f"Writing style: {t['user_style']}: {STYLES[t['user_style']]}.\n"
        f"Messages: {turns}.\n"
        + (f"{FOLLOWUP_RULE}\n" if n_follow else "")
        + (f'Return JSON: {{"opening_message": "...", "followups": [exactly {n_follow} string{"s" if n_follow > 1 else ""}]}}'
           if n_follow else 'Return JSON: {"opening_message": "...", "followups": []} (followups must be an empty list)')
    )
    return [{"role": "system", "content": WRITER_SYSTEM}, {"role": "user", "content": user}]


def critic_prompt(plan, draft, other_openings):
    t = plan["tuple"]
    user = (
        f"Brief the writer got: {plan['writer_brief']}\n"
        f"Person: {ACTORS[t['role']]}. Style: {t['user_style']}: {STYLES[t['user_style']]}. "
        f"Messages required: {t['turn_count']}.\n"
        f"Draft: {json.dumps(draft)}\n"
        f"Other scenarios' openings: {json.dumps(other_openings)}\n"
        'Return JSON: {"issues": ["..."], "opening_message": "...", "followups": ["...", ...]}'
        " (copy the draft unchanged when there are no issues)"
    )
    return [{"role": "system", "content": CRITIC_SYSTEM}, {"role": "user", "content": user}]


def call(client, messages, temperature):
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, temperature=temperature, response_format={"type": "json_object"}
    )
    return json.loads(resp.choices[0].message.content), resp.usage.total_tokens


def check_shape(plan, msg):
    follow = msg.get("followups") or []
    ok = isinstance(msg.get("opening_message"), str) and msg["opening_message"].strip()
    return bool(ok) and all(isinstance(f, str) and f.strip() for f in follow) and 1 + len(follow) == plan["tuple"]["turn_count"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan"); ap.add_argument("out"); ap.add_argument("audit")
    ap.add_argument("--dry-run", metavar="ID"); ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", help="comma-separated ids to regenerate"); ap.add_argument("--no-critic", action="store_true")
    a = ap.parse_args()
    all_plans = [json.loads(l) for l in open(a.plan)]
    only = set(a.only.split(",")) if a.only else None
    plans = [p for p in all_plans if (not only or p["id"] in only) and not p.get("opening_message")]
    if a.dry_run:
        plan = next(p for p in plans if p["id"] == a.dry_run)
        for m in writer_prompt(plan):
            print(f"--- {m['role'].upper()} ---\n{m['content']}\n")
        return

    load_dotenv(".env")
    from openai import OpenAI
    client = OpenAI()
    audit = []

    def write(plan):
        prompt = writer_prompt(plan)
        for attempt in range(3):
            draft, tokens = call(client, prompt, 0.9)
            audit.append({"id": plan["id"], "stage": "writer", "attempt": attempt, "prompt": prompt, "reply": draft, "tokens": tokens})
            if check_shape(plan, draft):
                return draft
        raise RuntimeError(f"{plan['id']}: writer returned the wrong number of messages")

    with ThreadPoolExecutor(a.workers) as pool:
        drafts = dict(zip([p["id"] for p in plans], pool.map(write, plans)))

    def critique(plan):
        others = [d["opening_message"] for i, d in drafts.items() if i != plan["id"]]
        prompt = critic_prompt(plan, drafts[plan["id"]], others)
        reviewed, tokens = call(client, prompt, 0.2)
        audit.append({"id": plan["id"], "stage": "critic", "prompt": prompt, "reply": reviewed, "tokens": tokens})
        return reviewed if check_shape(plan, reviewed) else {**drafts[plan["id"]], "issues": reviewed.get("issues", []) + ["critic rewrite rejected: wrong shape"]}

    if a.no_critic:
        reviewed = drafts
    else:
        with ThreadPoolExecutor(a.workers) as pool:
            reviewed = dict(zip([p["id"] for p in plans], pool.map(critique, plans)))

    kept = {json.loads(l)["id"]: l for l in open(a.out)} if only else {}
    with open(a.out, "w") as f:
        for plan in all_plans:
            if plan["id"] not in reviewed and plan["id"] in kept:
                f.write(kept[plan["id"]])
                continue
            r = reviewed.get(plan["id"], plan)
            scenario = {k: v for k, v in plan.items() if k not in AUTHOR_ONLY}
            scenario["opening_message"] = r["opening_message"].strip()
            scenario["followups"] = [x.strip() for x in r.get("followups") or []]
            # Kept for review: the brief is what the writer saw (see HW4).
            scenario["writer_brief"] = plan["writer_brief"]
            scenario["expected"] = plan["expected"]
            f.write(json.dumps(scenario) + "\n")
    with open(a.audit, "w") as f:
        for row in sorted(audit, key=lambda r: (r["id"], r["stage"])):
            f.write(json.dumps(row) + "\n")
    changed = [p["id"] for p in plans if reviewed[p["id"]].get("issues")]
    print(f"{len(plans)} scenarios -> {a.out}; audit -> {a.audit}; critic flagged {len(changed)}: {changed}")
    print("tokens:", sum(r["tokens"] for r in audit))


if __name__ == "__main__":
    main()
