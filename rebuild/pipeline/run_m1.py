"""The M1 integration driver (M1-PLAN Phase 5): the full pipeline run over the real rune files, writing every section 8 artifact under rebuild/out/m1/.

Stages: load_default_spec -> per-configuration decision/treaty tables (partition + E-STRANDED asserted, TSVs written, and the window enumeration serialized under the fingerprint of the sources it came from, so `--conform-only` replays it rather than rebuilding the fixpoint) -> glyph inventory minting (settled cells named by the table's own cell labels, plus the raw cmap glyphs, marker twins, chokepoint twins, and the namer dot pair) -> defects gates (run_gates merged with surface.check_anchor_conventions) -> emit_gsub/emit_gpos -> build_mini_font with the budget gate.

The glyph-name contract this driver pins: settlement-lookup outcomes are `settle.cell_label` names, so the decision-table rules and the compiled glyph set agree by construction; the raw cmap glyph for each rune is the bare rune name drawn as the isolated cell but carrying no curs anchors; marker, chokepoint, and ss10 twins reuse the bare drawing (under ss10 the pre-empt lookup substitutes every letter's cmap glyph by its anchor-free `.ss10` twin before formation, so no ligature ever forms, nothing settles, each letter keeps its own cluster, and every seam is a break).

Run as: uv run python -m rebuild.pipeline.run_m1
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping

import yaml

from rebuild.pipeline import (
    baseline_subset,
    compile_font,
    conform,
    defects,
    emit_gpos,
    emit_gsub,
    fingerprint,
    geometry,
    manual_pins,
    surface,
)
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import (
    CellId,
    GlyphRecord,
    ResolvedSpec,
    locked_glyph_name,
    relevant_marker_features,
    ss10_twin_name,
)
from rebuild.pipeline import settle as settle_module
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
PUNCTUATION_YAML = REPO_ROOT / "glyph_data" / "punctuation.yaml"
CONTACT_ALLOW_YAML = REPO_ROOT / "rebuild" / "m1-contact-allow.yaml"
ALIAS_YAML = REPO_ROOT / "rebuild" / "m1-aliases.yaml"
DIVERGENCES_YAML = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
KERN_SIDECAR_YAML = REPO_ROOT / "glyph_data" / "senior_quikscript_kerning.yaml"

RAW_STANCE = "cmap"


def _spawn_pool(jobs: int) -> ProcessPoolExecutor:
    workers = min(jobs, len(conform.ACCEPTANCE_CONFIGS))
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


def _persist_tables(decision, treaty, out_dir: Path | None, inputs: str | None):
    """Write one configuration's artifacts, in whichever process built them, and hand back the table the parent actually reads. Given the fingerprint that names its sources, the window enumeration goes to disk for the conformance sweep to load and is dropped from the returned table: a million rows per configuration is a pickle across the pool boundary and a resident peak that nothing after the build spends."""
    if out_dir is None:
        return decision
    decision.write_tsv(out_dir / f"settlement-{decision.config}.tsv")
    treaty.write_tsv(out_dir / f"treaties-{decision.config}.tsv")
    if inputs is None:
        return decision
    table_module.write_windows(decision, table_module.windows_path(out_dir, decision.config), inputs)
    return replace(decision, transitions=())


def _build_tables_worker(
    spec: ResolvedSpec, config: str, out_dir: Path | None, inputs: str | None
) -> tuple[str, object, object]:
    features = conform.features_for_config(config)
    decision, treaty = table_module.build_tables(spec, features)
    decision.assert_outcome_partition()
    decision.assert_e_stranded()
    return config, _persist_tables(decision, treaty, out_dir, inputs), treaty


