"""Compile the prototype OTF and measure the GSUB budget (prototype/PLAN.md sections 1 and 5-6d).

Reuses the existing pipeline read-only: `build_font` and `_report_gsub_budget` from tools/build_font.py via the verified recipe in prototype/recon/pipeline.md section 5 (legacy `glyphs:` records only, empty `glyph_families`, hand-built FEA passed as `senior_fea` so the IR emitter never runs). The OTF lands at prototype/out/Proto.otf with the .fea sidecar written by build_font; this script also writes prototype/out/settlement.tsv and prototype/out/budget.json (measured numbers, the PLAN.md section 6d extrapolation with assumptions A1-A4, and the K1/K2 kill-criterion verdicts — K3 is semantic: prototype/conform.py and prototype/coretext_smoke.py each record their half into budget.json after they run).

When prototype/out/scale_stress.json exists (run prototype/scale_stress.py first), the K2 verdict is based on the at-scale measured compiles instead of the small-font bytes-per-rule arithmetic, which PLAN.md deviation 15 documents as unvalidatable at prototype scale. The small-scale arithmetic is still reported, marked superseded.

Run with: uv run python prototype/build.py
"""

from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

PROTOTYPE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROTOTYPE_DIR.parent
sys.path.insert(0, str(PROTOTYPE_DIR))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_font import _report_gsub_budget, build_font
from fontTools.ttLib import TTFont

from emit import emit_fea
from spec import SPEC
from table import build_table

OUT_DIR = PROTOTYPE_DIR / "out"
OTF_PATH = OUT_DIR / "Proto.otf"

METADATA = {
    "font_name": "Proto",
    "version": 1.0,
    "units_per_em": 550,
    "pixel_size": 50,
    "ascender": 550,
    "descender": -150,
    "cap_height": 400,
    "x_height": 300,
}

# PLAN.md section 6d kill criteria.
K1_RULE_CEILING = 10_000
K2_HEADROOM_FLOOR = 16_384
K2_SUBTABLE_WALL = 49_151
K2_CUMULATIVE_WALL = 65_535
CLASSDEF_CEILING_BYTES = (
    7_000  # A3: format-1 worst case, ~6 bytes per covered glyph over the full ~1,150-glyph set
)
FULL_ALPHABET_RUNES = 45
A1_PESSIMISM = 1.5


def _glyph_data(table) -> dict:
    glyphs: dict[str, dict] = {}
    for name in sorted(table.reachable_glyphs):
        record = SPEC.glyphs[name]
        key = f"{name}.prop" if name.startswith("qs") else name
        definition: dict = {}
        if record.bitmap:
            definition["bitmap"] = list(record.bitmap)
        if record.y_offset:
            definition["y_offset"] = record.y_offset
        if record.advance_width is not None:
            definition["advance_width"] = record.advance_width
            if record.bitmap == ():
                definition["bitmap"] = []
        glyphs[key] = definition
    return {
        "metadata": dict(METADATA),
        "glyphs": glyphs,
        "glyph_families": {},
        "context_sets": {},
        "kerning": {},
        "senior_kerning": [],
        "restore_isolated_form_overrides": [],
        "predecessor_demote_overrides": [],
        "trailing_demote_overrides": [],
    }


def _settle_lookup_metrics(font_path: Path) -> dict:
    """Re-derive the budget numbers from the compiled table so the section 6d extrapolation runs on numbers, not parsed stdout: raw GSUB bytes via the lazy reader, and the settlement (chained-context) lookup's compiled subtable bytes via OTTableWriter."""
    font = TTFont(str(font_path))
    try:
        entry = font.reader.tables["GSUB"]
        gsub_bytes = entry.length
        gsub = font["GSUB"].table
        lookups = gsub.LookupList.Lookup
        settle_index = None
        settle_subtables = 0
        for index, lookup in enumerate(lookups):
            if lookup.LookupType == 6 and lookup.SubTableCount > settle_subtables:
                settle_index = index
                settle_subtables = lookup.SubTableCount
        settle_bytes = None
        if settle_index is not None:
            from fontTools.ttLib.tables.otBase import OTTableWriter

            writer = OTTableWriter()
            lookups[settle_index].compile(writer, font)
            settle_bytes = len(writer.getAllData())
        return {
            "gsub_bytes": gsub_bytes,
            "lookup_count": len(lookups),
            "subtable_count": sum(lookup.SubTableCount for lookup in lookups),
            "settle_lookup_index": settle_index,
            "settle_lookup_subtables": settle_subtables,
            "settle_lookup_bytes": settle_bytes,
        }
    finally:
        font.close()


