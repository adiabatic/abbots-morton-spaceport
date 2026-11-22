"""GPOS emission: one cursive lookup for each height in `CURS_HEIGHT_YS` that has anchors (M1-PLAN section 5, Group 3), and the `kern` feature's contextual lookup (rebuild/KERN-PLAN.md section 5).

Each `curs` lookup registers every glyph with an anchor at that height. A glyph that enters or exits at another height gets a NULL anchor on that side. When `spec` is given, every locked copy also gets a NULL/NULL registration at each height where its rune declares an entry row; without `spec` these are skipped and the output says so in a comment. Coordinates are glyph-space pixels × 50, with x shifted by the one-pixel ink offset.

**The kern lookup.** `kern_plan` turns the kern stage's resolution (`kern.KernResolution`) into rules, and `emit_gpos` writes them after the `curs` feature, so the cursive lookups keep the first places in the lookup list and the kern lookup comes after them. The lookup is one chained-context lookup (GPOS type 8), not a pair lookup. HarfBuzz skips a ZWNJ when it matches GPOS input, so a pair lookup kerns across one; CoreText does not skip it, so the same pair lookup leaves a pair across a ZWNJ unkerned there. A chained-context lookup holds, for each kerned case, a rule for the adjacent pair and a rule with one explicit `uni200C` between the two glyphs, and a shaper applies the first rule of a lookup that matches: HarfBuzz matches the adjacent rule across a ZWNJ and never reaches the second, and CoreText matches the second. So a pair across one ZWNJ kerns once in both, which is the author's ruling that a break at a ZWNJ looks like any other break between the same two shapes. Each rule invokes a single-adjustment value lookup, one for each distinct value, which moves the first glyph's advance by that value.

The rules are grouped to keep their count down: the leading sides whose rows (each following side and its value) are the same form one input class, and for each value of that row one rule pair reaches the union of the following sides that take the value. feaLib picks each subtable's format. A side's glyphs are its settled cells' glyphs and the `.ss10` copies of the cells it holds (`kern.KernResolution.glyph_sides`). Only facing pairs whose value is not 0 are compiled, so with no such pair the font has no `kern` feature at all, which read-back expects.

`kern_plan` refuses a lookup that could move a join (`KernPlanError`): a glyph in an input class with an exit anchor, or a glyph in a lookahead class with an entry anchor, which a `selectable: false` entry row gives every cell of its stance (`model.GlyphRecord.entry_curs_only`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from rebuild.pipeline.model import CellId, GlyphRecord, ResolvedSpec

if TYPE_CHECKING:
    from rebuild.pipeline.kern import KernResolution, Side

PIXEL = 50
INK_X_OFFSET = 1
CURS_HEIGHT_YS = (0, 5, 6, 8)
ZWNJ_GLYPH = "uni200C"
KERN_LOOKUP = "m1_kern"
KERN_FEATURE = "kern"
# The behavior-class tokens the kern lookup adds to `behavior_classes.json` when it exists: its two rule shapes, which the conformance sweep's and the deep sweep's keys hash (`artifact_cycle.deep_sweep_skip_lines`).
KERN_BEHAVIOR_CLASSES = ("kern:adjacent", "kern:one-zwnj")


Anchor = tuple[int, int] | None
Registration = tuple[Anchor, Anchor]


class KernPlanError(AssertionError):
    """The kern lookup the stage's resolution asks for could move a join: a glyph it would adjust carries an exit anchor, or a glyph it would read after the adjusted one carries an entry anchor."""


@dataclass(frozen=True)
class KernRule:
    """One rule pair of the kern lookup: the glyphs it adjusts (`lead`), the glyphs that must follow them, adjacent or after one `uni200C` (`follow`), and the advance it adds to the first glyph, in font units (`value`). `lead_class` and `follow_class` are the FEA class names."""

    lead_class: str
    lead: tuple[str, ...]
    follow_class: str
    follow: tuple[str, ...]
    value: int