def build_tables(
    spec: ResolvedSpec, out_dir: Path | None = None, jobs: int = 1, inputs: str | None = None
) -> dict[str, tuple]:
    """Every acceptance configuration's decision and treaty tables, built here or fanned out over `jobs` workers, with the section 8 TSVs written under `out_dir` by whichever process built them.

    `inputs` is `fingerprint.tables_value` over the sources this spec was loaded from. Supplying it alongside `out_dir` serializes each configuration's window enumeration next to the TSVs — where `run_font_conformance` picks it up instead of rebuilding the fixpoint — and drops those windows from the tables returned here, since only the rules, the reachable cells and the fired provenance are read after the build; `table.read_windows` gets them back. Omit it and the tables come back whole, which is what a caller building a spec of its own must do: the fingerprint names the repo's rune files and cannot vouch for tables they did not produce.
    """
    if jobs > 1:
        collected: dict[str, tuple] = {}
        with _spawn_pool(jobs) as pool:
            futures = {
                pool.submit(_build_tables_worker, spec, config, out_dir, inputs): config
                for config in conform.ACCEPTANCE_CONFIGS
            }
            for future in as_completed(futures):
                config, decision, treaty = future.result()
                collected[config] = (decision, treaty)
                print(f"[t] build_tables[{config}] done", flush=True)
        return {config: collected[config] for config in conform.ACCEPTANCE_CONFIGS}
    tables: dict[str, tuple] = {}
    for config in conform.ACCEPTANCE_CONFIGS:
        start = time.perf_counter()
        features = conform.features_for_config(config)
        decision, treaty = table_module.build_tables(spec, features)
        decision.assert_outcome_partition()
        decision.assert_e_stranded()
        tables[config] = (_persist_tables(decision, treaty, out_dir, inputs), treaty)
        print(f"[t] build_tables[{config}] {time.perf_counter() - start:.1f}s", flush=True)
    return tables


def mint_cell_glyphs(
    spec: ResolvedSpec, tables: Mapping[str, DecisionTable | tuple[DecisionTable, ...]]
) -> dict[CellId, GlyphRecord]:
    cells: set[CellId] = set()
    for entry in tables.values():
        decision = entry[0] if isinstance(entry, (tuple, list)) else entry
        cells.update(cell for cell in decision.reachable_cells() if cell.rune in spec.runes)
    glyphs: dict[CellId, GlyphRecord] = {}
    for cell in sorted(cells, key=lambda c: cell_label(spec, c)):
        plan = surface.resolve_cell(spec, cell)
        name = cell_label(spec, cell)
        if len(name.encode()) > geometry.MAX_GLYPH_NAME_BYTES:
            raise RuntimeError(f"cell label {name!r} exceeds {geometry.MAX_GLYPH_NAME_BYTES} bytes")
        glyphs[cell] = geometry.realize(spec, plan, name=name)
    return glyphs


def mint_raw_glyphs(
    spec: ResolvedSpec,
) -> tuple[dict[CellId, GlyphRecord], dict[CellId, GlyphRecord], dict[str, str]]:
    """Returns (bare cmap glyphs, marker + chokepoint + ss10 twins, the raw-name → ss10-twin-name map for the ss10 pre-empt lookup). Raw glyphs are keyed under the synthetic stance so they never collide with a reachable settled cell that happens to be the isolated cell. Only codepoint-bearing letter runes get ss10 twins: ligature runes never appear in a cmap buffer, and boundary tokens are not runes."""
    bare: dict[CellId, GlyphRecord] = {}
    twins: dict[CellId, GlyphRecord] = {}
    ss10_twins: dict[str, str] = {}
    for rune_name, rune in spec.runes.items():
        isolated = geometry.isolated_cell(spec, rune_name)
        record = geometry.realize(spec, surface.resolve_cell(spec, isolated), name=rune_name)
        stripped = replace(record, entry=None, exit=None, entry_curs_only=None, safety_checks=())
        key = CellId(rune_name, RAW_STANCE, None, None, ())
        bare[key] = stripped

        if not rune.sequence and rune.codepoint is not None:
            twin_name = ss10_twin_name(rune_name)
            twins[CellId(rune_name, RAW_STANCE, None, None, ("ss10",))] = replace(stripped, name=twin_name)
            ss10_twins[rune_name] = twin_name

        live_names = [rune_name]
        for marker_name in emit_gsub.marker_states(rune_name, relevant_marker_features(rune)):
            twins[CellId(marker_name, RAW_STANCE, None, None, ())] = replace(stripped, name=marker_name)
            live_names.append(marker_name)
        if any(stance.surface.entries for stance in rune.stances.values()):
            for raw_name in live_names:
                twin_name = locked_glyph_name(raw_name)
                twins[CellId(rune_name, RAW_STANCE, None, None, ("locked", raw_name))] = replace(
                    stripped, name=twin_name
                )
    return bare, twins, ss10_twins


