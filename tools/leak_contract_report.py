"""Phase-1 reporting pass for the derived join contract (see doc/history/2026-06-03--leak-cleanup/leak-prevention-plan.md).

This is the cross-check oracle that brief calls for: the standalone, read-only classifier that the eventual in-emitter warn pass must agree with. It changes zero FEA bytes because it only reads the already-built Senior `calt` FEA and the approved depth-4 leak snapshot.

What it answers: of the leaks currently frozen in the bad backlog + benign census (`site/bad-leak-backlog.txt`, `site/benign-leak-census.txt`), how many will the construction-time join contract make impossible (so they are moot for hand-triage), how many are author-declared cosmetic tucks the contract is meant to keep, and how many are emergent across the chained lookups and so out of the contract's reach. That partition is the whole point — it tells you which snapshot rows you can skip when collecting verdicts.

The contract's per-rule predicate (from the brief): a contextual substitution that selects variant `V` may keep a neighbor `N` only if `V` cursively joins `N` — `exit_ys(V) & entry_ys(N) != set()` for a forward (follower) neighbor, `exit_ys(N) & entry_ys(V) != set()` for a backward (predecessor) neighbor. A non-joining neighbor is dropped unless `V` carries a directional cosmetic modifier (`before-<fam>` for a follower, `after-<fam>` for a predecessor) naming that neighbor's family.

We project each snapshot signature `(isolated_left, left_chosen, isolated_right, right_chosen)` onto that predicate: for the side whose form changed in context, look for a single emitted `calt` rule that selects the changed form `V` with the non-joining neighbor in its nearest context position. If such a rule exists, the leak is single-form (contract-reachable); if it does not, the dependency only emerges from the composition of several lookups (the `·Ah·It | ·Tea·Oy` case in the findings doc) and stays the snapshot gate's job.
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
# The full set of visible depth-4 leaks now lives partitioned across the bad backlog and the benign census; this analysis wants both halves together (the old single isolation-leak-snapshot.txt was retired when those two files began reconstructing it).
SNAPSHOT_PATHS = (SITE_DIR / "bad-leak-backlog.txt", SITE_DIR / "benign-leak-census.txt")
DUMP_PATH = ROOT / "tmp" / "leak-contract-report.txt"

Signature = tuple[str, str, str, str]  # (isolated_left, left_chosen, isolated_right, right_chosen)


def joins(left: str, right: str) -> bool:
    """Whether *left* can cursively hand off to *right*. Mirrors `leak_static_analysis.joins` / `_pair_join_ys`."""
    return bool(_exit_ys(left) & _entry_ys(right))


def _base_name(glyph: str) -> str:
    meta = _compiled_meta().get(glyph)
    return meta.base_name if meta is not None else glyph


def _is_cosmetic(variant: str, neighbor: str, *, direction: str) -> bool:
    """Whether *variant* is an author-declared cosmetic interaction with *neighbor*'s family.

    The signal (per the brief, to avoid adding YAML) is a directional cosmetic modifier — `before-<fam>` for a follower neighbor, `after-<fam>` for a predecessor — paired with the neighbor's family appearing in the form's resolved trigger list. The modifier says "this cross-break shape change is intentional"; the trigger list says "for these families", so together they pin the opt-out to the right neighbor without re-deriving messy modifier stems (`before-vertical`, `before-day-exam`, ...).
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
    """How one changed side of a leak fares against the contract."""

    side: str  # "left" or "right"
    variant: str  # the in-context form V the rule selected (left_chosen / right_chosen)
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
    """Read each approved leak as `(signature, example-label)` across the given snapshot files (default: the bad backlog + benign census, together the full visible set). Mirrors `leak_snapshot.parse_snapshot` but without pulling in the shaping/HTML stack."""
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
    """Line numbers of `calt` rules that select *variant* with one of *neighbor_forms* in the nearest context position on the side the contract polices.

    Forward: the follower neighbor sits in the nearest lookahead slot (`lookahead[0]`). Backward: the predecessor sits in the nearest backtrack slot (`backtrack[-1]`). We match on the rule's *output* (`replacement == variant`) rather than its pivot pre-form, because the contract's predicate is about which variant a rule emits next to a non-joining neighbor — independent of whatever the pivot was before this lookup in the chain.
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
        # Left changed: its exit form was modulated by the follower it does not join -> a forward rule selecting `lc` with the right glyph in lookahead.
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
        # Right changed: its entry form was modulated by the predecessor it does not join -> a backward rule selecting `rc` with the left glyph in backtrack.
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
    print(f"  contract-reachable (single-form): {reachable}")
    print(f"    droppable  (contract erases -> MOOT for triage): {counts['droppable']}")
    print(f"    cosmetic   (author-declared tuck, contract keeps): {counts['cosmetic']}")
    print(f"    mixed      (one side each; review):                {counts['mixed']}")
    print(f"  emergent (needs genuine triage):                     {counts['emergent']}")
    print(f"\nFull breakdown written to {DUMP_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
