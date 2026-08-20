"""The M1 integration driver (M1-PLAN Phase 5): the full pipeline run over the real rune files, writing every section 8 artifact under rebuild/out/m1/.

Stages: load_default_spec -> per-configuration decision/treaty tables (partition + E-STRANDED asserted, TSVs written, and the window enumeration serialized under the fingerprint of the sources it came from, so `--conform-only` mints its glyph inventory from it rather than rebuilding the fixpoint) -> glyph inventory minting (settled cells named by the table's own cell labels, plus the raw cmap glyphs, marker twins, chokepoint twins, and the namer dot pair) -> defects gates (run_gates merged with surface.check_anchor_conventions) -> emit_gsub/emit_gpos (whose plan also enumerates the emitted lookup's HarfBuzz-facing shapes into behavior_classes.json, the arming key rebuild/tools/deep_sweep.py reads) -> build_mini_font with the budget gate -> read-back (the font just written, re-parsed from its own bytes and structurally proven against the plan the emitters held; rebuild/pipeline/readback.py).

The glyph-name contract this driver pins: settlement-lookup outcomes are `settle.cell_label` names, so the decision-table rules and the compiled glyph set agree by construction; the raw cmap glyph for each rune is the bare rune name drawn as the isolated cell but carrying no curs anchors; marker, chokepoint, and ss10 twins reuse the bare drawing (under ss10 the pre-empt lookup substitutes every letter's cmap glyph by its anchor-free `.ss10` twin before formation, so no ligature ever forms, nothing settles, each letter keeps its own cluster, and every seam is a break).

Run as: uv run python -m rebuild.pipeline.run_m1
"""

from __future__ import annotations

import argparse
import gc
import gzip
import json
import multiprocessing
import os
import shutil
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

ENGINES = ("python", "rust")
ENGINE_DEFAULT = "rust"
TABLE_DIGESTS_FORMAT = "ams-m1-table-digests/1"


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
    engine: str = ENGINE_DEFAULT,
    kernel_threads: int | None = None,
) -> dict[str, tuple]:
    """Every acceptance configuration's decision and treaty tables, with the section 8 TSVs written under `out_dir`.

    `inputs` is `fingerprint.tables_value` over the sources this spec was loaded from. Supplying it alongside `out_dir` serializes each configuration's window enumeration next to the TSVs — where `run_font_conformance` picks it up instead of rebuilding the fixpoint — and drops those windows from the tables returned here, since only the rules, the reachable cells and the fired provenance are read after the build; `table.read_windows` gets them back. Omit it and the tables come back whole, which is what a caller building a spec of its own must do: the fingerprint names the repo's rune files and cannot vouch for tables they did not produce.

    `engine` chooses which half of the port enumerates those windows (issue 40). `rust` is the engine of record: the kernel crate enumerates, and each stream folds back through the same `assemble_tables`, the same table-level asserts and the same writers a Python build uses, so nothing downstream can tell which engine built the tables — the claim `rebuild.tools.kernel_gate` (`make kernel-gate`) re-proves on demand, run by hand around any kernel-semantics change. `python` runs the fixpoint in-process, one configuration at a time: the cargo-less arm a hand-assembled spec builds through, the conformance sweep's fallback, and one side of that differential. Either engine also leaves `table-digests.json` under `out_dir`: each configuration's `table.table_digest`, taken while the window rows are still in hand, which is the grain that harness states its comparison at.
    """
    if engine == "rust":
        tables, digests = _build_tables_rust(spec, out_dir, inputs, kernel_threads)
    elif engine == "python":
        tables, digests = _build_tables_python(spec, out_dir, inputs)
    else:
        raise ValueError(f"no such table engine: {engine!r}; the engines are {', '.join(ENGINES)}")
    if out_dir is not None:
        _write_table_digests(out_dir, inputs, digests, engine)
    return tables


def _build_tables_python(
    spec: ResolvedSpec, out_dir: Path | None, inputs: str | None
) -> tuple[dict[str, tuple], dict[str, str]]:
    """The in-process arm: one fixpoint per configuration in acceptance order, nothing carried between them."""
    tables: dict[str, tuple] = {}
    digests: dict[str, str] = {}
    for config in conform.ACCEPTANCE_CONFIGS:
        start = time.perf_counter()
        features = conform.features_for_config(config)
        decision, treaty = table_module.build_tables(spec, features)
        decision.assert_outcome_partition()
        decision.assert_e_stranded()
        persisted, digest = _persist_tables(decision, treaty, out_dir, inputs)
        tables[config] = (persisted, treaty)
        if digest is not None:
            digests[config] = digest
        print(f"[t] build_tables[{config}] {time.perf_counter() - start:.1f}s", flush=True)
    return tables, digests


