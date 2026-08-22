"""The M1 integration driver (M1-PLAN Phase 5): the full pipeline run over the real rune files, writing every section 8 artifact under rebuild/out/m1/.

Stages: load_default_spec -> per-configuration decision/treaty tables (partition + E-STRANDED asserted, TSVs written, and the window enumeration serialized under the fingerprint of the sources it came from, so `--conform-only` mints its glyph inventory from it and refuses to run against a stale or missing one) -> glyph inventory minting (settled cells named by the table's own cell labels, plus the raw cmap glyphs, marker twins, chokepoint twins, and the namer dot pair) -> defects gates (run_gates merged with surface.check_anchor_conventions) -> emit_gsub/emit_gpos (whose plan also enumerates the emitted lookup's HarfBuzz-facing shapes into behavior_classes.json, the arming key rebuild/tools/deep_sweep.py reads) -> build_mini_font with the budget gate -> read-back (the font just written, re-parsed from its own bytes and structurally proven against the plan the emitters held, and that plan's settlement rows recorded beside the summary with their per-configuration sources for the witness gate to count coverage over; rebuild/pipeline/readback.py).

The glyph-name contract this driver pins: settlement-lookup outcomes are `settle.cell_label` names, so the decision-table rules and the compiled glyph set agree by construction; the raw cmap glyph for each rune is the bare rune name drawn as the isolated cell but carrying no curs anchors; marker, chokepoint, and ss10 twins reuse the bare drawing (under ss10 the pre-empt lookup substitutes every letter's cmap glyph by its anchor-free `.ss10` twin before formation, so no ligature ever forms, nothing settles, each letter keeps its own cluster, and every seam is a break).

The ZWNJ-structure and split-buffer checks that once had a standalone horizon-5 gate of their own now ride gate:conform's belt, so they are proven per build at horizon 4 and periodically at 5 or deeper by `make conform-deep` — the same charter the belt already has, over a rule whose closure property makes a horizon-4 proof cover every window the oracle absorbs.

Run as: uv run python -m rebuild.pipeline.run_m1 — or `--conform-only` for the belt alone against the M1.otf on disk, or `--gates-only` for the Manual-pin gate and the oracle against it, which is the cheap way to re-adjudicate a ledger or classifier edit without rebuilding a thing.
"""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, NoReturn

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
    kernel_exec,
    kernel_io,
    manual_pins,
    readback,
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
from rebuild.tools.peak_rss import process_peak_rss_bytes, rss_token

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "rebuild" / "out" / "m1"
PUNCTUATION_YAML = REPO_ROOT / "glyph_data" / "punctuation.yaml"
CONTACT_ALLOW_YAML = REPO_ROOT / "rebuild" / "m1-contact-allow.yaml"
ALIAS_YAML = REPO_ROOT / "rebuild" / "m1-aliases.yaml"
DIVERGENCES_YAML = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
KERN_SIDECAR_YAML = REPO_ROOT / "glyph_data" / "senior_quikscript_kerning.yaml"

RAW_STANCE = "cmap"

TABLE_DIGESTS_FORMAT = "ams-m1-table-digests/2"


def _spawn_pool(jobs: int) -> ProcessPoolExecutor:
    workers = min(jobs, len(conform.ACCEPTANCE_CONFIGS))
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


def _persist_tables(decision, treaty, out_dir: Path | None, inputs: str | None):
    """Write one configuration's artifacts, in whichever process built them, and hand back the table the parent actually reads plus the contract digest of the pair. Given the fingerprint that names its sources, the window enumeration goes to disk for the conformance sweep to load and is dropped from the returned table: a million rows per configuration is a pickle across the pool boundary and a resident peak that nothing after the build spends. The digest is taken here, between the writers and that drop, because `table.table_digest` covers those window rows; a caller with nowhere to write gets None rather than the several seconds a digest nothing will read would cost."""
    if out_dir is None:
        return decision, None
    decision.write_tsv(out_dir / f"settlement-{decision.config}.tsv")
    treaty.write_tsv(out_dir / f"treaties-{decision.config}.tsv")
    digest = table_module.table_digest(decision, treaty)
    if inputs is None:
        return decision, digest
    table_module.write_windows(decision, table_module.windows_path(out_dir, decision.config), inputs)
    return replace(decision, transitions=()), digest


