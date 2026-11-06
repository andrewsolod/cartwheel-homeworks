"""Pick 50 monitoring scenarios: seeded random draw, stratified by group x role in proportion
to the set; if an intent is missing, swap in a same-group, same-role scenario that has it.
usage: uv run python -m scripts.select_monitoring SCENARIOS OUT [--n 50] [--seed 20261104]"""
import argparse, json, random
from collections import Counter

ap = argparse.ArgumentParser(); ap.add_argument("scenarios"); ap.add_argument("out")
ap.add_argument("--n", type=int, default=50); ap.add_argument("--seed", type=int, default=20261104)
a = ap.parse_args()
lines = open(a.scenarios).read().splitlines()
rows = [json.loads(l) for l in lines]
rng = random.Random(a.seed)
key = lambda s: (s["scenario_group"], s["tuple"]["role"])
strata = Counter(key(s) for s in rows)
# largest-remainder allocation
quota = {k: a.n * c // len(rows) for k, c in strata.items()}
for k in sorted(strata, key=lambda k: -(a.n * strata[k] / len(rows) - quota[k]))[: a.n - sum(quota.values())]:
    quota[k] += 1
picked = []
for k in sorted(strata):
    pool = [i for i, s in enumerate(rows) if key(s) == k]
    picked += rng.sample(pool, quota[k])
intents = {s["tuple"]["intent"] for s in rows}
for intent in sorted(intents - {rows[i]["tuple"]["intent"] for i in picked}):
    have = Counter(rows[i]["tuple"]["intent"] for i in picked)
    # replace a scenario of the most common intent within a stratum that has the missing intent
    options = [(j, i) for i in picked for j, s in enumerate(rows)
               if j not in picked and s["tuple"]["intent"] == intent and key(s) == key(rows[i])
               and have[rows[i]["tuple"]["intent"]] > 1]
    options.sort(key=lambda p: -have[rows[p[1]]["tuple"]["intent"]])
    best = [p for p in options if have[rows[p[1]]["tuple"]["intent"]] == have[rows[options[0][1]]["tuple"]["intent"]]]
    j, i = rng.choice(best)
    print(f"swap for intent {intent}: {rows[i]['id']} ({rows[i]['tuple']['intent']}) -> {rows[j]['id']}")
    picked[picked.index(i)] = j
picked.sort()
with open(a.out, "w") as f:
    for i in picked:
        f.write(lines[i] + "\n")
print(f"{len(picked)} -> {a.out}; quota {dict(sorted(quota.items()))}")
