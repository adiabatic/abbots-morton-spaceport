"""FEA emitter for the prototype font (prototype/PLAN.md sections 1 and 4).

`emit_fea(table, spec) -> str` produces the complete feature file: GSUB standalone lookups `proto_formation`, `proto_ss03_marker`, `proto_zwnj`, `proto_settle` in that definition order (definition order fixes LookupList indices, which fixes cross-feature application order in both HarfBuzz and CoreText — formation before the marker is what makes divergence 5 structural), feature blocks `calt` and `ss03`, then the GPOS `curs` feature with one per-height cursive lookup in today's verbatim shape (NULLed anchors for cross-height cells, NULL/NULL coverage-parity registrations for locked twins, coordinates = glyph-space pixels x 50 plus the one-pixel ink-centering offset).

`proto_settle` is ONE chained-context lookup of single substitutions: backtrack classes contain settled glyphs (GSUB backtrack sees post-substitution glyphs — the within-lookup sequencing under cross-shaper test), lookahead classes contain raw glyphs only, positive rules only, zero selection-semantics `ignore sub`. Boundary-outcome rows carry `uni200C` explicit in the class at the boundary slot, ordered ahead of any join row that could match across a skipped ZWNJ (the proven coverage-transform shape from quikscript_fea.py:2056-2230).

Asserts the PLAN.md section 7 emitter invariant before returning: no locked twin and no chokepoint output appears in any raw lookahead class, and every glyph named in any rule exists in the planned glyph set.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec import INK_X_OFFSET, PIXEL, SPEC, SubsetSpec
from table import DecisionTable, build_table

CURS_HEIGHTS = (0, 5, 8)


def _anchor(x_px: int, y_px: int) -> str:
    return f"<anchor {(x_px + INK_X_OFFSET) * PIXEL} {y_px * PIXEL}>"


class _ClassRegistry:
    def __init__(self) -> None:
        self.by_members: dict[tuple[str, ...], str] = {}
        self.definitions: list[str] = []

    def ref(self, members: tuple[str, ...], hint: str) -> str:
        if len(members) == 1:
            return members[0]
        members = tuple(sorted(members))
        name = self.by_members.get(members)
        if name is None:
            name = f"@{hint}"
            suffix = 0
            while any(line.startswith(name + " ") for line in self.definitions):
                suffix += 1
                name = f"@{hint}_{suffix}"
            self.by_members[members] = name
            self.definitions.append(f"{name} = [{' '.join(members)}];")
        return name


def _settle_rules(table: DecisionTable, registry: _ClassRegistry) -> list[str]:
    lines = []
    counters: dict[str, int] = {}
    for rule in table.rules:
        base = rule.input_glyph.replace(".", "_")
        index = counters.get(base, 0)
        counters[base] = index + 1
        parts = ["sub"]
        if rule.backtrack:
            parts.append(registry.ref(rule.backtrack, f"s_{base}_bk{index}"))
        parts.append(f"{rule.input_glyph}'")
        if rule.look1:
            parts.append(registry.ref(rule.look1, f"s_{base}_la1_{index}"))
        if rule.look2:
            parts.append(registry.ref(rule.look2, f"s_{base}_la2_{index}"))
        parts.append(f"by {rule.outcome};")
        comment = f"  # joint row" if rule.joint else ""
        lines.append("    " + " ".join(parts) + comment)
    return lines


def _curs_statements(table: DecisionTable, spec: SubsetSpec) -> dict[int, list[str]]:
    per_height: dict[int, dict[str, tuple[str, str]]] = {h: {} for h in CURS_HEIGHTS}
    for name in sorted(table.reachable_glyphs):
        record = spec.glyphs[name]
        for height in CURS_HEIGHTS:
            entry = None
            if record.entry is not None and record.entry[1] == height:
                entry = record.entry
            elif record.entry_curs_only is not None and record.entry_curs_only[1] == height:
                entry = record.entry_curs_only
            exit_anchor = record.exit if record.exit is not None and record.exit[1] == height else None
            if entry is None and exit_anchor is None:
                continue
            per_height[height][name] = (
                _anchor(*entry) if entry else "<anchor NULL>",
                _anchor(*exit_anchor) if exit_anchor else "<anchor NULL>",
            )
    for name, heights in spec.noentry_parity_heights.items():
        if name not in table.reachable_glyphs:
            continue
        for height in heights:
            per_height.setdefault(height, {}).setdefault(name, ("<anchor NULL>", "<anchor NULL>"))
    return {
        height: [
            f"        pos cursive {name} {entry} {exit};" for name, (entry, exit) in sorted(glyphs.items())
        ]
        for height, glyphs in per_height.items()
        if glyphs
    }


def _assert_invariants(table: DecisionTable, spec: SubsetSpec, fea: str) -> None:
    locked_or_chokepoint = {name for name in spec.glyphs if ".noentry" in name}
    named: set[str] = set()
    for rule in table.rules:
        for slot in (rule.look1, rule.look2):
            if not slot:
                continue
            leaked = set(slot) & locked_or_chokepoint
            if leaked:
                raise AssertionError(
                    f"locked twin or chokepoint output in a raw lookahead class: {sorted(leaked)}"
                )
        named.add(rule.input_glyph)
        named.add(rule.outcome)
        for slot in (rule.backtrack, rule.look1, rule.look2):
            if slot:
                named.update(slot)
    missing = {name for name in named if name not in spec.glyphs and name != "uni200C"}
    if missing:
        raise AssertionError(f"rules name glyphs outside the planned glyph set: {sorted(missing)}")
    if "ignore sub" in fea:
        raise AssertionError("selection-semantics ignore sub leaked into the emitted FEA")


def emit_fea(table: DecisionTable, spec: SubsetSpec = SPEC) -> str:
    registry = _ClassRegistry()
    live_members = list(spec.entry_bearing_families)
    for family in spec.entry_bearing_families:
        marker = spec.marker_families.get(family)
        if marker:
            live_members.append(marker)
    live_members.sort()
    locked_members = [f"{name}.noentry" for name in live_members]

    settle_lines = _settle_rules(table, registry)
    curs = _curs_statements(table, spec)

    parts: list[str] = []
    parts.append(
        "# Generated by prototype/emit.py — the PLAN.md section 4 transducer encoding. Do not hand-edit."
    )
    parts.append("")
    parts.extend(registry.definitions)
    parts.append(f"@proto_entry_live = [{' '.join(live_members)}];")
    parts.append(f"@proto_entry_locked = [{' '.join(locked_members)}];")
    parts.append("")
    formation_lines = [f"    sub {lead} {trail} by {ligature};" for lead, trail, ligature in spec.formation]
    parts.append("lookup proto_formation {\n" + "\n".join(formation_lines) + "\n} proto_formation;")
    marker_lines = [
        f"    sub {family} by {marker};" for family, marker in sorted(spec.marker_families.items())
    ]
    parts.append("\nlookup proto_ss03_marker {\n" + "\n".join(marker_lines) + "\n} proto_ss03_marker;")
    parts.append(
        "\nlookup proto_zwnj {\n    sub uni200C @proto_entry_live' by @proto_entry_locked;\n} proto_zwnj;"
    )
    parts.append("\nlookup proto_settle {\n" + "\n".join(settle_lines) + "\n} proto_settle;")
    parts.append(
        "\nfeature calt {\n    lookup proto_formation;\n    lookup proto_zwnj;\n    lookup proto_settle;\n} calt;"
    )
    parts.append("\nfeature ss03 {\n    lookup proto_ss03_marker;\n} ss03;")

    curs_blocks = []
    for height in CURS_HEIGHTS:
        if height not in curs:
            continue
        curs_blocks.append(
            f"    lookup cursive_y{height} {{\n" + "\n".join(curs[height]) + f"\n    }} cursive_y{height};"
        )
    parts.append("\nfeature curs {\n" + "\n".join(curs_blocks) + "\n} curs;")

    fea = "\n".join(parts) + "\n"
    _assert_invariants(table, spec, fea)
    return fea


if __name__ == "__main__":
    fea_text = emit_fea(build_table())
    out_dir = Path(__file__).resolve().parent / "out"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "Proto-preview.fea").write_text(fea_text)
    print(fea_text)