def _build_tables_rust(
    spec: ResolvedSpec, out_dir: Path | None, inputs: str | None, kernel_threads: int | None
) -> tuple[dict[str, tuple], dict[str, str]]:
    """The engine of record: the resolved spec dumped once, every configuration answered by one `enumerate-configs` process, and each stream folded back through the Python half a configuration at a time. The two class-grain partition asserts `table.build_tables` runs have no place here — they read enumeration scaffolding a product deliberately does not carry across the boundary; the outcome-partition and E-STRANDED asserts are on the tables themselves and run exactly as they do on the other arm.

    It refuses a caller with no `out_dir` and `inputs`, where the python arm would happily build tables in memory: this engine exists to produce the repo's own artifacts under the stamp that names their sources, and a spec someone assembled by hand has no such stamp to write. Threads are capped at the configuration count and the CPU count because the kernel caps them there anyway, and defaulted low because the ceiling is memory rather than CPU — every configuration in flight holds its whole working set until it has emitted.
    """
    if out_dir is None or inputs is None:
        raise ValueError(
            "the rust engine builds the repo's own artifacts under the stamp naming their sources: build_tables needs both out_dir and inputs to run it"
        )
    configs = conform.ACCEPTANCE_CONFIGS
    threads = max(
        1,
        min(kernel_threads or kernel_exec.KERNEL_THREADS_DEFAULT, len(configs), os.process_cpu_count() or 1),
    )
    kernel_exec.cargo_build()
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
            decision, treaty = _fold_stream(spec, streams[config], directory)
            decision.assert_outcome_partition()
            decision.assert_e_stranded()
            persisted, digest = _persist_tables(decision, treaty, out_dir, inputs)
            tables[config] = (persisted, treaty)
            if digest is not None:
                digests[config] = digest
            print(f"[t] assemble_tables[{config}] {time.perf_counter() - start:.1f}s", flush=True)
    return tables, digests


def _fold_stream(spec: ResolvedSpec, stream: Path, scratch: Path):
    """One kernel stream folded into its two tables by the Python half of the build. `enumerate-configs` writes plain ndjson where `kernel_io.read_transitions` reads the gzip shape every artifact under `rebuild/out/` wears, so the bytes are packed on the way in the way `kernel_fixpoint.packed` packs them — at the cheapest compression there is, since this copy is written, read once and unlinked, and what the reader wants from it is the shape rather than the size. Both files go as soon as the product is in hand: a live configuration's stream is hundreds of megabytes and six of them would otherwise sit in the temporary directory for the length of the build."""
    packed = scratch / f"{stream.stem}.ndjson.gz"
    with (
        stream.open("rb") as plain,
        packed.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as handle,
    ):
        shutil.copyfileobj(plain, handle)
    stream.unlink()
    product = kernel_io.read_transitions(packed)
    packed.unlink()
    return table_module.assemble_tables(spec, product)