def _parse_budget_report(report: str) -> dict:
    parsed: dict = {}
    headroom = re.search(r"LookupList ([\d,]+) bytes, subtable ([\d,]+) bytes in lookup (\d+)", report)
    if headroom:
        parsed["lookuplist_offset_headroom"] = int(headroom.group(1).replace(",", ""))
        parsed["subtable_offset_headroom"] = int(headroom.group(2).replace(",", ""))
        parsed["tightest_lookup_index"] = int(headroom.group(3))
    return parsed


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    table = build_table()
    table.write_tsv(OUT_DIR / "settlement.tsv")
    fea = emit_fea(table, SPEC)

    glyph_data = _glyph_data(table)
    build_log = io.StringIO()
    with redirect_stdout(build_log):
        build_font(glyph_data, OTF_PATH, variant="senior", senior_fea=fea)
    sys.stdout.write(build_log.getvalue())

    budget_capture = io.StringIO()
    with redirect_stdout(budget_capture):
        _report_gsub_budget(OTF_PATH, fea)
    budget_report = budget_capture.getvalue()
    print("=== _report_gsub_budget ===")
    print(budget_report, end="")

    metrics = _settle_lookup_metrics(OTF_PATH)
    parsed = _parse_budget_report(budget_report)

    settle_rules = len(table.rules)
    settle_rules_pre_partition = table.raw_rule_count
    per_family_rules: dict[str, int] = {}
    for rule in table.rules:
        family = rule.input_glyph.split(".")[0]
        per_family_rules[family] = per_family_rules.get(family, 0) + 1
    core_family_rules = sum(
        count for family, count in per_family_rules.items() if family in ("qsIt", "qsTea", "qsMay")
    )

    settle_bytes = metrics["settle_lookup_bytes"] or 0
    bytes_per_rule = settle_bytes / settle_rules if settle_rules else 0.0
    rules_per_rune = settle_rules / 3
    projected_rules_full_font = rules_per_rune * FULL_ALPHABET_RUNES * A1_PESSIMISM
    projected_settle_bytes_full_font = projected_rules_full_font * bytes_per_rule + CLASSDEF_CEILING_BYTES
    largest_family_rules = max(per_family_rules.values()) if per_family_rules else 0
    projected_largest_family_subtable = (
        largest_family_rules * A1_PESSIMISM * bytes_per_rule + CLASSDEF_CEILING_BYTES
    )

    scale_stress_path = OUT_DIR / "scale_stress.json"
    scale_stress = json.loads(scale_stress_path.read_text()) if scale_stress_path.exists() else None

    k1_break_even_rules_per_rune = K1_RULE_CEILING / (FULL_ALPHABET_RUNES * A1_PESSIMISM)
    k1_tripped = projected_rules_full_font > K1_RULE_CEILING

    k2_small_scale_tripped = (
        projected_largest_family_subtable > K2_SUBTABLE_WALL
        or projected_settle_bytes_full_font > K2_CUMULATIVE_WALL
        or parsed.get("subtable_offset_headroom", K2_HEADROOM_FLOOR + 1) < K2_HEADROOM_FLOOR
    )
    if scale_stress is not None:
        # The binding K2 evidence is the at-scale compile of the projected rule count with partitioned (DFA-style, slot-disjoint) classes: it must compile on the primary path (no Extension promotion) with the uint16 subtable-offset headroom above the floor. The prototype-fraction class regime is the pessimistic bound and may legitimately need the sanctioned Extension escape hatch; an outright compile failure at projected scale trips K2 regardless of regime.
        projected_scenarios = {
            name: data
            for name, data in scale_stress["scenarios"].items()
            if name.startswith("projected_") and data.get("disjoint_slot_classes")
        }
        primary = projected_scenarios.get("projected_partitioned_modest", {})
        k2_tripped = (
            any(data.get("compile_failed") for data in projected_scenarios.values())
            or primary.get("extension_promotion_needed", True)
            or primary.get("subtable_offset_headroom", 0) < K2_HEADROOM_FLOOR
        )
        k2_basis = "measured at-scale compiles (prototype/out/scale_stress.json)"
    else:
        k2_tripped = k2_small_scale_tripped
        k2_basis = "small-scale bytes-per-rule arithmetic (scale_stress.json not found; run prototype/scale_stress.py)"

    budget = {
        "measured": {
            "settle_rules": settle_rules,
            "settle_rules_pre_partition": settle_rules_pre_partition,
            "compression_ratio": (
                round(settle_rules_pre_partition / settle_rules, 2) if settle_rules else None
            ),
            "two_lookahead_slot_rules": sum(1 for rule in table.rules if rule.look2),
            "joint_flagged_rules": sum(1 for rule in table.rules if rule.joint),
            "identity_guard_rules": table.identity_guard_rules,
            "zwnj_ignore_guards": table.ignore_guards_needed,
            "per_family_rules": per_family_rules,
            "fea_lines": fea.count("\n") + 1,
            "glyphs": len(table.reachable_glyphs),
            **metrics,
            **parsed,
            "report_text": budget_report.strip().splitlines(),
        },
        "extrapolation": {
            "assumptions": {
                "A1": "qsIt/qsTea/qsMay are upper-quartile accretion density; x1.5 pessimism applied on top of linear scaling. Empirical handle: scale_stress.json density_growth measures rules-per-family as the prototype alphabet grows",
                "A2": f"S45 = (S3 / 3) x {FULL_ALPHABET_RUNES} x {A1_PESSIMISM}; S3 counts all {settle_rules} emitted rules including the ligature/marker/locked-twin/encoding-probe rows ({core_family_rules} rows key on the three core families)",
                "A3": f"B45 = S45 x (B3 / S3) + ClassDef ceiling {CLASSDEF_CEILING_BYTES} bytes — SUPERSEDED by the at-scale measured compiles when scale_stress.json is present; kept for reference because 31-glyph coverage sharing does not transfer to ~1,150 glyphs (PLAN.md deviation 15)",
                "A4": "per-family subtable split (one `subtable;` break per family, format decided by fontTools per ruleset): the binding constraint is measured directly by the at-scale compiles",
            },
            "small_scale_arithmetic": {
                "superseded_by_scale_stress": scale_stress is not None,
                "bytes_per_rule": round(bytes_per_rule, 1),
                "projected_settle_bytes_full_font": round(projected_settle_bytes_full_font),
                "projected_largest_family_subtable_bytes": round(projected_largest_family_subtable),
            },
            "projected_rules_full_font": round(projected_rules_full_font),
            "scale_stress": (
                {
                    "parameters": scale_stress["parameters"],
                    "scenarios": scale_stress["scenarios"],
                    "density_growth": scale_stress["density_growth"],
                }
                if scale_stress is not None
                else "missing — run prototype/scale_stress.py"
            ),
        },
        "kill_criteria": {
            "K1_rule_count": {
                "ceiling": K1_RULE_CEILING,
                "projected": round(projected_rules_full_font),
                "measured_rules_per_rune_over_core_families": round(rules_per_rune, 1),
                "break_even_rules_per_rune": round(k1_break_even_rules_per_rune, 1),
                "margin": round(k1_break_even_rules_per_rune / rules_per_rune, 1),
                "tripped": k1_tripped,
            },
            "K2_offset_headroom": {
                "basis": k2_basis,
                "subtable_wall": K2_SUBTABLE_WALL,
                "cumulative_wall": K2_CUMULATIVE_WALL,
                "headroom_floor": K2_HEADROOM_FLOOR,
                "measured_subtable_headroom_prototype": parsed.get("subtable_offset_headroom"),
                "measured_subtable_headroom_at_projected_scale": (
                    scale_stress["scenarios"]["projected_partitioned_modest"].get("subtable_offset_headroom")
                    if scale_stress is not None
                    else None
                ),
                "extension_needed_at_projected_scale_modest_classes": (
                    scale_stress["scenarios"]["projected_partitioned_modest"].get(
                        "extension_promotion_needed"
                    )
                    if scale_stress is not None
                    else None
                ),
                "extension_needed_at_projected_scale_prototype_fraction_classes": (
                    scale_stress["scenarios"]["projected_partitioned_prototype_fraction"].get(
                        "extension_promotion_needed"
                    )
                    if scale_stress is not None
                    else None
                ),
                "small_scale_arithmetic_tripped": k2_small_scale_tripped,
                "tripped": k2_tripped,
            },
            "K3_semantics": {
                "criterion": "any HarfBuzz-vs-settle or CoreText-vs-HarfBuzz divergence attributable to within-lookup sequential substitution or default-ignorable handling that cannot be fixed inside the section 7 encoding",
                "harfbuzz": "pending — run prototype/conform.py against this build",
                "coretext": "pending — run prototype/coretext_smoke.py against this build",
            },
        },
    }
    (OUT_DIR / "budget.json").write_text(json.dumps(budget, indent=2) + "\n")
    print("=== budget verdicts ===")
    print(
        f"settle rules: {settle_rules} (pre-partition {settle_rules_pre_partition}), bytes/rule {bytes_per_rule:.1f}, settle lookup bytes {settle_bytes}"
    )
    print(
        f"projected full-font settlement rules: {projected_rules_full_font:.0f} -> K1 {'TRIPPED' if k1_tripped else 'ok'} (ceiling {K1_RULE_CEILING}; break-even density {k1_break_even_rules_per_rune:.0f} rules/rune vs measured {rules_per_rune:.1f})"
    )
    print(f"K2 {'TRIPPED' if k2_tripped else 'ok'} — basis: {k2_basis}")
    print(f"budget written to {OUT_DIR / 'budget.json'}")


if __name__ == "__main__":
    main()