def namer_dot_glyphs() -> dict[CellId, GlyphRecord]:
    raw = yaml.safe_load(PUNCTUATION_YAML.read_text())["glyphs"]
    records: dict[CellId, GlyphRecord] = {}
    for name in ("periodcentered", "periodcentered.lowered"):
        definition = raw[f"{name}.prop"]
        records[CellId(name, RAW_STANCE, None, None, ())] = GlyphRecord(
            name=name,
            bitmap=tuple(definition["bitmap"]),
            y_offset=definition.get("y_offset", 0),
        )
    return records


def run(
    out_dir: Path = OUT_DIR, spec: ResolvedSpec | None = None, jobs: int = 1, inputs: str | None = None
) -> dict:
    """`inputs` is `fingerprint.tables_value` over the sources `spec` was loaded from, snapshotted before the load so it can only ever name content the tables are at least as new as. Supplying it serializes the window enumeration under `out_dir` for the conformance sweep; a caller running a spec of its own leaves it out."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    if spec is None:
        spec = load_default_spec()
    print(f"[t] spec_load {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    tables = build_tables(spec, out_dir, jobs=jobs, inputs=inputs)
    print(f"[t] build_tables_total {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    cell_glyphs = mint_cell_glyphs(spec, tables)
    bare, twins, ss10_twins = mint_raw_glyphs(spec)
    dots = namer_dot_glyphs()
    print(f"[t] glyph_minting {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    allow = frozenset(entry["signature"] for entry in yaml.safe_load(CONTACT_ALLOW_YAML.read_text()) or ())
    anchor_issues = surface.check_anchor_conventions(spec)
    defect_report = defects.run_gates(spec, tables, cell_glyphs, allow=allow)
    for issue in anchor_issues:
        defect_report.errors.append(
            defects.Defect("E-ANCHOR", f"convention:{issue.path}", f"{issue.file}: {issue.message}")
        )
    print(f"[t] defect_gates {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_twins=ss10_twins)
    gpos_fea = emit_gpos.emit_gpos({**cell_glyphs, **bare, **twins}, spec=spec)
    fea = gsub_plan.fea_text + "\n" + gpos_fea
    print(f"[t] emit_gsub_gpos {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    all_glyphs = {**cell_glyphs, **bare, **twins, **dots}
    font_path = compile_font.build_mini_font(all_glyphs, fea, out_dir / "M1.otf")
    print(f"[t] compile_font {time.perf_counter() - start:.1f}s", flush=True)
    (out_dir / "M1.generated.fea").write_text(fea)

    summary = {
        "configs": list(tables),
        "rules_per_config": {config: len(decision.rules) for config, (decision, _treaty) in tables.items()},
        "settled_cell_glyphs": len(cell_glyphs),
        "total_glyphs": len(all_glyphs),
        "gsub_rule_count": gsub_plan.rule_count,
        "defect_errors": [f"{d.code} {d.signature}: {d.message}" for d in defect_report.errors],
        "defect_flags": [f"{d.code} {d.signature}: {d.message}" for d in defect_report.flags],
        "dead_in_alphabet": sorted(defect_report.dead_in_alphabet),
        "deferred_partner": sorted(defect_report.deferred_partner),
        "notes": defect_report.notes,
        "font": str(font_path),
    }
    (out_dir / "pipeline_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    fingerprint.write_stage_a(REPO_ROOT, out_dir)
    return summary


def serialized_tables(out_dir: Path, inputs: str) -> dict[str, DecisionTable] | None:
    """Every acceptance configuration's decision table as the build stage left it under `out_dir`, minus the window enumeration — or None the moment one file is missing, unreadable, or was written from sources other than the ones `inputs` names. Nothing partial: a mixed set would sweep some configurations against tables the runes on disk no longer produce."""
    tables: dict[str, DecisionTable] = {}
    for config in conform.ACCEPTANCE_CONFIGS:
        try:
            stamp, decision = table_module.read_windows(
                table_module.windows_path(out_dir, config), windows=False
            )
        except OSError, ValueError:
            return None
        if stamp != inputs:
            return None
        tables[config] = decision
    return tables


