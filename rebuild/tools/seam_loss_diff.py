"""Independent clean-vs-candidate seam differ for the seam-loss-withdrawal triage.

Re-settles EVERY window of interest under BOTH the real runes (clean) and a candidate runes_dir, under the default config, and reports only windows whose seams actually MOVE between the two specs. This sidesteps the stale-shard problem (the frozen review unit `after` seams can be config-shadowed, producing phantom diffs); the only honest collateral signal is clean-settle != candidate-settle.

Window universe:
  - All seam-loss-withdrawal REJECT windows (the targets, tagged by group A/B/C/D), each with its OLD seam from the shard's `before`.
  - All APPROVED windows in the guarded families (the collateral pool).

Usage:
    uv run python rebuild/tools/seam_loss_diff.py <candidate_runes_dir>
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from rebuild.pipeline.settle import settle
from rebuild.pipeline.spec_load import DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR, load_spec

REPO = Path(__file__).resolve().parents[2]
REAL_RUNES = REPO / "glyph_data" / "runes"
VERDICTS = REPO / "verdicts-11.33.00PM.json"
UNITS_GLOB = str(REPO / "rebuild" / "out" / "review" / "units" / "*.json")

GUARD = {
    "no-chain-gains", "may-utter-gains", "seam-loss-withdrawal", "tea-it-xheight",
    "oy-it-baseline", "entered-it-baseline-join-gain", "ss03-chain-join-gains",
    "pea-chain-regularized", "unmatched-misc",
}


def lead(t): return t.split("/", 1)[0].split("_", 1)[0]
def trail(t): return t.split("/", 1)[0].rsplit("_", 1)[-1]


def primary_gap(u):
    b, a, c = u["before"]["seams"], u["after"]["seams"], u["after"]["cells"]
    if len(b) != len(a) or len(a) + 1 != len(c):
        return None
    for i, (x, y) in enumerate(zip(b, a)):
        if x != y:
            return i
    return None


def group_of(u):
    g = primary_gap(u)
    if g is None:
        return None
    c = u["after"]["cells"]
    l, r = trail(c[g]), lead(c[g + 1])
    if l == "qsUtter" and r == "qsNo":
        return "A"
    if l == "qsIt" and r == "qsUtter":
        return "B"
    if r == "qsNo":
        return "C"
    return "D"


def latest_verdicts():
    raw = json.loads(VERDICTS.read_text())["verdicts"]
    by = {}
    for v in raw:
        if v["unit"] not in by or v["at"] > by[v["unit"]]["at"]:
            by[v["unit"]] = v
    return {k: v["verdict"] for k, v in by.items()}


def all_units():
    u = {}
    for f in glob.glob(UNITS_GLOB):
        for x in json.load(open(f)):
            u[x["id"]] = x
    return u


def cps(u):
    return [int(h, 16) for h in u["codepoints"].split(":")]


def seams(spec, c):
    return tuple("break" if s.seam is None else f"y{spec.registry.y_of(s.seam)}" for s in settle(spec, c, frozenset())[:-1])


def main():
    cand_dir = Path(sys.argv[1])
    clean = load_spec(REAL_RUNES, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)
    cand = load_spec(cand_dir, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)

    units = all_units()
    verdicts = latest_verdicts()

    targets, collateral = [], []
    for uid, u in units.items():
        cls, vd = u["class"], verdicts.get(uid)
        if cls == "seam-loss-withdrawal" and vd == "reject":
            targets.append(u)
        elif cls in GUARD and vd == "approve":
            collateral.append(u)

    print(f"targets={len(targets)} collateral-approved={len(collateral)}  (default config, clean vs candidate)")

    bygroup = {"A": [0, 0, []], "B": [0, 0, []], "C": [0, 0, []], "D": [0, 0, []]}
    for u in targets:
        g = group_of(u)
        gap = primary_gap(u)
        old = u["before"]["seams"]
        cl = seams(clean, cps(u))
        ca = seams(cand, cps(u))
        bygroup[g][1] += 1
        if len(ca) == len(old) and ca[gap] == old[gap] and ca != cl:
            bygroup[g][0] += 1
        elif ca != cl:
            bygroup[g][2].append((u["notation"], f"gap{gap} old={old[gap]} clean={cl[gap]} cand={ca[gap]}"))
    print("\n=== TARGET RESTORATION (moved to OLD at primary gap) ===")
    for g in "ABCD":
        r, tot, moved_other = bygroup[g]
        print(f"  group {g}: restored {r}/{tot}" + (f"  | moved-but-not-to-old: {len(moved_other)}" if moved_other else ""))
        for n, d in moved_other:
            print(f"      {n:30s} {d}")

    print("\n=== COLLATERAL (approved windows that MOVED clean->cand = real regressions) ===")
    regs = []
    for u in collateral:
        cl = seams(clean, cps(u))
        ca = seams(cand, cps(u))
        if cl != ca:
            regs.append((u, cl, ca))
    print(f"  real regressions: {len(regs)}")
    for u, cl, ca in regs[:80]:
        print(f"    {u['id']:9s} {u['class']:26s} {u['notation']:30s} clean={list(cl)} cand={list(ca)}")


if __name__ == "__main__":
    main()
