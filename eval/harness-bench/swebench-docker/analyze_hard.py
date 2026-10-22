"""Summarize run_hard.py rows: resolve rate, paired comparisons, tokens, estimated cost, tool use.

Usage: python3 analyze_hard.py <rows.jsonl> [--price-miss 0.4427 --price-hit 0.0369 --price-out 0.8853]
Prices are USD per 1 M tokens (OpenRouter list prices for deepseek/deepseek-v4-pro on 2026-10-22).
Only instances where every arm has a graded or empty-patch row (resolved is not None) are compared.
"""

import json
import math
import sys
from collections import defaultdict
from statistics import mean, median


def exact_mcnemar(b, c):
    """Two-sided exact McNemar p-value for b and c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def main():
    path = sys.argv[1]
    args = sys.argv[2:]
    price = {"miss": 0.4427, "hit": 0.0369, "out": 0.8853}
    for i in range(0, len(args), 2):
        price[args[i].removeprefix("--price-")] = float(args[i + 1])

    rows = {}
    for line in open(path):
        r = json.loads(line)
        rows[(r["instance"], r["arm"], str(r["seed"]))] = r  # later rows (resume) replace earlier ones
    arms = sorted({a for _, a, _ in rows}, key="RPH".index)
    seeds = sorted({s for _, _, s in rows})
    insts = sorted({i for i, _, _ in rows})
    valid = {k: r for k, r in rows.items() if r.get("resolved") is not None}
    complete = [(i, s) for i in insts for s in seeds if all((i, a, s) in valid for a in arms)]
    failed = [r for r in rows.values() if r.get("resolved") is None]

    def cost(r):
        return (r.get("inputTokens", 0) * price["miss"] + r.get("cacheReadTokens", 0) * price["hit"]
                + r.get("outputTokens", 0) * price["out"]) / 1e6

    print(f"rows {len(rows)}, valid {len(valid)}, without a result {len(failed)}, complete paired (instance, seed) {len(complete)}")
    for r in failed:
        print(f"  no result: {r['instance']} {r['arm']} s{r['seed']}: {(r.get('api_error') or r.get('error') or '')[:120]}")
    print()
    print("arm  resolved      mean steps  median steps  mean input+cache  mean output  est. cost/run  cost/resolved  empty patch")
    for a in arms:
        rs = [valid[(i, a, s)] for i, s in complete]
        if not rs:
            continue
        res = sum(r["resolved"] for r in rs)
        c = [cost(r) for r in rs]
        print(f"{a}    {res:>3}/{len(rs):<3} {100 * res / len(rs):5.1f}%  {mean(r['steps'] for r in rs):10.1f}  {median(r['steps'] for r in rs):12.1f}"
              f"  {mean(r['inputTokens'] + r['cacheReadTokens'] for r in rs):16,.0f}  {mean(r['outputTokens'] for r in rs):11,.0f}"
              f"  ${mean(c):12.3f}  ${(sum(c) / res if res else float('nan')):12.3f}  {sum(bool(r.get('empty_patch')) for r in rs):11}")
    print()
    for a in arms[1:]:
        b = sum(1 for i, s in complete if valid[(i, a, s)]["resolved"] and not valid[(i, "R", s)]["resolved"])
        c = sum(1 for i, s in complete if not valid[(i, a, s)]["resolved"] and valid[(i, "R", s)]["resolved"])
        more = sum(1 for i, s in complete if valid[(i, a, s)]["steps"] > valid[(i, "R", s)]["steps"])
        print(f"{a} vs R: {a} only {b}, R only {c}, exact McNemar p = {exact_mcnemar(b, c):.3f}; {a} took more steps on {more}/{len(complete)}")
    print()
    print("tool use (calls per run, mean) and plugin signals (runs with at least one / total count)")
    for a in arms:
        rs = [valid[(i, a, s)] for i, s in complete]
        if not rs:
            continue
        tools = defaultdict(int)
        for r in rs:
            for t, n in (r.get("tools") or {}).items():
                tools[t] += n
        top = ", ".join(f"{t} {n / len(rs):.1f}" for t, n in sorted(tools.items(), key=lambda x: -x[1])[:9])
        sig = ", ".join(f"{k} {sum(1 for r in rs if (r.get(k) or 0) > 0)}/{sum(r.get(k) or 0 for r in rs)}"
                        for k in ("budget_notices", "routed", "syntax_denials"))
        print(f"{a}: {top}\n   {sig}")
    by_repo = defaultdict(lambda: defaultdict(list))
    for i, s in complete:
        for a in arms:
            by_repo[valid[(i, a, s)]["repo"]][a].append(valid[(i, a, s)]["resolved"])
    print()
    print("resolved by repository: " + "; ".join(f"{repo.split('/')[1]} " + " ".join(f"{a} {sum(v)}/{len(v)}" for a, v in d.items())
                                             for repo, d in sorted(by_repo.items())))


if __name__ == "__main__":
    main()