def build_tables(
    spec: ResolvedSpec,
    out_dir: Path | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
) -> dict[str, tuple]:
    """Every acceptance configuration's decision and treaty tables: the resolved spec dumped once, every configuration answered by one `enumerate-configs` process, and each stream folded back through the Python half a configuration at a time. The kernel crate is the only fixpoint there is (issue 78), so there is nothing to choose between here and nothing an artifact could disagree with; `kernel_exec.build_tables` is the same work for one configuration in memory, which is what a test or a hand-assembled spec builds through.

    `out_dir`, when given, gets the section 8 TSVs and `table-digests.json` — each configuration's `table.table_digest`, taken while the window rows are still in hand, which is the grain the rest of the rebuild states table identity at. A caller with nowhere to write gets the tables and nothing else.

    `inputs` is `fingerprint.tables_value` over the sources this spec was loaded from. Supplying it alongside `out_dir` serializes each configuration's window enumeration next to the TSVs — where `run_font_conformance` picks it up rather than rebuilding anything — and drops those windows from the tables returned here, since only the rules, the reachable cells and the fired provenance are read after the build; `table.read_windows` gets them back. Omit it and the tables come back whole, which is what a caller building a spec of its own must do: the fingerprint names the repo's rune files and cannot vouch for tables they did not produce.

    Threads are capped at the configuration count and the CPU count because the kernel caps them there anyway, and defaulted low because the ceiling is memory rather than CPU — every configuration in flight holds its whole working set until it has emitted.
    """
    configs = conform.ACCEPTANCE_CONFIGS
    threads = max(
        1,
        min(kernel_threads or kernel_exec.KERNEL_THREADS_DEFAULT, len(configs), os.process_cpu_count() or 1),
    )
    kernel_exec.ensure_built()
    tables: dict[str, tuple] = {}
    digests: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        start = time.perf_counter()
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        streams = kernel_exec.enumerate_configs(
            spec_path, directory / "streams", configs, threads=threads, timings=True
        )
        print(f"[t] kernel_enumerate_configs {time.perf_counter() - start:.1f}s", flush=True)
        for config in configs:
            start = time.perf_counter()
            product = kernel_exec.read_stream(streams[config], directory)
            decision, treaty = table_module.assemble_tables(spec, product)
            decision.assert_outcome_partition()
            decision.assert_e_stranded()
            persisted, digest = _persist_tables(decision, treaty, out_dir, inputs)
            tables[config] = (persisted, treaty)
            if digest is not None:
                digests[config] = digest
            print(f"[t] assemble_tables[{config}] {time.perf_counter() - start:.1f}s", flush=True)
    if out_dir is not None:
        _write_table_digests(out_dir, inputs, digests)
    return tables


