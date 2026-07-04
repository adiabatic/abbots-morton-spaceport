"""Clean-vs-candidate differ for the 2026-07-03 regrouping-floor-drift verdict batch.

Targets are the new reject/neither windows (desired seams encoded explicitly, settled under each window's own config). Guards are (a) the new approve windows on the bd1 surface, settled under their own config, and (b) every latest-verdict window from the old export, resolved on the OLD served surface by codepoints and settled under the default config. Any guard whose settle moves clean->candidate is reported with its verdict for context.

Usage:
    uv run python rebuild/tools/regroup_diff.py <candidate_runes_dir>
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
OLD_VERDICTS = REPO / "verdicts-11.33.00PM.json"
NEW_VERDICTS = REPO / "verdicts-12.04.13AM.json"
OLD_UNITS = str(REPO / "rebuild" / "out" / "review" / "units" / "*.json")
NEW_UNITS = str(REPO / "tmp" / "review-preview-bd1" / "units" / "*.json")

TARGETS = {
    "u-1280": ["y0", "y0"],
    "u-1281": ["y0", "y0"],
    "u-1282": ["y0", "y0"],
    "u-1283": ["break", "y0", "y0"],
    "u-1284": ["break", "y0", "y0"],
    "u-1318": ["y5", "break", "y0"],
    "u-1319": ["y5", "break", "y0"],
    "u-1320": ["y5", "break", "y0"],
}


def load_units(pattern):
    us = {}
    for f in glob.glob(pattern):
        for u in json.load(open(f)):
            us[u["id"]] = u
    return us


def latest(path):
    by = {}
    for v in json.loads(Path(path).read_text())["verdicts"]:
        if v["unit"] not in by or v["at"] > by[v["unit"]]["at"]:
            by[v["unit"]] = v
    return by


def cps(u):
    return [int(h, 16) for h in u["codepoints"].split(":")]


def features_of(u):
    cfgs = set(u.get("config_classes") or {})
    if not cfgs or "default" in cfgs:
        return frozenset()
    cfg = sorted(cfgs, key=len)[0]
    return frozenset(cfg.split("+"))


def seams(spec, c, feats):
    return ["break" if s.seam is None else f"y{spec.registry.y_of(s.seam)}" for s in settle(spec, c, feats)[:-1]]


def main():
    cand_dir = Path(sys.argv[1])
    clean = load_spec(REAL_RUNES, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)
    cand = load_spec(cand_dir, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)

    new_units = load_units(NEW_UNITS)
    new_v = latest(NEW_VERDICTS)
    old_units = load_units(OLD_UNITS)
    old_v = latest(OLD_VERDICTS)

    print("=== TARGETS (desired seams; settled under the window's own config) ===")
    hit = 0
    for uid, want in TARGETS.items():
        u = new_units[uid]
        feats = features_of(u)
        cl = seams(clean, cps(u), feats)
        ca = seams(cand, cps(u), feats)
        ok = ca == want
        hit += ok
        mark = "OK  " if ok else "MISS"
        print(f"  {mark} {uid} {u['notation']:22s} clean={cl} cand={ca} want={want}")
    print(f"  restored {hit}/{len(TARGETS)}")

    print("\n=== NEW-SURFACE GUARDS (this batch's approves) ===")
    regs = 0
    for uid, v in sorted(new_v.items()):
        if uid in TARGETS or uid not in new_units or v["verdict"] != "approve":
            continue
        u = new_units[uid]
        if u.get("class") != "regrouping-floor-drift":
            continue
        feats = features_of(u)
        cl = seams(clean, cps(u), feats)
        ca = seams(cand, cps(u), feats)
        if cl != ca:
            regs += 1
            print(f"  MOVED {uid} {u['notation']:22s} clean={cl} cand={ca}")
    print(f"  moved approves: {regs}")

    print("\n=== OLD-SURFACE GUARDS (all prior latest verdicts, default config) ===")
    moved = {}
    for uid, v in old_v.items():
        u = old_units.get(uid)
        if not u:
            continue
        c = cps(u)
        cl = seams(clean, c, frozenset())
        ca = seams(cand, c, frozenset())
        if cl != ca:
            moved.setdefault(v["verdict"], []).append((u["notation"], cl, ca))
    for verdict, rows in sorted(moved.items()):
        print(f"  {verdict}: {len(rows)} moved")
        for n, cl, ca in rows[:40]:
            print(f"    {n:30s} clean={cl} cand={ca}")
    if not moved:
        print("  none moved")


if __name__ == "__main__":
    main()
