"""Build the M1 pipeline from any runes directory into any out dir and run the oracle, so candidate policy records can be build-tested without touching glyph_data/runes/ or the shared rebuild/out/m1/ artifacts.

Usage:
    uv run python rebuild/tools/scratch_build.py <runes_dir> <out_dir> [--configs a,b]

`--configs` narrows the table build and the oracle to the same list, in the order given (`request_configs`). Its names are the settlement configurations `rebuild.tools.kernel_all_configs.configs_from` accepts, checked before the spec is resolved and dumped. Without it, the build covers the settlement set and the oracle covers the acceptance set, so a plain run also checks the ss10 overlay. A narrowed run does not, because it can name only settlement configurations. A run whose search reads only `default`'s rows can pass `--configs default` and pay for one configuration's enumeration and one oracle walk. A set that does not include `default` enumerates every member from scratch and saves much less.

The out dir may not resolve under rebuild/out/m1 (`scratch_out_dir`), whether or not the run is narrowed. A narrowed build there would rewrite the settlement, treaty and memo files of the configurations it names and leave the others as the last whole build wrote them, because the crate deletes nothing. The directory's readers (`rebuild/review/tablediff.py` reads it as one build) cannot tell that mixed set from a whole one. A whole-set build there would rewrite every table, `M1.otf` and `divergence-audit.tsv` from the candidate's runes but leave the stamped window enumerations, because it passes no inputs stamp. The artifact cycle's run_m1 skip key hashes the repo's inputs and only checks that those files exist, so the next pass would build the review surface over the candidate's font and audit.

Prints a JSON line with the configurations built (`configs`), the configurations the oracle walked (`oracle_configs`, which a plain run's ss10 overlay makes the longer list), the defect gate's errors, the oracle's `rows_compared`, `divergent_rows`, `unmatched` and `multi_matched`, and the audit path. It follows `rebuild.pipeline.run_m1.run()` and `run_oracle()`, read-back included, with the spec as a parameter.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rebuild.pipeline import compile_font, conform, emit_gpos, emit_gsub, oracle, readback
from rebuild.pipeline import run_m1
from rebuild.pipeline.spec_load import (
    DEFAULT_REGISTRY_PATH,
    DEFAULT_SCHEMA_DIR,
    load_spec,
)
from rebuild.tools.kernel_all_configs import configs_from

REPO_ROOT = Path(__file__).resolve().parents[2]
M1_OUT = REPO_ROOT / "rebuild" / "out" / "m1"


def request_configs(requested: str) -> tuple[list[str], list[str]]:
    """Return the configurations for the build and the oracle, as `(build, oracle)`. An empty request gives the settlement set for the build and the acceptance set, which includes the ss10 overlay, for the oracle. A named request gives that one list for both, in the order named."""
    if not requested:
        return list(conform.SETTLEMENT_CONFIGS), list(conform.ACCEPTANCE_CONFIGS)
    tokens = configs_from(requested)
    return list(tokens), list(tokens)


def scratch_out_dir(requested: str) -> Path:
    """Return the resolved out dir, and exit when it is under rebuild/out/m1. `kernel_all_configs.scratch_out_dir` makes the opposite check: it requires one directory where this forbids one."""
    out_dir = Path(requested).resolve()
    if out_dir == M1_OUT or M1_OUT in out_dir.parents:
        raise SystemExit(
            f"refusing out_dir {out_dir}: a scratch build under {M1_OUT} rewrites the live settlement, treaty and memo files, M1.otf and divergence-audit.tsv from the candidate's runes and leaves the stamped window enumerations standing, and the artifact cycle's run_m1 skip key cannot see that, so the next pass would build the review surface over the candidate's font and audit"
        )
    return out_dir


def build_and_oracle(
    runes_dir: Path, out_dir: Path, build_configs: list[str], oracle_configs: list[str]
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    # The baseline (old-font) subset tables are independent of the rune edits under test, so seed them from the real out dir.
    for gz in M1_OUT.glob("baseline-*.subset.tsv.gz"):
        target = out_dir / gz.name
        if not target.exists():
            target.write_bytes(gz.read_bytes())
    spec = load_spec(runes_dir, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)
    tables, _digests = run_m1.build_tables(spec, out_dir, configs=build_configs)

    cell_glyphs = run_m1.mint_cell_glyphs(spec, tables)
    bare, twins, ss10_twins = run_m1.mint_raw_glyphs(spec)
    dots = run_m1.namer_dot_glyphs()

    defect_report = run_m1._run_defect_gates(spec, tables, cell_glyphs)

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
            f"{readback_report['divergence_count']} read-back divergence(s) between the scratch font and the plan; see {out_dir / 'readback_summary.json'}"
        )

    report = oracle.compare_against_baseline(
        spec,
        out_dir,
        REPO_ROOT / "rebuild" / "m1-aliases.yaml",
        REPO_ROOT / "rebuild" / "m1-divergences.yaml",
        configs=oracle_configs,
        out_dir=out_dir,
        font_path=out_dir / "M1.otf",
        kern_sidecar_path=REPO_ROOT / "glyph_data" / "senior_quikscript_kerning.yaml",
    )
    return {
        "configs": build_configs,
        "oracle_configs": oracle_configs,
        "defect_errors": [f"{d.code} {d.signature}: {d.message}" for d in defect_report.errors],
        "rows_compared": report.rows_compared,
        "divergent_rows": report.divergent_rows,
        "unmatched": report.unmatched_count,
        "multi_matched": report.multi_matched_count,
        "audit": str(out_dir / "divergence-audit.tsv"),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("runes_dir")
    ap.add_argument("out_dir")
    ap.add_argument(
        "--configs",
        default="",
        help=f"comma-separated settlement configurations to build and oracle, from {', '.join(conform.SETTLEMENT_CONFIGS)}; the default builds the settlement set and oracles the acceptance set; a set that does not name default enumerates from scratch and saves far less than `default` alone",
    )
    args = ap.parse_args(argv)
    build_configs, oracle_configs = request_configs(args.configs)
    out_dir = scratch_out_dir(args.out_dir)
    runes_dir = Path(args.runes_dir).resolve()
    print(json.dumps(build_and_oracle(runes_dir, out_dir, build_configs, oracle_configs)))


if __name__ == "__main__":
    main()
