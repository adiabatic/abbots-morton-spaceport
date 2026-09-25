"""Classify the recorded leaks by whether the join contract can remove them.

The emitter enforces the join contract with `_JoinContractRecorder` in `tools/quikscript_fea.py`, and doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md describes it. This module applies the contract's predicate from outside the emitter, reading only the built Senior `calt` FEA and the leak files `site/bad-leak-backlog.txt` and `site/benign-leak-census.txt`. The two can disagree for two reasons. This module tests joins with bare anchor Ys, while the emitter also counts the Ys a neighbor reaches in context. The emitter also counts a variant with no exit (forward) or no entry (backward) as joining, and this module has no such exemption. `tools/build_check_html.py` uses `classify` to group the leaks on site/check.html. Run as a script, the module writes the full breakdown to `tmp/leak-contract-report.txt`.

The predicate: a contextual substitution that selects variant `V` keeps a neighbor `N` only if `V` joins `N`, meaning `exit_ys(V) & entry_ys(N)` is non-empty for a follower and `exit_ys(N) & entry_ys(V)` for a predecessor. A non-joining neighbor is dropped unless `V` has a directional cosmetic modifier (`before-<fam>` for a follower, `after-<fam>` for a predecessor) that names the neighbor's family.

For each leak signature `(isolated_left, left_chosen, isolated_right, right_chosen)`, each side whose stance changed is checked for a single emitted `calt` rule that outputs the changed stance with the non-joining neighbor in the nearest context position. A leak is `emergent` when some changed side has no such rule, because the change comes from several lookups combined (the `·Ah·It | ·Tea·Oy` case in doc/history/2026-06-03--leak-cleanup/leak-investigation-findings.md). Otherwise it is `cosmetic` when every changed side has a cosmetic modifier, `droppable` when none does, and `mixed` when one side does.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
SITE_DIR = ROOT / "site"
TOOLS_DIR = ROOT / "tools"
for _p in (str(TOOLS_DIR), str(TEST_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from leak_static_analysis import Rule, parse_calt  # noqa: E402
from quikscript_shaping_helpers import _compiled_meta, _entry_ys, _exit_ys  # noqa: E402

FEA_PATH = SITE_DIR / "AbbotsMortonSpaceportSansSenior-Regular.fea"
# The bad backlog and the benign census together list every visible depth-4 leak.
SNAPSHOT_PATHS = (SITE_DIR / "bad-leak-backlog.txt", SITE_DIR / "benign-leak-census.txt")
DUMP_PATH = ROOT / "tmp" / "leak-contract-report.txt"

Signature = tuple[str, str, str, str]  # (isolated_left, left_chosen, isolated_right, right_chosen)


def joins(left: str, right: str) -> bool:
    """Return whether *left* has an exit at a Y where *right* has an entry, as `leak_static_analysis.joins` does."""
    return bool(_exit_ys(left) & _entry_ys(right))


def _base_name(glyph: str) -> str:
    meta = _compiled_meta().get(glyph)
    return meta.base_name if meta is not None else glyph


def _is_cosmetic(variant: str, neighbor: str, *, direction: str) -> bool:
    """Return whether *variant* declares a cosmetic interaction with *neighbor*'s family.

    It must have a `before-` modifier (for a follower) or an `after-` modifier (for a predecessor), and its resolved trigger list (`meta.before` or `meta.after`) must name the neighbor or its family. Reading the trigger list handles modifiers whose stem is not one family name, such as `before-vertical` and `before-day-exam`.
    """
    meta = _compiled_meta().get(variant)
    if meta is None:
        return False
    prefix = "before-" if direction == "forward" else "after-"
    if not any(m.startswith(prefix) for m in meta.modifiers):
        return False
    triggers = meta.before if direction == "forward" else meta.after
    neighbor_bases = {neighbor, _base_name(neighbor)}
    for trigger in triggers:
        if trigger in neighbor_bases or _base_name(trigger) in neighbor_bases:
            return True
    return False


@dataclass
class SideVerdict:

    side: str  # "left" or "right"
    variant: str  # the in-context stance V the rule selected (left_chosen / right_chosen)
    neighbor: str  # the non-joining neighbor whose presence drove the selection
    direction: str  # "forward" (follower drove a left exit) or "backward" (predecessor drove a right entry)
    rule_lines: tuple[int, ...] = ()  # emitted FEA line numbers of the explaining rule(s)
    cosmetic: bool = False

    @property
    def reachable(self) -> bool:
        return bool(self.rule_lines)


@dataclass
class LeakVerdict:
    signature: Signature
    label: str
    sides: list[SideVerdict] = field(default_factory=list)

    @property
    def klass(self) -> str:
        if not self.sides or any(not s.reachable for s in self.sides):
            return "emergent"
        if all(s.cosmetic for s in self.sides):
            return "cosmetic"
        if all(not s.cosmetic for s in self.sides):
            return "droppable"
        return "mixed"


def parse_snapshot(paths: tuple[Path, ...] = SNAPSHOT_PATHS) -> list[tuple[Signature, str]]:
    """Return `(signature, example label)` for each leak line in the given files, by default the bad backlog and the benign census. It parses lines as `leak_snapshot.parse_snapshot` does, without importing the shaping code that module needs."""
    out: list[tuple[Signature, str]] = []
    for path in paths:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            label, _, diff = line.partition(" :: ")
            il = lc = ir = rc = ""
            for clause in diff.split(" | "):
                clause = clause.strip().lstrip("*").strip()
                if clause.startswith("L ") and "->" in clause:
                    il, _, lc = clause[2:].partition("->")
                elif clause.startswith("R ") and "->" in clause:
                    ir, _, rc = clause[2:].partition("->")
            out.append(((il.strip(), lc.strip(), ir.strip(), rc.strip()), label.strip()))
    return out


def _subs_by_replacement(rules: list[Rule]) -> dict[str, list[Rule]]:
    index: dict[str, list[Rule]] = {}
    for rule in rules:
        if rule.kind == "sub" and rule.replacement is not None:
            index.setdefault(rule.replacement, []).append(rule)
    return index


def _explaining_rules(
    rules: list[Rule], *, variant: str, neighbor_forms: set[str], direction: str
) -> tuple[int, ...]:
    """Return the sorted line numbers of the rules in *rules* that have one of *neighbor_forms* in the nearest context position: `lookahead[0]` for forward, `backtrack[-1]` for backward.

    The function does not read *variant*. The caller passes only the rules whose replacement is *variant*, because the contract concerns the variant a rule outputs, whatever the pivot was before that lookup.
    """
    found: list[int] = []
    for rule in rules:
        if direction == "forward":
            if not rule.lookahead:
                continue
            context = rule.lookahead[0]
        else:
            if not rule.backtrack:
                continue
            context = rule.backtrack[-1]
        if neighbor_forms & context:
            found.append(rule.line_no)
    return tuple(sorted(found))


def classify(snapshot: list[tuple[Signature, str]], rules: list[Rule]) -> list[LeakVerdict]:
    index = _subs_by_replacement(rules)
    verdicts: list[LeakVerdict] = []
    for sig, label in snapshot:
        il, lc, ir, rc = sig
        verdict = LeakVerdict(signature=sig, label=label)
        # The left stance changed: look for a rule that outputs `lc` with the right glyph in its nearest lookahead position.
        if il != lc:
            neighbor_forms = {rc, ir}
            lines = ()
            if not joins(lc, rc):
                lines = _explaining_rules(
                    index.get(lc, []), variant=lc, neighbor_forms=neighbor_forms, direction="forward"
                )
            verdict.sides.append(
                SideVerdict(
                    side="left",
                    variant=lc,
                    neighbor=rc,
                    direction="forward",
                    rule_lines=lines,
                    cosmetic=bool(lines) and _is_cosmetic(lc, rc, direction="forward"),
                )
            )
        # The right stance changed: look for a rule that outputs `rc` with the left glyph in its nearest backtrack position.
        if ir != rc:
            neighbor_forms = {lc, il}
            lines = ()
            if not joins(lc, rc):
                lines = _explaining_rules(
                    index.get(rc, []), variant=rc, neighbor_forms=neighbor_forms, direction="backward"
                )
            verdict.sides.append(
                SideVerdict(
                    side="right",
                    variant=rc,
                    neighbor=lc,
                    direction="backward",
                    rule_lines=lines,
                    cosmetic=bool(lines) and _is_cosmetic(rc, lc, direction="backward"),
                )
            )
        verdicts.append(verdict)
    return verdicts


def _format_dump(verdicts: list[LeakVerdict]) -> str:
    buckets: dict[str, list[LeakVerdict]] = {"droppable": [], "cosmetic": [], "mixed": [], "emergent": []}
    for v in verdicts:
        buckets[v.klass].append(v)
    headers = {
        "droppable": "Contract makes these impossible (moot for triage):",
        "cosmetic": "Author-declared cosmetic tucks the contract keeps (already labeled):",
        "mixed": "One side droppable, the other cosmetic (review):",
        "emergent": "Emergent across lookups; contract cannot reach (genuine triage):",
    }
    lines: list[str] = [
        "# Leak-contract Phase-1 report. Generated by tools/leak_contract_report.py; do not hand-edit.",
        f"# snapshot leaks: {len(verdicts)}",
        "",
    ]
    for klass in ("droppable", "cosmetic", "mixed", "emergent"):
        rows = buckets[klass]
        lines.append(f"## {headers[klass]} ({len(rows)})")
        for v in sorted(rows, key=lambda v: v.label):
            il, lc, ir, rc = v.signature
            detail = []
            for s in v.sides:
                tag = (
                    "cosmetic"
                    if s.cosmetic
                    else ("rule@" + ",".join(map(str, s.rule_lines)) if s.reachable else "no-rule")
                )
                detail.append(f"{s.side}:{s.variant} vs {s.neighbor} [{tag}]")
            lines.append(f"  {v.label}  ::  L {il}->{lc} | R {ir}->{rc}  ::  {'; '.join(detail)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    fea = Path(sys.argv[1]) if len(sys.argv) > 1 else FEA_PATH
    snapshot = parse_snapshot()
    program = parse_calt(str(fea))
    verdicts = classify(snapshot, program.rules)

    counts = {"droppable": 0, "cosmetic": 0, "mixed": 0, "emergent": 0}
    for v in verdicts:
        counts[v.klass] += 1
    total = len(verdicts)
    reachable = counts["droppable"] + counts["cosmetic"] + counts["mixed"]

    DUMP_PATH.parent.mkdir(exist_ok=True)
    DUMP_PATH.write_text(_format_dump(verdicts))

    print(f"Parsed {len(program.rules)} calt rules from {fea.name}")
    print(f"Snapshot leaks: {total}")
    print(f"  contract-reachable (single-stance): {reachable}")
    print(f"    droppable  (contract erases -> MOOT for triage): {counts['droppable']}")
    print(f"    cosmetic   (author-declared tuck, contract keeps): {counts['cosmetic']}")
    print(f"    mixed      (one side each; review):                {counts['mixed']}")
    print(f"  emergent (needs genuine triage):                     {counts['emergent']}")
    print(f"\nFull breakdown written to {DUMP_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