def tables_inputs() -> str:
    """The stamp serialized windows carry: `fingerprint.tables_value` plus a token per semantics-mode default that is on (the simulated prospect, the stage-4b shifted vote slots). The environment flags change settlement semantics without moving any hashed source, so without the tokens a flag-on enumeration would read as fresh to a flag-off process (and the reverse) and the sweep would replay tables the in-process kernel no longer produces."""
    inputs = fingerprint.tables_value(REPO_ROOT)
    if settle_module.SIMULATED_PROSPECT_DEFAULT:
        inputs = f"{inputs}+simulated-prospect"
    if settle_module.VOTE_SLOTS_DEFAULT:
        inputs = f"{inputs}+vote-slots"
    return inputs


def run_font_conformance(out_dir: Path = OUT_DIR, max_length: int = 5, jobs: int = 1) -> dict:
    """The exhaustive font-vs-settle sweep. The tables it replays are the ones the build stage already produced from these same sources, read back per configuration; only when that fingerprint fails to match does the fixpoint run again here, which is the standalone case of a sweep against a font whose runes have since moved. Two carried-forward proofs cut the spend: the boundary gate's summary, when green for exactly this M1.otf, hands the sweep its structural checks within the proven horizon, and each config's recorded witness winners (`witnesses-<config>.tsv.gz` beside the windows) spare a hunt whose table did not move."""
    inputs = tables_inputs()
    spec = load_default_spec()
    start = time.perf_counter()
    serialized = serialized_tables(out_dir, inputs)
    windows: dict[str, Path] | None = None
    rebuilt: dict[str, tuple] | None = None
    if serialized is not None:
        decisions: Mapping[str, DecisionTable | tuple[DecisionTable, ...]] = serialized
        windows = {config: table_module.windows_path(out_dir, config) for config in serialized}
        print(f"[t] load_tables {time.perf_counter() - start:.1f}s", flush=True)
    else:
        rebuilt = build_tables(spec, jobs=jobs)
        decisions = rebuilt
        print(f"[t] build_tables_total {time.perf_counter() - start:.1f}s", flush=True)
    cell_glyphs = mint_cell_glyphs(spec, decisions)
    boundary_horizon = conform.proven_boundary_horizon(
        out_dir / "M1.otf", out_dir / "boundary_equivalence_summary.json"
    )
    witness_caches = {
        config: conform.witnesses_path(out_dir, config) for config in conform.ACCEPTANCE_CONFIGS
    }
    if jobs > 1:
        collected: dict[str, conform.ConformanceConfigResult] = {}
        with _spawn_pool(jobs) as pool:
            futures = {
                pool.submit(
                    conform.conformance_config_worker,
                    spec,
                    out_dir / "M1.otf",
                    config,
                    max_length,
                    cell_glyphs,
                    decision=None if rebuilt is None else rebuilt[config][0],
                    windows_path=None if windows is None else windows[config],
                    boundary_horizon=boundary_horizon,
                    witness_cache_path=witness_caches[config],
                ): config
                for config in conform.ACCEPTANCE_CONFIGS
            }
            for future in as_completed(futures):
                result = future.result()
                collected[result.config] = result
        ordered = [collected[config] for config in conform.ACCEPTANCE_CONFIGS]
        report = conform.merge_conformance_results(out_dir / "M1.otf", ordered)
        report.write(out_dir / "conform_summary.json")
    else:
        report = conform.run_conformance(
            out_dir / "M1.otf",
            spec,
            glyphs=cell_glyphs,
            max_length=max_length,
            out_dir=out_dir,
            tables=rebuilt,
            windows=windows,
            boundary_horizon=boundary_horizon,
            witness_caches=witness_caches,
        )
    summary = {
        "sequences": report.sequences,
        "shaping_runs": report.shaping_runs,
        "divergences": len(report.divergences),
        "uncovered_rules": report.uncovered_rules,
        "uncovered_transitions": report.uncovered_transitions,
        "topped_up_rules": report.topped_up_rules,
        "topped_up_sequences": report.topped_up_sequences,
        "pass": report.passed,
        "notes": report.notes,
    }
    for divergence in report.divergences[:20]:
        summary.setdefault("divergence_exemplars", []).append(
            f"{divergence.config} {':'.join(f'{ord(ch):04X}' for ch in divergence.text)} position {divergence.position} [{divergence.kind}] expected {divergence.expected} got {divergence.got}"
        )
    return summary


