"""Build the pipeline from any runes directory into any out dir and run the defect gates, so candidate policy records can be build-tested without touching glyph_data/runes/ or the shared engine/out/build/ artifacts.

Usage:
    uv run python engine/tools/scratch_build.py <runes_dir> <out_dir> [--configs a,b]

`--configs` narrows the table build to that list, in the order given (`request_configs`). Its names are the settlement configurations `engine.tools.kernel_all_configs.configs_from` accepts, checked before the spec is resolved and dumped. Without it, the build covers the settlement set. A run whose search reads only `default`'s rows can pass `--configs default` and pay for one configuration's enumeration. A narrowed build writes the same window enumerations and join tables as the whole set, but its settlement tables can differ, because the whole set's fold takes in the windows each configuration keeps live that another's rules would answer wrongly in the shipped order (`engine/kernel-rs/src/crossconfig.rs`), and a narrowed build has fewer configurations to take them from. A set that does not include `default` enumerates every member from scratch and saves much less.

The out dir may not resolve under engine/out/build (`scratch_out_dir`), whether or not the run is narrowed. A narrowed build there would rewrite the settlement and join files of the configurations it names and leave the others as the last whole build wrote them, because the crate deletes nothing. The directory's readers (`engine/review/tablediff.py` reads it as one build) cannot tell that mixed set from a whole one. A whole-set build there would rewrite every table and `Senior-Regular.otf` from the candidate's runes but leave the stamped window enumerations, because it passes no inputs stamp. The artifact cycle's run_build skip key hashes the repo's inputs and only checks that those files exist, so the next pass would build the review corpus over the candidate's font.

Prints a JSON line with the configurations built (`configs`) and the defect gate's errors (`defect_errors`). It follows `engine.pipeline.run_build.run()`, read-back included, with the spec as a parameter. `build_font` is the build and read-back without the defect gates, which `engine/tools/scaling_sweep.py` runs on each alphabet size of its series under `AMS_SCALING_FONTS=1`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine.pipeline import compile_font, conform, emit_gpos, emit_gsub, kern, readback
from engine.pipeline import run_build
from engine.pipeline.model import ResolvedSpec
from engine.pipeline.spec_load import (
    DEFAULT_REGISTRY_PATH,
    DEFAULT_SCHEMA_DIR,
    load_spec,
)
from engine.tools.kernel_all_configs import configs_from

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_OUT = REPO_ROOT / "engine" / "out" / "build"


def request_configs(requested: str) -> list[str]:
    """Return the configurations for the build. An empty request gives the settlement set, and a named request that list, in the order named."""
    if not requested:
        return list(conform.SETTLEMENT_CONFIGS)
    return list(configs_from(requested))


def scratch_out_dir(requested: str) -> Path:
    """Return the resolved out dir, and exit when it is under engine/out/build. `kernel_all_configs.scratch_out_dir` makes the opposite check: it requires one directory where this forbids one."""
    out_dir = Path(requested).resolve()
    if out_dir == BUILD_OUT or BUILD_OUT in out_dir.parents:
        raise SystemExit(
            f"refusing out_dir {out_dir}: a scratch build under {BUILD_OUT} rewrites the live settlement and join files and Senior-Regular.otf from the candidate's runes and leaves the stamped window enumerations standing, and the artifact cycle's run_build skip key cannot see that, so the next pass would build the review corpus over the candidate's font"
        )
    return out_dir


@dataclass(frozen=True)
class FontBuild:
    """One scratch build's tables, its settled cell glyphs, and its read-back report."""

    tables: dict[str, tuple]
    cell_glyphs: dict
    readback: dict
    kerned: kern.KernResolution | None = None


def build_font(spec: ResolvedSpec, out_dir: Path, build_configs: list[str]) -> FontBuild:
    """Build `spec`'s tables for `build_configs` into `out_dir`, resolve the kern records (`kern.run_stage`, which writes `kern.tsv` there), compile `Senior-Regular.otf` there with its kern lookup and without the full font's non-Quikscript glyphs and features (`compile_font.build_font` with no `FontSources`), which no Quikscript text reaches, read it back, and write `readback_summary.json` and `kern-classes.json`, as run_build's glyph chain does without its defect gates. Raises `readback.ReadbackError` after writing the report when read-back finds a divergence, and what the kern stage raises."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tables, _digests = run_build.build_tables(spec, out_dir, configs=build_configs)

    cell_glyphs = run_build.mint_cell_glyphs(spec, tables)
    bare, copies, ss10_copies = run_build.mint_raw_glyphs(spec)
    dots = run_build.namer_dot_glyphs()
    kerned = kern.run_stage(spec, tables, cell_glyphs, out_dir, ss10_copies)
    kern_plan = emit_gpos.kern_plan(kerned, cell_glyphs)

    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_copies=ss10_copies)
    gpos_fea = emit_gpos.emit_gpos({**cell_glyphs, **bare, **copies}, spec=spec, kern=kern_plan)
    fea = gsub_plan.fea_text + "\n" + gpos_fea
    all_glyphs = {**cell_glyphs, **bare, **copies, **dots}
    font_path = compile_font.build_font(
        all_glyphs, fea, out_dir / "Senior-Regular.otf", fea_path=out_dir / run_build.FEA_FILE
    )

    readback_report = readback.verify_font(
        font_path,
        gsub_plan,
        emit_gpos.cursive_registrations({**cell_glyphs, **bare, **copies}, spec=spec),
        kern=kern_plan,
    )
    (out_dir / "readback_summary.json").write_text(json.dumps(readback_report, indent=2) + "\n")
    if not readback_report["pass"]:
        raise readback.ReadbackError(
            f"{readback_report['divergence_count']} read-back divergence(s) between the scratch font and the plan; see {out_dir / 'readback_summary.json'}"
        )
    kern.write_classes(kerned, cell_glyphs, out_dir)
    return FontBuild(tables=tables, cell_glyphs=cell_glyphs, readback=readback_report, kerned=kerned)


def build_and_check(runes_dir: Path, out_dir: Path, build_configs: list[str]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = load_spec(runes_dir, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)
    built = build_font(spec, out_dir, build_configs)
    defect_report = run_build._run_defect_gates(spec, built.tables, built.cell_glyphs, built.kerned)
    return {
        "configs": build_configs,
        "defect_errors": [f"{d.code} {d.signature}: {d.message}" for d in defect_report.errors],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("runes_dir")
    ap.add_argument("out_dir")
    ap.add_argument(
        "--configs",
        default="",
        help=f"comma-separated settlement configurations to build, from {', '.join(conform.SETTLEMENT_CONFIGS)}; the default builds the settlement set; a set that does not name default enumerates from scratch and saves far less than `default` alone",
    )
    args = ap.parse_args(argv)
    build_configs = request_configs(args.configs)
    out_dir = scratch_out_dir(args.out_dir)
    runes_dir = Path(args.runes_dir).resolve()
    print(json.dumps(build_and_check(runes_dir, out_dir, build_configs)))


if __name__ == "__main__":
    main()