@dataclass(frozen=True)
class KernPlan:
    """The kern lookup in emission order. `relation` is the (first glyph, second glyph) to value map every rule pair stands for, which read-back compares with both rule shapes it finds in the font."""

    rules: tuple[KernRule, ...] = ()

    @property
    def values(self) -> tuple[int, ...]:
        return tuple(sorted({rule.value for rule in self.rules}))

    def relation(self) -> dict[tuple[str, str], int]:
        pairs: dict[tuple[str, str], int] = {}
        for rule in self.rules:
            for lead in rule.lead:
                for follow in rule.follow:
                    pairs.setdefault((lead, follow), rule.value)
        return pairs

    def behavior_classes(self) -> tuple[str, ...]:
        return KERN_BEHAVIOR_CLASSES if self.rules else ()


def value_lookup_name(value: int) -> str:
    """The name of the value lookup that adds `value` font units to a glyph's advance."""
    return f"m1_kern_value_{'m' if value < 0 else 'p'}{abs(value)}"


def kern_plan(resolution: KernResolution, glyphs: Mapping[CellId, GlyphRecord]) -> KernPlan:
    """Return the kern lookup's rules for the stage's resolution, grouped as the module docstring says, with every value in font units. `glyphs` are the settled cells' records; each `.ss10` copy is drawn from its source cell with its anchors stripped, so it carries none. Raises `KernPlanError` when a glyph the lookup would adjust has an exit anchor, or a glyph it would read after one has an entry anchor."""
    leading, following = resolution.glyph_sides(glyphs)
    records = {record.name: record for record in glyphs.values()}
    rows: dict[Side, dict[Side, int]] = {}
    for pair in resolution.pairs:
        if pair.by:
            rows.setdefault(pair.leading, {})[pair.following] = pair.by * PIXEL
    lead_glyphs: dict[Side, list[str]] = {}
    for name, side in leading.items():
        lead_glyphs.setdefault(side, []).append(name)
    follow_glyphs: dict[Side, list[str]] = {}
    for name, side in following.items():
        follow_glyphs.setdefault(side, []).append(name)

    groups: dict[tuple[tuple[str, int], ...], list[Side]] = {}
    for side, row in rows.items():
        key = tuple(sorted(((follow.name, value) for follow, value in row.items())))
        groups.setdefault(key, []).append(side)
    rules: list[KernRule] = []
    for group_index, key in enumerate(
        sorted(groups, key=lambda key: sorted(side.name for side in groups[key]))
    ):
        sides = groups[key]
        lead = tuple(sorted(name for side in sides for name in lead_glyphs.get(side, ())))
        for name in lead:
            record = records.get(name)
            if record is not None and record.exit is not None:
                raise KernPlanError(
                    f"{name} exits at {record.exit} but a kern record adjusts it as the first glyph of a break; a glyph with a live exit is on no leading side (rebuild/KERN-PLAN.md section 5)"
                )
        row = rows[sides[0]]
        for value_index, value in enumerate(sorted({value for value in row.values()})):
            follow_sides = [side for side, taken in row.items() if taken == value]
            follow = tuple(sorted(name for side in follow_sides for name in follow_glyphs.get(side, ())))
            for name in follow:
                record = records.get(name)
                if record is None:
                    continue
                if record.entry is not None or record.entry_curs_only is not None:
                    anchor = record.entry if record.entry is not None else record.entry_curs_only
                    raise KernPlanError(
                        f"{name} carries a cursive entry anchor at {anchor} but follows a kerned break, so the kern lookup would read it after a glyph it adjusts; a `selectable: false` entry row gives every cell of its stance that anchor (`surface.resolve_cell`). Remove the kern records that reach its following side ({following[name].name}), or give the stance's entry row `selectable: true` (rebuild/KERN-PLAN.md section 5)"
                    )
            if not lead or not follow:
                continue
            rules.append(
                KernRule(
                    lead_class=f"@m1_kern_lead_{group_index}",
                    lead=lead,
                    follow_class=f"@m1_kern_follow_{group_index}_{value_index}",
                    follow=follow,
                    value=value,
                )
            )
    return KernPlan(rules=tuple(rules))


def _units(anchor: tuple[int, int] | None) -> Anchor:
    if anchor is None:
        return None
    x_px, y_px = anchor
    return ((x_px + INK_X_OFFSET) * PIXEL, y_px * PIXEL)


def _anchor(units: Anchor) -> str:
    return "<anchor NULL>" if units is None else f"<anchor {units[0]} {units[1]}>"


