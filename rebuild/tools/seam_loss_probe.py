"""Seam-probe harness for the seam-loss-withdrawal verdict-application pass.

Settles any window under an arbitrary runes_dir (a scratch copy of glyph_data/runes with a candidate edit) and reports, per window, the OLD-font seams (from the review unit shard's `before`), the CURRENT new-font seams (the shard's `after`), and the CANDIDATE seams (re-settled under the edited spec). This is the fast, parallel-safe arbiter for the triage: it isolates a candidate edit's per-window effect with no font compile.

Two window pools:
  - targets: the seam-loss-withdrawal REJECT windows for a group (A/B/C/D). A good edit RESTORES these to the old seam.
  - collateral: the APPROVED windows of the families an edit risks disturbing (no-chain-gains, may-utter-gains, the seam-loss approves, …). A good edit leaves these UNCHANGED from current.

Usage:
    uv run python rebuild/tools/seam_loss_probe.py <runes_dir> --group A|B|C|D [--collateral fam1,fam2,...]

Seams are probed under the DEFAULT config (frozenset()) — the config the round-3 families adjudicate on. The full-rebuild recipe (VERDICT-APPLICATION-PROGRESS.md) remains the commit-time arbiter across all configs.
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from rebuild.pipeline.settle import settle
from rebuild.pipeline.spec_load import (
    DEFAULT_REGISTRY_PATH,
    DEFAULT_SCHEMA_DIR,
    load_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VERDICTS = REPO_ROOT / "verdicts-11.33.00PM.json"
UNITS_GLOB = str(REPO_ROOT / "rebuild" / "out" / "review" / "units" / "*.json")


def _lead(tok: str) -> str:
    return tok.split("/", 1)[0].split("_", 1)[0]


def _trail(tok: str) -> str:
    return tok.split("/", 1)[0].rsplit("_", 1)[-1]


def _primary_gap(unit) -> int | None:
    before = unit["before"]["seams"]
    after = unit["after"]["seams"]
    cells = unit["after"]["cells"]
    if len(before) != len(after) or len(after) + 1 != len(cells):
        return None
    for i, (a, b) in enumerate(zip(before, after)):
        if a != b:
            return i
    return None


def _group_of(unit) -> str | None:
    gap = _primary_gap(unit)
    if gap is None:
        return None
    cells = unit["after"]["cells"]
    left, right = _trail(cells[gap]), _lead(cells[gap + 1])
    if left == "qsUtter" and right == "qsNo":
        return "A"
    if left == "qsIt" and right == "qsUtter":
        return "B"
    if right == "qsNo":
        return "C"
    return "D"


def _latest_verdicts() -> dict[str, str]:
    raw = json.loads(VERDICTS.read_text())["verdicts"]
    by_unit: dict[str, dict] = {}
    for v in raw:
        uid = v["unit"]
        if uid not in by_unit or v["at"] > by_unit[uid]["at"]:
            by_unit[uid] = v
    return {uid: v["verdict"] for uid, v in by_unit.items()}


def _all_units() -> dict[str, dict]:
    units: dict[str, dict] = {}
    for f in glob.glob(UNITS_GLOB):
        for u in json.load(open(f)):
            units[u["id"]] = u
    return units


def load_targets(group: str) -> list[dict]:
    units = _all_units()
    verdicts = _latest_verdicts()
    out = []
    for uid, u in units.items():
        if u["class"] != "seam-loss-withdrawal" or verdicts.get(uid) != "reject":
            continue
        if _group_of(u) != group:
            continue
        out.append(u)
    return sorted(out, key=lambda u: u["notation"])


def load_collateral(families: list[str]) -> list[dict]:
    units = _all_units()
    verdicts = _latest_verdicts()
    fams = set(families)
    out = []
    for uid, u in units.items():
        if u["class"] in fams and verdicts.get(uid) == "approve":
            out.append(u)
    return sorted(out, key=lambda u: u["notation"])


def _codepoints(unit) -> list[int]:
    return [int(h, 16) for h in unit["codepoints"].split(":")]


def seams_under(spec, cps: list[int]) -> list[str]:
    settled = settle(spec, cps, frozenset())
    out = []
    for s in settled[:-1]:
        out.append("break" if s.seam is None else f"y{spec.registry.y_of(s.seam)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runes_dir", type=Path)
    ap.add_argument("--group", choices=["A", "B", "C", "D"], required=True)
    ap.add_argument("--collateral", default="")
    args = ap.parse_args()

    spec = load_spec(args.runes_dir, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)

    targets = load_targets(args.group)
    print(f"=== TARGETS group {args.group} ({len(targets)}) — want CANDIDATE == OLD at the primary gap ===")
    restored = same = other = 0
    for u in targets:
        gap = _primary_gap(u)
        old = u["before"]["seams"]
        cur = u["after"]["seams"]
        cand = seams_under(spec, _codepoints(u))
        if len(cand) != len(old):
            flag = "LEN-MISMATCH"
        elif cand[gap] == old[gap]:
            flag = "RESTORED"
            restored += 1
        elif cand[gap] == cur[gap]:
            flag = "unchanged"
            same += 1
        else:
            flag = "OTHER"
            other += 1
        print(f"  {flag:12s} {u['id']:9s} {u['notation']:30s} gap{gap} old={old[gap]} cur={cur[gap]} cand={cand[gap]}  [{','.join(cand)}]")
    print(f"  -> RESTORED={restored} unchanged={same} other/len={other + (len(targets) - restored - same)}")

    fams = [f for f in args.collateral.split(",") if f]
    if fams:
        coll = load_collateral(fams)
        print(f"\n=== COLLATERAL approved in {fams} ({len(coll)}) — want CANDIDATE == CURRENT everywhere ===")
        regressed = []
        for u in coll:
            cur = u["after"]["seams"]
            cand = seams_under(spec, _codepoints(u))
            if list(cand) != list(cur):
                regressed.append((u, cur, cand))
        print(f"  REGRESSED (candidate disturbs an approved window): {len(regressed)}")
        for u, cur, cand in regressed[:60]:
            print(f"    {u['id']:9s} {u['class']:22s} {u['notation']:30s} cur={cur} cand={list(cand)}")


if __name__ == "__main__":
    main()