def run_boundary_gate(
    out_dir: Path = OUT_DIR, max_length: int = 5, spec: ResolvedSpec | None = None, jobs: int = 1
) -> dict:
    if spec is None:
        spec = load_default_spec()
    if jobs > 1:
        collected: dict[str, conform.BoundaryConfigResult] = {}
        with _spawn_pool(jobs) as pool:
            futures = {
                pool.submit(
                    conform.boundary_config_worker, spec, out_dir / "M1.otf", config, max_length
                ): config
                for config in conform.ACCEPTANCE_CONFIGS
            }
            for future in as_completed(futures):
                result = future.result()
                collected[result.config] = result
        ordered = [collected[config] for config in conform.ACCEPTANCE_CONFIGS]
        report = conform.merge_boundary_results(out_dir / "M1.otf", ordered, max_length=max_length)
        report.write(out_dir / "boundary_equivalence_summary.json")
    else:
        report = conform.run_boundary_equivalence(
            out_dir / "M1.otf", spec, max_length=max_length, out_dir=out_dir
        )
    summary = {
        "sequences": report.sequences,
        "shaping_runs": report.shaping_runs,
        "divergences": len(report.divergences),
        "pass": not report.divergences,
    }
    for divergence in report.divergences[:20]:
        summary.setdefault("divergence_exemplars", []).append(
            f"{divergence.config} {':'.join(f'{ord(ch):04X}' for ch in divergence.text)} position {divergence.position} [{divergence.kind}] expected {divergence.expected} got {divergence.got}"
        )
    return summary