def _entry_heights(spec: ResolvedSpec, rune_name: str) -> set[int]:
    rune = spec.runes.get(rune_name)
    if rune is None:
        return set()
    heights: set[int] = set()
    for stance in rune.stances.values():
        for height in stance.surface.entries:
            heights.add(spec.registry.y_of(height))
    return heights


def cursive_registrations(
    glyphs: Mapping[CellId, GlyphRecord], spec: ResolvedSpec | None = None
) -> dict[int, dict[str, Registration]]:
    """Per registered height, every glyph's (entry, exit) anchor pair in font units. `emit_gpos` renders it and `rebuild/pipeline/readback.py` checks the compiled GPOS against it. An absent side is None, and a locked copy's coverage-only registration is (None, None)."""
    per_height: dict[int, dict[str, Registration]] = {y: {} for y in CURS_HEIGHT_YS}
    for cell, record in glyphs.items():
        for y in CURS_HEIGHT_YS:
            entry = None
            if record.entry is not None and record.entry[1] == y:
                entry = record.entry
            elif record.entry_curs_only is not None and record.entry_curs_only[1] == y:
                entry = record.entry_curs_only
            exit_anchor = record.exit if record.exit is not None and record.exit[1] == y else None
            if entry is None and exit_anchor is None:
                continue
            per_height[y][record.name] = (_units(entry), _units(exit_anchor))

    if spec is not None:
        for cell, record in glyphs.items():
            if "locked" not in cell.adjustments:
                continue
            for y in _entry_heights(spec, cell.rune):
                per_height.setdefault(y, {}).setdefault(record.name, (None, None))
    return per_height


def emit_kern(plan: KernPlan) -> str:
    """Return the kern lookup's FEA: one value lookup per distinct value, the rules' glyph classes, and the `kern` feature holding the chained-context lookup, each rule pair as its adjacent rule and then its one-`uni200C` rule. Empty when the plan has no rule, so the font then has no `kern` feature."""
    if not plan.rules:
        return ""
    parts: list[str] = []
    for value in plan.values:
        adjusted = sorted({name for rule in plan.rules if rule.value == value for name in rule.lead})
        name = value_lookup_name(value)
        parts.append(f"lookup {name} {{\n    pos [{' '.join(adjusted)}] <0 0 {value} 0>;\n}} {name};")
    declared: set[str] = set()
    for rule in plan.rules:
        for klass, members in ((rule.lead_class, rule.lead), (rule.follow_class, rule.follow)):
            if klass not in declared:
                declared.add(klass)
                parts.append(f"{klass} = [{' '.join(members)}];")
    statements: list[str] = []
    for rule in plan.rules:
        name = value_lookup_name(rule.value)
        statements.append(f"        pos {rule.lead_class}' lookup {name} {rule.follow_class};")
        statements.append(f"        pos {rule.lead_class}' lookup {name} {ZWNJ_GLYPH} {rule.follow_class};")
    parts.append(
        f"feature {KERN_FEATURE} {{\n    lookup {KERN_LOOKUP} {{\n"
        + "\n".join(statements)
        + f"\n    }} {KERN_LOOKUP};\n}} {KERN_FEATURE};"
    )
    return "\n".join(parts) + "\n"


def emit_gpos(
    glyphs: Mapping[CellId, GlyphRecord], spec: ResolvedSpec | None = None, kern: KernPlan | None = None
) -> str:
    per_height = cursive_registrations(glyphs, spec)
    coverage_only_skipped = spec is None

    blocks: list[str] = []
    for y in CURS_HEIGHT_YS:
        statements = [
            f"        pos cursive {name} {_anchor(entry)} {_anchor(exit)};"
            for name, (entry, exit) in sorted(per_height[y].items())
        ]
        if not statements:
            # A height with no anchors in this glyph set emits no lookup. The M1 runes declare rows at all four heights.
            continue
        blocks.append(
            f"    lookup m1_cursive_y{y} {{\n" + "\n".join(statements) + f"\n    }} m1_cursive_y{y};"
        )

    header = ""
    if coverage_only_skipped:
        header = (
            "# locked-copy NULL/NULL coverage-only registrations skipped: no spec supplied to emit_gpos.\n"
        )
    curs = header + "feature curs {\n" + "\n".join(blocks) + "\n} curs;\n"
    kerned = emit_kern(kern) if kern is not None else ""
    return curs + ("\n" + kerned if kerned else "")
