"""Scale-stress measurement for the PLAN.md section 6d budget extrapolation (PLAN.md deviation 15).

The problem this closes: the original extrapolation multiplied a bytes-per-rule figure measured on a 31-glyph font (where fontTools shares the small duplicate coverage tables aggressively) by a rule count projected from a closed 6-symbol alphabet, and asserted the x1.5 density pessimism — three load-bearing numbers, none validated at the scale where they matter. This module replaces the byte arithmetic with measurement at the destination scale and gives the density assumption its only available empirical handle:

1. **At-scale compiles.** Synthesizes fonts with the full-font glyph count (~1,150 glyphs across 45 families) and a settlement lookup in the exact section 7 row shape — chained-context single substitutions through feaLib, the same compiler path build.py uses — at the projected full-font rule count and at the K1 ceiling (10,000 rules), under two class-size regimes: `prototype-fraction` scales the prototype's class-member-to-glyph-set ratio up (large, scattered, range-hostile classes), `modest` uses fixed 60-member classes. The rule-shape mix (backtrack/lookahead/two-slot/boundary shares) is copied from the real prototype table at runtime. Measured outputs: real GSUB bytes, the settlement lookup's compiled bytes, uint16 offset headroom from the same binary walk `_report_gsub_budget` does, and whether fontTools had to promote lookups to GSUB type 7 Extension to resolve offset overflows (the escape hatch that counts against the primary path per PLAN.md section 6d).

2. **Density growth.** Rebuilds the decision table over reduced alphabets ({qsIt, qsTea}, then + qsMay, then + qsOy and the ligature) and reports rules-per-family at each size — the only measurable signal for whether per-rune rule density grows or shrinks as the alphabet widens, which is what the A1 x1.5 factor is standing in for.

Run as: uv run python prototype/scale_stress.py

Writes prototype/out/scale_stress.json; build.py folds the results into budget.json and bases the K2 verdict on the at-scale measurements when this file is present.
"""

from __future__ import annotations

import io
import json
import random
import struct
import sys
from contextlib import redirect_stdout
from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROTOTYPE_DIR.parent
sys.path.insert(0, str(PROTOTYPE_DIR))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_font import _report_gsub_budget
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph

import spec as spec_module
from build import _parse_budget_report
from table import build_table

OUT_DIR = PROTOTYPE_DIR / "out"

FULL_ALPHABET_RUNES = 45
CELLS_PER_FAMILY = 24
K1_RULE_CEILING = 10_000
A1_PESSIMISM = 1.5
SEED = 0xE650


def _rule_shape_mix(rules) -> dict:
    total = len(rules)
    return {
        "rules": total,
        "backtrack_share": sum(1 for rule in rules if rule.backtrack) / total,
        "look1_share": sum(1 for rule in rules if rule.look1) / total,
        "look2_share": sum(1 for rule in rules if rule.look2) / total,
        "boundary_class_share": sum(1 for rule in rules if rule.look1 == ("uni200C", "space")) / total,
    }


def _class_fraction(rules, glyph_count: int) -> float:
    sizes = [
        len(slot)
        for rule in rules
        for slot in (rule.backtrack, rule.look1, rule.look2)
        if slot and len(slot) > 1
    ]
    return (sum(sizes) / len(sizes)) / glyph_count