def _write_table_digests(out_dir: Path, inputs: str | None, digests: dict[str, str]) -> None:
    """The per-configuration contract digests a build leaves beside its tables, in acceptance order under the same stamp the windows heads carry. `table.table_digest` is the grain the rest of the rebuild states table identity at — the ordered rules with their provenance, every enumerated window row, the treaty rows, the reachable cells, the cited provenance and the identity guards — so a comparison of two builds is made against these rather than against the TSVs alone, which drop most of that. It has to be written at build time: the digest covers rows `_persist_tables` drops on its way out, and recovering one afterwards would cost the fixpoint that produced it."""
    payload = {"format": TABLE_DIGESTS_FORMAT, "inputs": inputs, "digests": digests}
    (out_dir / "table-digests.json").write_text(json.dumps(payload, indent=2) + "\n")


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
    out_dir: Path = OUT_DIR,
    spec: ResolvedSpec | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
) -> dict:
    """`inputs` is `fingerprint.tables_value` over the sources `spec` was loaded from, snapshotted before the load so it can only ever name content the tables are at least as new as. Supplying it serializes the window enumeration under `out_dir` for the conformance sweep; a caller running a spec of its own leaves it out. `kernel_threads` reaches the table build and nothing else."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    if spec is None:
        spec = load_default_spec()
    print(f"[t] spec_load {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    tables = build_tables(spec, out_dir, inputs=inputs, kernel_threads=kernel_threads)
    print(
        f"[t] build_tables_total {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
        flush=True,
    )

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
    curs_glyphs = {**cell_glyphs, **bare, **twins}
    gsub_plan = emit_gsub.emit_gsub(spec, tables, glyphs={**cell_glyphs, **bare}, ss10_twins=ss10_twins)
    classes = emit_gsub.behavior_classes(gsub_plan)
    (out_dir / "behavior_classes.json").write_text(
        json.dumps(
            {"format": emit_gsub.BEHAVIOR_CLASSES_FORMAT, "classes": list(classes)},
            indent=2,
        )
        + "\n"
    )
    gpos_fea = emit_gpos.emit_gpos(curs_glyphs, spec=spec)
    fea = gsub_plan.fea_text + "\n" + gpos_fea
    print(f"[t] emit_gsub_gpos {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    all_glyphs = {**curs_glyphs, **dots}
    font_path = compile_font.build_mini_font(all_glyphs, fea, out_dir / "M1.otf")
    print(f"[t] compile_font {time.perf_counter() - start:.1f}s", flush=True)
    (out_dir / "M1.generated.fea").write_text(fea)

    start = time.perf_counter()
    readback_report = readback.verify_font(
        font_path, gsub_plan, emit_gpos.cursive_registrations(curs_glyphs, spec=spec)
    )
    (out_dir / "readback_summary.json").write_text(json.dumps(readback_report, indent=2) + "\n")
    readback.write_settle_fold(
        readback.settle_fold_path(out_dir), gsub_plan, inputs, readback_report["pass"], font=font_path
    )
    print(f"[t] readback {time.perf_counter() - start:.1f}s", flush=True)
    if not readback_report["pass"]:
        raise readback.ReadbackError(
            f"{len(readback_report['divergences'])} read-back divergence(s) between the compiled font and the plan; see {out_dir / 'readback_summary.json'}"
        )

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
    """The stamp serialized windows carry: `fingerprint.tables_value` plus a token per semantics-mode default that is on (the simulated prospect, the stage-4b shifted vote slots, the issue-26 class-grain deep slots). The environment flags change settlement semantics or enumeration grain without moving any hashed source, so without the tokens a flag-on enumeration would read as fresh to a flag-off process (and the reverse) and the sweep would replay tables the in-process kernel no longer produces."""
    inputs = fingerprint.tables_value(REPO_ROOT)
    if settle_module.SIMULATED_PROSPECT_DEFAULT:
        inputs = f"{inputs}+simulated-prospect"
    if settle_module.VOTE_SLOTS_DEFAULT:
        inputs = f"{inputs}+vote-slots"
    if kernel_exec.class_grain():
        inputs = f"{inputs}+deep-classes"
    return inputs


def run_font_conformance(
    out_dir: Path = OUT_DIR,
    max_length: int = 4,
    jobs: int = 1,
    summary_name: str = "conform_summary.json",
) -> dict:
    """The exhaustive font-vs-settle sweep — the per-edit belt at `max_length` 4, and the same sweep deeper when rebuild.tools.deep_sweep asks for it under its own `summary_name`. The tables the build stage left under `out_dir` are read back here for one reason only, the glyph inventory `mint_cell_glyphs` needs to name settled cells and read their anchors; the sweep itself takes no table, because what it proves is HarfBuzz's behavior against the kernel's, and read-back already proved the font holds the rules the build planned. A stamp that fails to match is a hard stop rather than a rebuild: the enumeration costs a whole kernel fan-out, and a sweep that quietly built its own inventory would be measuring a font against runes that have since moved. The ZWNJ and split-buffer structural checks ride this sweep now, on every text that carries a boundary."""
    inputs = tables_inputs()
    spec = load_default_spec()
    start = time.perf_counter()
    serialized = serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; the sweep no longer rebuilds the fixpoint in process"
        )
    decisions: Mapping[str, DecisionTable | tuple[DecisionTable, ...]] = serialized
    print(f"[t] load_tables {time.perf_counter() - start:.1f}s", flush=True)
    cell_glyphs = mint_cell_glyphs(spec, decisions)
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
                ): config
                for config in conform.ACCEPTANCE_CONFIGS
            }
            for future in as_completed(futures):
                result = future.result()
                collected[result.config] = result
        ordered = [collected[config] for config in conform.ACCEPTANCE_CONFIGS]
        report = conform.merge_conformance_results(out_dir / "M1.otf", ordered)
        report.write(out_dir / summary_name)
    else:
        report = conform.run_conformance(
            out_dir / "M1.otf",
            spec,
            glyphs=cell_glyphs,
            max_length=max_length,
            out_dir=out_dir,
            summary_name=summary_name,
        )
    summary = {
        "sequences": report.sequences,
        "shaping_runs": report.shaping_runs,
        "divergences": len(report.divergences),
        "pass": report.passed,
        "notes": report.notes,
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


def manual_pin_gate_failure(summary: Mapping) -> str | None:
    """Why the Manual-pin gate does not count as passed, or None. `pass` alone is `not disagreements`, which a gate that replayed nothing satisfies vacuously — so the scope is part of the verdict here: the pins have to have been in scope and every one of them actually replayed against the font."""
    if not summary.get("pass"):
        return f"Manual-pin gate failed ({len(summary.get('disagreements') or [])} disagreements)"
    in_scope = summary.get("pins_in_scope") or 0
    replayed = summary.get("replayed") or 0
    if in_scope < 1:
        return "Manual-pin gate passed with no pins in scope, which proves nothing about the font"
    if replayed != in_scope:
        return f"Manual-pin gate replayed {replayed} of {in_scope} pins in scope"
    return None


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


def run_gates_only(out_dir: Path = OUT_DIR, jobs: int = 1) -> None:
    """The two post-build gates over artifacts already on disk: the Manual-pin replay and the oracle, rewriting their summaries and `divergence-audit.tsv` without recompiling anything. What licenses the reuse is the stamp the build left on its serialized enumerations — it names the sources those tables came from, so a stamp that still matches the runes on disk says the M1.otf beside them is the font those runes describe, and a stamp that does not is a refusal rather than a silent sweep of a stale binary. This writes no green record: run_m1's green covers the whole build, and a pass that recompiled nothing has not earned it."""
    inputs = tables_inputs()
    font_path = out_dir / "M1.otf"
    if serialized_tables(out_dir, inputs) is None:
        raise SystemExit(
            f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; --gates-only re-runs the gates over a build, it does not make one"
        )
    if not font_path.is_file():
        raise SystemExit(
            f"no compiled font at {font_path} — run `uv run python -m rebuild.pipeline.run_m1` first"
        )
    spec = load_default_spec()

    start = time.perf_counter()
    pin_gate = run_manual_pin_gate(out_dir=out_dir, spec=spec)
    print(f"[t] run_manual_pin_gate {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(pin_gate, indent=2))
    pin_failure = manual_pin_gate_failure(pin_gate)
    if pin_failure is not None:
        raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")

    start = time.perf_counter()
    oracle = run_oracle(out_dir=out_dir, spec=spec, jobs=jobs)
    print(f"[t] run_oracle {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(oracle, indent=2))
    if not oracle["pass"]:
        raise SystemExit("oracle conformance failed; see oracle_summary.json and divergence-audit.tsv")


def _settle_green(
    green_path: Path,
    key: str,
    ok: bool,
    recompute: Callable[[], str],
    label: str,
    files_of: Callable[[], dict[str, str]] | None = None,
) -> None:
    """Shared last-green bookkeeping, on the discipline rebuild.tools.make_test_gate established: the key is snapshotted before the work, rechecked after, and recorded only when it still matches — inputs edited mid-run describe content that was never tested. A red result whose key still matches the record deletes it, since the green it claims is contradicted. Recording here is what lets the artifact cycle skip work an interactive run already proved; `files_of` supplies the per-file digest lines behind the key, so a later skip miss can name which input moved."""
    from rebuild.tools.artifact_cycle import clear_contradicted_green, record_green

    if not ok:
        clear_contradicted_green(green_path, key)
        return
    if recompute() != key:
        print(f"{label}: green, but its inputs changed while it ran — green not recorded", flush=True)
        return
    record_green(green_path, key, files=files_of() if files_of is not None else None)
    where = green_path.relative_to(REPO_ROOT) if green_path.is_relative_to(REPO_ROOT) else green_path
    print(f"{label}: green — fingerprint recorded in {where}", flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the M1 integration pipeline and its Phase-2 gates.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="worker budget for the oracle and conformance shards, one process per acceptance configuration; 1 = serial. The table build's own width is --kernel-threads.",
    )
    parser.add_argument(
        "--conform-only",
        action="store_true",
        help="run only the font-vs-settle conformance sweep against the existing M1.otf and exit nonzero unless it passes",
    )
    parser.add_argument(
        "--gates-only",
        action="store_true",
        help="re-run the Manual-pin gate and the oracle against the M1.otf and tables already on disk, rewriting their summaries and divergence-audit.tsv; refuses when those tables were built from other sources than the ones on disk",
    )
    parser.add_argument(
        "--conform-horizon",
        type=int,
        default=4,
        help="exhaustive sweep length for --conform-only (the per-edit belt); `make conform-deep` runs the same sweep deeper on demand",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        default=None,
        help=f"how many configurations the kernel enumerates at once, capped at the configuration count and the CPU count (default {kernel_exec.KERNEL_THREADS_DEFAULT}, which AMS_KERNEL_THREADS overrides); the ceiling is memory rather than CPU",
    )
    args = parser.parse_args(argv)
    jobs = args.jobs if args.jobs and args.jobs > 1 else 1

    if args.gates_only:
        run_gates_only(out_dir=OUT_DIR, jobs=jobs)
        return

    if args.conform_only:
        from rebuild.tools.artifact_cycle import (
            CONFORM_GREEN,
            conform_skip_fingerprint,
            conform_skip_files,
            evaluate_conform_gate,
        )

        def conform_key() -> str:
            return conform_skip_fingerprint(REPO_ROOT, args.conform_horizon)

        before = conform_key()
        start = time.perf_counter()
        conformance = run_font_conformance(max_length=args.conform_horizon, jobs=jobs)
        print(
            f"[t] run_font_conformance {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
        print(json.dumps(conformance, indent=2))
        _, conform_failures = evaluate_conform_gate(conformance)
        _settle_green(
            CONFORM_GREEN,
            before,
            not conform_failures,
            conform_key,
            "gate:conform",
            files_of=lambda: conform_skip_files(REPO_ROOT, args.conform_horizon),
        )
        if not conformance["pass"]:
            raise SystemExit("font conformance failed; see conform_summary.json")
        return

    from rebuild.tools.artifact_cycle import (
        RUN_M1_GREEN,
        evaluate_run_m1_gate,
        run_m1_skip_files,
        run_m1_skip_fingerprint,
    )

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(REPO_ROOT)

    start = time.perf_counter()
    refiltered = baseline_subset.ensure_fresh(REPO_ROOT)
    print(
        f"[t] baseline_subset {time.perf_counter() - start:.1f}s ({'refiltered' if refiltered else 'fresh'})",
        flush=True,
    )
    start = time.perf_counter()
    missing_aliases = conform.unaliased_subset_names(OUT_DIR, ALIAS_YAML)
    print(f"[t] alias_completeness {time.perf_counter() - start:.1f}s", flush=True)
    if missing_aliases:
        listing = "\n".join(f"  {name} ({', '.join(configs)})" for name, configs in missing_aliases.items())
        raise SystemExit(
            f"rebuild/m1-aliases.yaml is missing {len(missing_aliases)} old glyph names that appear in subset baseline rows — every oracle number would be quietly wrong, so author each entry (or map it to the literal `pending` to run anyway with those rows unaliased):\n{listing}"
        )
    inputs = tables_inputs()
    spec = load_default_spec()
    before = run_m1_key()
    try:
        start = time.perf_counter()
        summary = run(spec=spec, inputs=inputs, kernel_threads=args.kernel_threads)
        print(
            f"[t] run_total {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
        print(json.dumps(summary, indent=2))
        if summary["defect_errors"]:
            raise SystemExit(f"{len(summary['defect_errors'])} defect-gate errors; see pipeline_summary.json")
        start = time.perf_counter()
        pin_gate = run_manual_pin_gate(spec=spec)
        print(f"[t] run_manual_pin_gate {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(pin_gate, indent=2))
        pin_failure = manual_pin_gate_failure(pin_gate)
        if pin_failure is not None:
            raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")
        start = time.perf_counter()
        oracle = run_oracle(spec=spec, jobs=jobs)
        print(f"[t] run_oracle {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(oracle, indent=2))
    except (SystemExit, readback.ReadbackError, emit_gsub.EmitError) as error:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        if isinstance(error, SystemExit):
            raise
        raise SystemExit(str(error))
    gate = evaluate_run_m1_gate(summary, pin_gate, oracle)
    _settle_green(
        RUN_M1_GREEN, before, gate.ok, run_m1_key, "run_m1", files_of=lambda: run_m1_skip_files(REPO_ROOT)
    )
    if not oracle["pass"]:
        raise SystemExit("oracle conformance failed; see oracle_summary.json and divergence-audit.tsv")


def _hard_exit(status: int) -> NoReturn:
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)


def _run_cli() -> None:
    try:
        main()
    except SystemExit as error:
        if error.code is None:
            status = 0
        elif isinstance(error.code, int):
            status = error.code
        else:
            sys.stdout.flush()
            print(error.code, file=sys.stderr)
            status = 1
        _hard_exit(status)
    _hard_exit(0)


if __name__ == "__main__":
    # This batch is short-lived, and its large live heap contains almost no cyclic garbage worth scanning.
    gc.freeze()
    gc.disable()
    _run_cli()