def _write_table_digests(out_dir: Path, inputs: str | None, digests: dict[str, str], engine: str) -> None:
    """The per-configuration contract digests a build leaves beside its tables, in acceptance order under the same stamp the windows heads carry. `table.table_digest` is the grain the rest of the rebuild states table identity at — the ordered rules with their provenance, every enumerated window row, the treaty rows, the reachable cells, the cited provenance and the identity guards — so `rebuild.tools.kernel_gate` compares the two engines against these rather than against the TSVs alone, which drop most of that. It has to be written at build time: the digest covers rows `_persist_tables` drops on its way out, and recovering one afterwards would cost the fixpoint that produced it. The record also states which engine built the set — provenance only, now that the differential builds both of its sides itself instead of reading this one."""
    payload = {"format": TABLE_DIGESTS_FORMAT, "inputs": inputs, "engine": engine, "digests": digests}
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
    engine: str = ENGINE_DEFAULT,
    kernel_threads: int | None = None,
) -> dict:
    """`inputs` is `fingerprint.tables_value` over the sources `spec` was loaded from, snapshotted before the load so it can only ever name content the tables are at least as new as. Supplying it serializes the window enumeration under `out_dir` for the conformance sweep; a caller running a spec of its own leaves it out. `engine` and `kernel_threads` reach the table build and nothing else, and the engine that built the tables rides the summary, so a `rebuild/out/m1` the kernel enumerated says so."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    if spec is None:
        spec = load_default_spec()
    print(f"[t] spec_load {time.perf_counter() - start:.1f}s", flush=True)

    start = time.perf_counter()
    tables = build_tables(spec, out_dir, inputs=inputs, engine=engine, kernel_threads=kernel_threads)
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
    print(f"[t] readback {time.perf_counter() - start:.1f}s", flush=True)
    if not readback_report["pass"]:
        raise readback.ReadbackError(
            f"{len(readback_report['divergences'])} read-back divergence(s) between the compiled font and the plan; see {out_dir / 'readback_summary.json'}"
        )

    summary = {
        "engine": engine,
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
    if table_module.DEEP_CLASSES_DEFAULT and table_module._deep_world(None):
        inputs = f"{inputs}+deep-classes"
    return inputs


def run_font_conformance(
    out_dir: Path = OUT_DIR,
    max_length: int = 4,
    jobs: int = 1,
    summary_name: str = "conform_summary.json",
) -> dict:
    """The exhaustive font-vs-settle sweep — the per-edit belt at `max_length` 4, and the same sweep deeper when rebuild.tools.deep_sweep asks for it under its own `summary_name`. The tables the build stage left under `out_dir` are read back here for one reason only, the glyph inventory `mint_cell_glyphs` needs to name settled cells and read their anchors; the sweep itself takes no table, because what it proves is HarfBuzz's behavior against the kernel's, and read-back already proved the font holds the rules the build planned. A fingerprint that fails to match rebuilds those tables in-process, which is the standalone case of a sweep against a font whose runes have since moved. The boundary gate's summary, when green for exactly this M1.otf, hands the sweep its structural checks within the proven horizon."""
    inputs = tables_inputs()
    spec = load_default_spec()
    start = time.perf_counter()
    serialized = serialized_tables(out_dir, inputs)
    if serialized is not None:
        decisions: Mapping[str, DecisionTable | tuple[DecisionTable, ...]] = serialized
        print(f"[t] load_tables {time.perf_counter() - start:.1f}s", flush=True)
    else:
        decisions = build_tables(spec, engine="python")
        print(
            f"[t] build_tables_total {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
    cell_glyphs = mint_cell_glyphs(spec, decisions)
    boundary_horizon = conform.proven_boundary_horizon(
        out_dir / "M1.otf", out_dir / "boundary_equivalence_summary.json"
    )
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
                    boundary_horizon=boundary_horizon,
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
            boundary_horizon=boundary_horizon,
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
        help="worker budget for the oracle/boundary/conformance shards; 1 = serial",
    )
    parser.add_argument(
        "--conform-only",
        action="store_true",
        help="run only the font-vs-settle conformance sweep against the existing M1.otf and exit nonzero unless it passes",
    )
    parser.add_argument(
        "--conform-horizon",
        type=int,
        default=4,
        help="exhaustive sweep length for --conform-only (the per-edit belt); `make conform-deep` runs the same sweep deeper on demand",
    )
    parser.add_argument(
        "--engine",
        choices=ENGINES,
        default=ENGINE_DEFAULT,
        help="which half of the port enumerates the windows: rust is the engine of record and hands the fixpoint to the kernel crate (built first), folding its streams through the same Python back half; python runs the fixpoint in-process, cargo-free; --conform-only ignores it",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        default=None,
        help=f"how many configurations --engine rust enumerates at once, capped at the configuration count and the CPU count (default {kernel_exec.KERNEL_THREADS_DEFAULT}, which AMS_KERNEL_THREADS overrides); the ceiling is memory rather than CPU",
    )
    args = parser.parse_args(argv)
    jobs = args.jobs if args.jobs and args.jobs > 1 else 1

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
        summary = run(spec=spec, inputs=inputs, engine=args.engine, kernel_threads=args.kernel_threads)
        print(
            f"[t] run_total {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
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
    except (SystemExit, readback.ReadbackError, emit_gsub.EmitError) as error:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        if isinstance(error, SystemExit):
            raise
        raise SystemExit(str(error))
    gate = evaluate_run_m1_gate(summary, boundary_gate, pin_gate, oracle)
    recordable = gate.ok and args.engine == ENGINE_DEFAULT
    if gate.ok and not recordable:
        print(
            f"run_m1: green under --engine {args.engine} — green not recorded, so the next cycle rebuilds with the engine of record ({ENGINE_DEFAULT})",
            flush=True,
        )
    _settle_green(
        RUN_M1_GREEN, before, recordable, run_m1_key, "run_m1", files_of=lambda: run_m1_skip_files(REPO_ROOT)
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