def _slot_classes(
    family: str,
    kind: str,
    class_count: int,
    class_size: int,
    all_cells: list[str],
    rng: random.Random,
    disjoint: bool,
) -> tuple[list[str], list[str]]:
    """Class definitions for one slot of one family's ruleset. `disjoint` mirrors the real emitter: outcome-partition compression produces a partition of each slot's filler set, so within one family's subtable the slot classes never overlap — which is exactly what lets fontTools express the ruleset as a format-2 class subtable. The overlapping regime is the hostile bound: random overlapping classes are unrepresentable as ClassDefs and force one format-3 subtable per rule."""
    names, lines = [], []
    if disjoint:
        pool = list(all_cells)
        rng.shuffle(pool)
        size = min(class_size, len(pool) // class_count)
        for index in range(class_count):
            members = sorted(pool[index * size : (index + 1) * size])
            name = f"@{kind}_{family}_{index}"
            lines.append(f"{name} = [{' '.join(members)}];")
            names.append(name)
    else:
        for index in range(class_count):
            members = sorted(rng.sample(all_cells, class_size))
            name = f"@{kind}_{family}_{index}"
            lines.append(f"{name} = [{' '.join(members)}];")
            names.append(name)
    return names, lines


def _synthesize(
    rule_count: int, class_size: int, disjoint: bool, mix: dict, rng: random.Random
) -> tuple[object, list[str]]:
    families = [f"f{index:02d}" for index in range(FULL_ALPHABET_RUNES)]
    cells = {family: [f"{family}.c{cell:02d}" for cell in range(CELLS_PER_FAMILY)] for family in families}
    all_cells = [name for family in families for name in cells[family]]
    glyph_order = [".notdef", "space", "uni200C"] + families + all_cells

    mean = rule_count / FULL_ALPHABET_RUNES
    per_family = [round(mean)] * FULL_ALPHABET_RUNES
    per_family[0] = round(mean * A1_PESSIMISM)
    while sum(per_family) > rule_count:
        per_family[per_family.index(max(per_family[1:]))] -= 1
    while sum(per_family) < rule_count:
        per_family[1 + rng.randrange(FULL_ALPHABET_RUNES - 1)] += 1

    class_lines: list[str] = ["@boundary = [uni200C space];"]
    family_blocks: list[list[str]] = []
    for family, family_rules in zip(families, per_family):
        class_count = max(2, family_rules // 5)
        backtrack_names, bk_lines = _slot_classes(
            family, "bk", class_count, class_size, all_cells, rng, disjoint
        )
        lookahead_names, la_lines = _slot_classes(
            family, "la", class_count, class_size, all_cells, rng, disjoint
        )
        class_lines.extend(bk_lines + la_lines)
        block: list[str] = []
        for index in range(family_rules):
            outcome = cells[family][index % CELLS_PER_FAMILY]
            parts = ["    sub"]
            if rng.random() < mix["backtrack_share"]:
                parts.append(rng.choice(backtrack_names))
            parts.append(f"{family}'")
            if rng.random() < mix["look1_share"]:
                if rng.random() < mix["boundary_class_share"]:
                    parts.append("@boundary")
                else:
                    parts.append(rng.choice(lookahead_names))
                if rng.random() < mix["look2_share"]:
                    parts.append(rng.choice(lookahead_names))
            parts.append(f"by {outcome};")
            block.append(" ".join(parts))
        family_blocks.append(block)

    # The section 7 size valve, authored the way the real emitter would: one `subtable;` break per family. fontTools sizes each ruleset independently (and gets to pick format 2 class subtables per ruleset where the classes permit), which is exactly A4's per-family-subtable assumption — without the breaks, the whole lookup is one ruleset and every candidate format overflows long before the design scale.
    rule_lines: list[str] = []
    for index, block in enumerate(family_blocks):
        if index:
            rule_lines.append("    subtable;")
        rule_lines.extend(block)

    def render(use_extension: bool) -> str:
        header = "lookup scale_settle useExtension {" if use_extension else "lookup scale_settle {"
        return "\n".join(
            class_lines
            + ["", header]
            + rule_lines
            + ["} scale_settle;", "", "feature calt {", "    lookup scale_settle;", "} calt;", ""]
        )

    return render, glyph_order


def _base_font(glyph_order: list[str]) -> FontBuilder:
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({0x0020: "space", 0x200C: "uni200C"})
    builder.setupGlyf({name: Glyph() for name in glyph_order})
    builder.setupHorizontalMetrics({name: (500, 0) for name in glyph_order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "ScaleStress", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    return builder


def _compile_and_measure(render, glyph_order: list[str], out_path: Path) -> dict:
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
    from fontTools.feaLib.error import FeatureLibError

    # feaLib never promotes a lookup to GSUB type 7 Extension on its own: a plain lookup whose subtable offsets cannot fit uint16 fails the build outright. The plain compile is attempted first because fitting without Extension is the section 7 primary path; the useExtension retry is the sanctioned escape hatch, and needing it is recorded and counts against the primary path (PLAN.md section 6d, K2).
    from fontTools.ttLib.tables.otBase import OTLOffsetOverflowError

    fea = render(False)
    extension_requested = False
    try:
        builder = _base_font(glyph_order)
        try:
            addOpenTypeFeaturesFromString(builder.font, fea)
        except FeatureLibError as error:
            if "overflow" not in str(error).lower():
                raise
            extension_requested = True
            fea = render(True)
            builder = _base_font(glyph_order)
            addOpenTypeFeaturesFromString(builder.font, fea)
        builder.save(str(out_path))
    except (FeatureLibError, OTLOffsetOverflowError, struct.error) as error:
        # A terminal overflow is a measurement, not a harness bug: this scenario's shape does not fit the encoding even with the Extension escape hatch.
        return {
            "glyphs": len(glyph_order),
            "compile_failed": True,
            "extension_promotion_needed": extension_requested,
            "error": f"{type(error).__name__}: {error}",
        }

    font = TTFont(str(out_path))
    try:
        gsub_bytes = font.reader.tables["GSUB"].length
        lookups = font["GSUB"].table.LookupList.Lookup
        extension_lookups = sum(1 for lookup in lookups if lookup.LookupType == 7)
        subtable_count = max(lookup.SubTableCount for lookup in lookups)
    finally:
        font.close()

    report = io.StringIO()
    with redirect_stdout(report):
        _report_gsub_budget(out_path, fea)
    parsed = _parse_budget_report(report.getvalue())

    # bytes/rule comes from the whole compiled GSUB (the settlement lookup is the only nontrivial content), measured on the packed binary the shaper actually reads — re-compiling the lookup standalone is not meaningful once the save-time overflow resolver has packed shared tables across the table.
    rule_lines = fea.count("    sub ")
    return {
        "glyphs": len(glyph_order),
        "fea_rule_lines": rule_lines,
        "gsub_bytes": gsub_bytes,
        "settle_lookup_subtables": subtable_count,
        "bytes_per_rule": round(gsub_bytes / rule_lines, 1),
        "extension_lookups": extension_lookups,
        "extension_promotion_needed": extension_requested or extension_lookups > 0,
        **parsed,
    }


def _reduced_spec(families: tuple[str, ...]) -> spec_module.SubsetSpec:
    keep = set(families) | {"space", "zwnj"}
    return spec_module.SubsetSpec(
        codepoint_to_token={
            cp: token for cp, token in spec_module.CODEPOINT_TO_TOKEN.items() if token in keep
        },
        families={name: fam for name, fam in spec_module.FAMILIES.items() if name in families},
        formation=tuple(
            entry for entry in spec_module.FORMATION if {entry[0], entry[1], entry[2]} <= set(families)
        ),
        marker_families={
            fam: marker for fam, marker in spec_module.MARKER_FAMILIES.items() if fam in families
        },
        entry_bearing_families=tuple(fam for fam in spec_module.ENTRY_BEARING_FAMILIES if fam in families),
    )


def _density_growth() -> list[dict]:
    steps = (
        ("qsIt+qsTea", ("qsIt", "qsTea")),
        ("qsIt+qsTea+qsMay", ("qsIt", "qsTea", "qsMay")),
        ("full subset (+qsOy, +qsTea_qsOy)", ("qsIt", "qsTea", "qsMay", "qsTea_qsOy", "qsOy")),
    )
    rows = []
    for label, families in steps:
        reduced = _reduced_spec(families)
        table = build_table(reduced, reduced.feature_configurations)
        rows.append(
            {
                "alphabet": label,
                "letter_families": len(families),
                "rules": len(table.rules),
                "rules_per_family": round(len(table.rules) / len(families), 2),
            }
        )
    for index in range(1, len(rows)):
        previous, current = rows[index - 1], rows[index]
        current["density_growth_vs_previous"] = round(
            current["rules_per_family"] / previous["rules_per_family"], 3
        )
    return rows


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    table = build_table()
    mix = _rule_shape_mix(table.rules)
    prototype_class_fraction = _class_fraction(table.rules, len(table.reachable_glyphs))
    glyph_count_at_scale = 3 + FULL_ALPHABET_RUNES * (1 + CELLS_PER_FAMILY)

    projected_rules = round(len(table.rules) / 3 * FULL_ALPHABET_RUNES * A1_PESSIMISM)
    class_regimes = {
        "partitioned_prototype_fraction": (
            max(2, round(prototype_class_fraction * glyph_count_at_scale)),
            True,
        ),
        "partitioned_modest": (60, True),
        "overlapping_modest": (60, False),
    }

    scenarios = {}
    for rules_label, rule_count in (("projected", projected_rules), ("k1_ceiling", K1_RULE_CEILING)):
        for class_label, (class_size, disjoint) in class_regimes.items():
            label = f"{rules_label}_{class_label}"
            rng = random.Random(SEED)
            render, glyph_order = _synthesize(rule_count, class_size, disjoint, mix, rng)
            out_path = OUT_DIR / f"ScaleStress-{label}.ttf"
            print(f"compiling {label}: {rule_count} rules, {class_size}-member classes ...")
            measured = _compile_and_measure(render, glyph_order, out_path)
            measured["rule_count"] = rule_count
            measured["class_size"] = class_size
            measured["disjoint_slot_classes"] = disjoint
            scenarios[label] = measured
            if measured.get("compile_failed"):
                print(f"  COMPILE FAILED ({measured['error']})")
            else:
                print(
                    f"  GSUB {measured['gsub_bytes']:,} B ({measured['bytes_per_rule']} B/rule), "
                    f"subtable headroom {measured.get('subtable_offset_headroom', 'n/a')}, "
                    f"extension promotion: {measured['extension_promotion_needed']}"
                )

    result = {
        "parameters": {
            "full_alphabet_runes": FULL_ALPHABET_RUNES,
            "cells_per_family": CELLS_PER_FAMILY,
            "glyphs_at_scale": glyph_count_at_scale,
            "rule_shape_mix_from_prototype_table": mix,
            "prototype_class_member_fraction": round(prototype_class_fraction, 4),
            "class_regimes": class_regimes,
            "seed": SEED,
        },
        "scenarios": scenarios,
        "density_growth": _density_growth(),
    }
    (OUT_DIR / "scale_stress.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nscale stress written to {OUT_DIR / 'scale_stress.json'}")
    for row in result["density_growth"]:
        print(
            f"density: {row['alphabet']}: {row['rules']} rules, {row['rules_per_family']} per family"
            + (
                f" (x{row['density_growth_vs_previous']} vs previous)"
                if "density_growth_vs_previous" in row
                else ""
            )
        )


if __name__ == "__main__":
    main()
