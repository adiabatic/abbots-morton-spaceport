"""Scratch-build harness for lever hunts: build the M1 pipeline against an arbitrary runes_dir into an arbitrary out_dir and run the oracle, so candidate policy records can be build-tested in isolation without touching glyph_data/runes/ or the shared rebuild/out/m1/ artifacts.

Usage:
    uv run python rebuild/tools/scratch_build.py <runes_dir> <out_dir>

Prints a JSON line with oracle pass/unmatched/multi_matched/divergent_rows and the audit path. Mirrors rebuild.pipeline.run_m1.run() + run_oracle(), read-back included, parameterized by spec.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rebuild.pipeline import compile_font, conform, defects, emit_gpos, emit_gsub, oracle, readback, surface
from rebuild.pipeline import run_m1
from rebuild.pipeline.spec_load import (
    DEFAULT_REGISTRY_PATH,
    DEFAULT_SCHEMA_DIR,
    load_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_and_oracle(runes_dir: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    # The baseline (old-font) subset tables are independent of the rune edits under test, so seed them from the real out dir.
    real_out = REPO_ROOT / "rebuild" / "out" / "m1"
    for gz in real_out.glob("baseline-*.subset.tsv.gz"):
        target = out_dir / gz.name
        if not target.exists():
            target.write_bytes(gz.read_bytes())
    spec = load_spec(runes_dir, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)
    tables, _digests = run_m1.build_tables(spec, out_dir)

    cell_glyphs = run_m1.mint_cell_glyphs(spec, tables)
    bare, twins, ss10_twins = run_m1.mint_raw_glyphs(spec)
    dots = run_m1.namer_dot_glyphs()

    allow = frozenset(
        entry["signature"] for entry in yaml.safe_load(run_m1.CONTACT_ALLOW_YAML.read_text()) or ()
    )
    anchor_issues = surface.check_anchor_conventions(spec)
    defect_report = defects.run_gates(spec, tables, cell_glyphs, allow=allow)
    for issue in anchor_issues:
        defect_report.errors.append(
            defects.Defect("E-ANCHOR", f"convention:{issue.path}", f"{issue.file}: {issue.message}")
        )

    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_twins=ss10_twins)
    gpos_fea = emit_gpos.emit_gpos({**cell_glyphs, **bare, **twins}, spec=spec)
    fea = gsub_plan.fea_text + "\n" + gpos_fea
    all_glyphs = {**cell_glyphs, **bare, **twins, **dots}
    font_path = compile_font.build_mini_font(all_glyphs, fea, out_dir / "M1.otf")

    readback_report = readback.verify_font(
        font_path,
        gsub_plan,
        emit_gpos.cursive_registrations({**cell_glyphs, **bare, **twins}, spec=spec),
    )
    (out_dir / "readback_summary.json").write_text(json.dumps(readback_report, indent=2) + "\n")
    if not readback_report["pass"]:
        raise readback.ReadbackError(
            f"{len(readback_report['divergences'])} read-back divergence(s) between the scratch font and the plan; see {out_dir / 'readback_summary.json'}"
        )

    report = oracle.compare_against_baseline(
        spec,
        out_dir,
        REPO_ROOT / "rebuild" / "m1-aliases.yaml",
        REPO_ROOT / "rebuild" / "m1-divergences.yaml",
        out_dir=out_dir,
        font_path=out_dir / "M1.otf",
        kern_sidecar_path=REPO_ROOT / "glyph_data" / "senior_quikscript_kerning.yaml",
    )
    return {
        "defect_errors": [f"{d.code} {d.signature}: {d.message}" for d in defect_report.errors],
        "rows_compared": report.rows_compared,
        "divergent_rows": report.divergent_rows,
        "unmatched": report.unmatched_count,
        "multi_matched": len(report.multi_matched),
        "audit": str(out_dir / "divergence-audit.tsv"),
    }


def main() -> None:
    runes_dir = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve()
    print(json.dumps(build_and_oracle(runes_dir, out_dir)))


if __name__ == "__main__":
    main()