def run_manual_pin_gate(out_dir: Path = OUT_DIR, spec: ResolvedSpec | None = None) -> dict:
    if spec is None:
        spec = load_default_spec()
    report = manual_pins.run_gate(out_dir / "M1.otf", spec)
    summary = manual_pins.summarize(report)
    (out_dir / "manual_pins_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def run_oracle(
    out_dir: Path = OUT_DIR, spec: ResolvedSpec | None = None, jobs: int = 1, hoist: bool = True
) -> dict:
    if spec is None:
        spec = load_default_spec()
    for config in ("ss06", "ss07", "ss06+ss07"):
        conform.assert_subset_identity(out_dir, config)
    if jobs > 1:
        collected: dict[str, conform.OracleConfigResult] = {}
        with _spawn_pool(jobs) as pool:
            futures = {
                pool.submit(
                    conform.oracle_config_worker,
                    spec,
                    out_dir,
                    ALIAS_YAML,
                    DIVERGENCES_YAML,
                    config,
                    out_dir / "M1.otf",
                    KERN_SIDECAR_YAML,
                ): config
                for config in conform.ACCEPTANCE_CONFIGS
            }
            for future in as_completed(futures):
                result = future.result()
                collected[result.config] = result
        ordered = [collected[config] for config in conform.ACCEPTANCE_CONFIGS]
        report, audit_lines = conform.merge_oracle_results(ordered)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "divergence-audit.tsv").write_text("\n".join(audit_lines) + "\n")
    else:
        report = conform.compare_against_baseline(
            spec,
            out_dir,
            ALIAS_YAML,
            DIVERGENCES_YAML,
            out_dir=out_dir,
            font_path=out_dir / "M1.otf",
            kern_sidecar_path=KERN_SIDECAR_YAML,
            hoist=hoist,
        )
    summary = {
        "rows_compared": report.rows_compared,
        "divergent_rows": report.divergent_rows,
        "positions_compared": report.positions_compared,
        "positions_excluded": report.positions_excluded,
        "counts_by_entry": dict(sorted(report.counts_by_entry.items())),
        "unmatched": len(report.unmatched),
        "multi_matched": len(report.multi_matched),
        "subset_identity": ["ss06", "ss07", "ss06+ss07"],
        "pass": report.passed,
        "notes": report.notes,
    }
    for row in report.unmatched[:20]:
        summary.setdefault("unmatched_exemplars", []).append(
            f"{row.config} {row.codepoints} {'|'.join(row.baseline_glyphs)} -> {'|'.join(row.new_cells)} {row.phenomena}"
        )
    (out_dir / "oracle_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _settle_green(green_path: Path, key: str, ok: bool, recompute: Callable[[], str], label: str) -> None:
    """Shared last-green bookkeeping, on the discipline rebuild.tools.make_test_gate established: the key is snapshotted before the work, rechecked after, and recorded only when it still matches — inputs edited mid-run describe content that was never tested. A red result whose key still matches the record deletes it, since the green it claims is contradicted. Recording here is what lets the artifact cycle skip work an interactive run already proved."""
    from rebuild.tools.artifact_cycle import clear_contradicted_green, record_green

    if not ok:
        clear_contradicted_green(green_path, key)
        return
    if recompute() != key:
        print(f"{label}: green, but its inputs changed while it ran — green not recorded", flush=True)
        return
    record_green(green_path, key)
    where = green_path.relative_to(REPO_ROOT) if green_path.is_relative_to(REPO_ROOT) else green_path
    print(f"{label}: green — fingerprint recorded in {where}", flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the M1 integration pipeline and its Phase-2 gates.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="worker budget for build_tables and the oracle/boundary/conformance shards; 1 = serial",
    )
    parser.add_argument(
        "--conform-only",
        action="store_true",
        help="run only the font-vs-settle conformance sweep against the existing M1.otf and exit nonzero unless it passes",
    )
    parser.add_argument(
        "--conform-horizon",
        type=int,
        default=5,
        help="exhaustive sweep length for --conform-only; witnesses complete rule/transition coverage beyond it",
    )
    args = parser.parse_args(argv)
    jobs = args.jobs if args.jobs and args.jobs > 1 else 1

    if args.conform_only:
        from rebuild.tools.artifact_cycle import (
            CONFORM_GREEN,
            conform_skip_fingerprint,
            evaluate_conform_gate,
        )

        def conform_key() -> str:
            return conform_skip_fingerprint(REPO_ROOT, args.conform_horizon)

        before = conform_key()
        start = time.perf_counter()
        conformance = run_font_conformance(max_length=args.conform_horizon, jobs=jobs)
        print(f"[t] run_font_conformance {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(conformance, indent=2))
        status, _ = evaluate_conform_gate(conformance)
        _settle_green(CONFORM_GREEN, before, status == "green", conform_key, "gate:conform")
        if not conformance["pass"]:
            raise SystemExit("font conformance failed; see conform_summary.json")
        return

    from rebuild.tools.artifact_cycle import RUN_M1_GREEN, evaluate_run_m1_gate, run_m1_skip_fingerprint

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(REPO_ROOT)

    start = time.perf_counter()
    refiltered = baseline_subset.ensure_fresh(REPO_ROOT)
    print(
        f"[t] baseline_subset {time.perf_counter() - start:.1f}s ({'refiltered' if refiltered else 'fresh'})",
        flush=True,
    )
    inputs = tables_inputs()
    spec = load_default_spec()
    before = run_m1_key()
    try:
        start = time.perf_counter()
        summary = run(spec=spec, jobs=jobs, inputs=inputs)
        print(f"[t] run_total {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(summary, indent=2))
        if summary["defect_errors"]:
            raise SystemExit(f"{len(summary['defect_errors'])} defect-gate errors; see pipeline_summary.json")
        start = time.perf_counter()
        boundary_gate = run_boundary_gate(spec=spec, jobs=jobs)
        print(f"[t] run_boundary_gate {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(boundary_gate, indent=2))
        if not boundary_gate["pass"]:
            raise SystemExit("boundary-equals-text-edge gate failed; see boundary_equivalence_summary.json")
        start = time.perf_counter()
        pin_gate = run_manual_pin_gate(spec=spec)
        print(f"[t] run_manual_pin_gate {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(pin_gate, indent=2))
        if not pin_gate["pass"]:
            raise SystemExit("Manual-pin gate failed; see manual_pins_summary.json")
        start = time.perf_counter()
        oracle = run_oracle(spec=spec, jobs=jobs)
        print(f"[t] run_oracle {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(oracle, indent=2))
    except SystemExit:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        raise
    gate = evaluate_run_m1_gate(summary, boundary_gate, pin_gate, oracle)
    _settle_green(RUN_M1_GREEN, before, gate.ok, run_m1_key, "run_m1")
    if not oracle["pass"]:
        raise SystemExit("oracle conformance failed; see oracle_summary.json and divergence-audit.tsv")


if __name__ == "__main__":
    main()
