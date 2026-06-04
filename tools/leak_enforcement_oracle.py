"""Standalone enforcement oracle for the derived join contract (see doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md).

Read-only. Applies the contract predicate to every Quikscript `sub` rule in the emitted Senior `calt` and predicts what Phase-2 enforcement would do: which adjacent Quikscript neighbors get dropped (the selected variant V cannot cursive-join them) and which rule context positions would empty out (refuse-to-emit). It then cross-checks against the snapshot partition from leak_contract_report.

The point is twofold. Scoped to the snapshot rows it independently reproduces the report's 164 droppable. But the all-rules sweep shows that a *blind* FEA rewrite would touch almost every rule and empty thousands of positions -- empirical proof that enforcement must live inside the emitter (where the selection-driving neighbor is known), not as a post-hoc FEA pass. See doc/history/2026-06-03--leak-cleanup/leak-triage.md.

Guard: police only Quikscript neighbors (names starting "qs"). `space` / ZWNJ (`uni200C`) / punctuation / Latin are boundary context, never join neighbors -- they have no anchors and must never be dropped.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT / "tools"), str(ROOT / "test")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import leak_contract_report as rep  # noqa: E402
from leak_static_analysis import Rule, parse_calt  # noqa: E402
from quikscript_shaping_helpers import _compiled_meta, _entry_ys, _exit_ys  # noqa: E402


def _is_qs(glyph: str) -> bool:
    return glyph.startswith("qs")


def _base_family(glyph: str) -> str:
    return glyph.split(".")[0]


def _is_cosmetic(variant: str, neighbor: str, direction: str) -> bool:
    """A `before-<fam>` modifier exempts a follower; `after-<fam>` exempts a predecessor (the no-new-YAML opt-out)."""
    meta = _compiled_meta().get(variant)
    if meta is None:
        return False
    prefix = "before-" if direction == "forward" else "after-"
    if not any(m.startswith(prefix) for m in meta.modifiers):
        return False
    fams = {neighbor, _base_family(neighbor)}
    triggers = meta.before if direction == "forward" else meta.after
    return any(t in fams or _base_family(t) in fams for t in triggers)


def _joins(variant: str, neighbor: str, direction: str) -> bool:
    if direction == "forward":  # follower: variant's exit must reach the neighbor's entry
        return bool(_exit_ys(variant) & _entry_ys(neighbor))
    return bool(
        _exit_ys(neighbor) & _entry_ys(variant)
    )  # predecessor: neighbor's exit must reach variant's entry


def main() -> None:
    prog = parse_calt(str(rep.FEA_PATH))
    rules = prog.rules
    subs = [r for r in rules if r.kind == "sub" and r.replacement and _is_qs(r.replacement)]
    print(f"total rules: {len(rules)}; qs-sub rules: {len(subs)}")

    drops: Counter[str] = Counter()
    empties: Counter[str] = Counter()
    cosmetic_keeps: Counter[str] = Counter()
    rules_touched: set[int] = set()

    def police(rule: Rule, context_pos: frozenset[str] | None, direction: str) -> None:
        if not context_pos:
            return
        variant = rule.replacement
        assert variant is not None
        qs = [g for g in context_pos if _is_qs(g)]
        if not qs:
            return
        kept_qs: list[str] = []
        dropped: list[str] = []
        for neighbor in qs:
            if _joins(variant, neighbor, direction):
                kept_qs.append(neighbor)
            elif _is_cosmetic(variant, neighbor, direction):
                cosmetic_keeps[direction] += 1
                kept_qs.append(neighbor)
            else:
                dropped.append(neighbor)
        if dropped:
            drops[direction] += len(dropped)
            rules_touched.add(rule.line_no)
            if not kept_qs and not [g for g in context_pos if not _is_qs(g)]:
                empties[direction] += 1

    for r in subs:
        police(r, r.lookahead[0] if r.lookahead else None, "forward")
        police(r, r.backtrack[-1] if r.backtrack else None, "backward")

    print("\n== predicted blind all-rules enforcement delta ==")
    print(f"  forward neighbor-drops:       {drops['forward']}")
    print(f"  backward neighbor-drops:      {drops['backward']}")
    print(f"  cosmetic keeps (fwd / bwd):   {cosmetic_keeps['forward']} / {cosmetic_keeps['backward']}")
    print(f"  rule positions emptied (f/b): {empties['forward']} / {empties['backward']}")
    print(f"  distinct rules touched:       {len(rules_touched)} of {len(subs)} qs-sub rules")
    print("  (this blind sweep is an OVER-count: most of these neighbors are incidental context, not")
    print("   selection-driving join targets. The correctly-scoped contract lives in the emitter.)")

    verdicts = rep.classify(rep.parse_snapshot(), rules)
    klass = Counter(v.klass for v in verdicts)
    print("\n== snapshot partition (correctly-scoped, per-row) ==")
    for k in ("droppable", "cosmetic", "mixed", "emergent"):
        print(f"  {k:10s} {klass[k]}")
    print(
        f"  predicted post-contract snapshot: {len(verdicts)} - {klass['droppable']} = {len(verdicts) - klass['droppable']}"
    )

    miss = [
        v.label
        for v in verdicts
        if v.klass == "droppable" and not ({ln for s in v.sides for ln in s.rule_lines} & rules_touched)
    ]
    print(
        f"\n== droppable rows whose explaining rule is NOT touched by the oracle (should be 0): {len(miss)} =="
    )
    for label in miss[:20]:
        print(f"  {label}")


if __name__ == "__main__":
    main()
