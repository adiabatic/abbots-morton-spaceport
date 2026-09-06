"""The M1 integration driver (M1-PLAN Phase 5): the full pipeline run over the real rune files, writing every section 8 artifact under rebuild/out/m1/.

Stages: load_default_spec -> per-configuration decision/treaty tables (enumerated and folded in the kernel crate, one process per configuration: the first-match-wins replay asserted as each one folds, TSVs written, and the window enumeration serialized under the fingerprint of the sources it came from, so `--conform-only` mints its glyph inventory from it and refuses to run against a stale or missing one) -> glyph inventory minting (settled cells named by the table's own cell labels, plus the raw cmap glyphs, marker twins, chokepoint twins, and the namer dot pair) -> defect gates (defects.run_gates under the reviewed allow-list) -> emit_gsub/emit_gpos (whose plan also enumerates the emitted lookup's HarfBuzz-facing shapes into behavior_classes.json, the arming key rebuild/tools/deep_sweep.py reads) -> build_mini_font -> read-back (the font just written, re-parsed from its own bytes and structurally proven against the plan the emitters held, with the GSUB's uint16 subtable-offset headroom read off the raw table bytes in that same parse and held to its floor, and that plan's settlement rows recorded beside the summary with their per-configuration sources for the witness gate to count coverage over; rebuild/pipeline/readback.py).

The glyph-name contract this driver pins: settlement-lookup outcomes are `settle.cell_label` names, so the decision-table rules and the compiled glyph set agree by construction; the raw cmap glyph for each rune is the bare rune name drawn as the isolated cell but carrying no curs anchors; marker, chokepoint, and ss10 twins reuse the bare drawing (under ss10 the pre-empt lookup substitutes every letter's cmap glyph by its anchor-free `.ss10` twin before formation, so no ligature ever forms, nothing settles, each letter keeps its own cluster, and every seam is a break).

The split-buffer check that once had a standalone horizon-5 gate of its own now rides gate:conform's belt, so it is proven per build at horizon 4 and periodically at 5 or deeper by `make conform-deep` — the same charter the belt already has, over a rule whose closure property makes a horizon-4 proof cover every window the oracle absorbs. The ZWNJ slot's own structure — zero advance, no ink — is read-back's static boundary-glyphs stage, proven off the written font bytes rather than at every shaped slot.

Run as: uv run python -m rebuild.pipeline.run_m1 — or `--conform-only` for the belt alone against the M1.otf on disk, or `--gates-only` for everything a full run does after the table build except the stages that make the artifacts: the defect gate, the Manual-pin gate and the oracle over the tables and font already there. That is the cheap way to re-adjudicate any comparison-side edit — the divergence ledger, the alias map, the kern sidecar, the contact allow-list the defect gate reads, or the oracle's own code, the classifier and its predicates and the ledger match and the position channel all living in rebuild/pipeline/oracle.py, which the enumeration's stamp leaves out (`fingerprint.table_code_paths`; rebuild/test_build_code_closure.py proves the build never reaches it) — without rebuilding a thing. That pass records run_m1's green when a prior green exists and everything that has moved since it is comparison-side, so the artifact cycle takes the route itself and the pass after it skips run_m1 outright. Either way the oracle serves what it can from the per-row verdict stores rebuild/pipeline/oracle_cache.py keeps beside the tables — a ledger or alias edit moves no family key and no stamp line, so every row verdict and every position verdict is served and the whole re-adjudication is the ledger match and the audit, while an edit to the kern sidecar or to the oracle's own module keeps the row verdicts and re-shapes the positions; `--fresh-oracle-cache` distrusts them, and `--gates-only` may read them but never write one.
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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
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
    oracle,
    oracle_cache,
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
from rebuild.pipeline.settle import cell_label
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable
from rebuild.tools import console
from rebuild.tools.cycle_timings import CYCLE_RUN_ENV, CheckVerdict, record_check
from rebuild.tools.memory_budget import describe_fit, usable_cores
from rebuild.tools.peak_rss import process_peak_rss_bytes, rss_token

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


def build_tables(
    spec: ResolvedSpec,
    out_dir: Path | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
) -> tuple[dict[str, tuple], dict[str, str]]:
    """Every acceptance configuration's decision and treaty tables: the resolved spec dumped once, then one `build-tables` process per configuration, each of which enumerates its fixpoint and folds it in place. There is no stream and no fold on this side at all — the crate writes the settlement TSV, the treaty TSV and the window enumeration itself, so the several hundred megabytes a configuration's transitions cost to write, to read and to hold parsed are never spent.

    What Python does per configuration is small and is what only Python can do: pack the plain window payload into the `.gz` the artifact is (the compressor never crossed the boundary), read the head back for the rules, the reachable cells and the fired provenance every downstream stage needs, and parse the treaty TSV back for the defect gates.

    `out_dir`, when given, gets the section 8 TSVs. The second returned mapping is each configuration's `table.table_digest` as the crate reported it — taken in the crate while the window rows are still in hand, which is the grain the rest of the rebuild states table identity at and the only moment it can be taken without re-costing the fixpoint; the crate also prints it on stdout, which is where `rebuild/tools/scaling_sweep.py` reads it. Both returned mappings are rebuilt in `conform.ACCEPTANCE_CONFIGS` order however the configurations finish, so completion order can never reach an artifact.

    `inputs` is `tables_inputs` over the sources this spec was loaded from. Supplying it alongside `out_dir` keeps each configuration's window enumeration next to the TSVs — where `run_font_conformance` picks it up rather than rebuilding anything — under the stamp that names those sources; omit it and the payload is read for its head and deleted, which is what a caller building a spec of its own must have, since the fingerprint names the repo's rune files and cannot vouch for tables they did not produce.

    `kernel_threads` is how many configurations are in flight at once, capped here at the configuration count and the cores this process may actually run on — neither of which is a memory bound — while the default it falls back to is the memory one: `kernel_exec.KERNEL_THREADS_DEFAULT` is this box's own memory divided by what a configuration costs while it holds its whole working set, from the first window it reaches to the last artifact it writes. So this `min()` only ever narrows a memory-derived width and never widens one, and nothing about memory belongs inside it. The fold's own width went with the Python fold: it runs inside the enumerating process now, and there is nothing left on this side to widen.
    """
    configs = conform.ACCEPTANCE_CONFIGS
    threads = max(
        1,
        min(kernel_threads or kernel_exec.KERNEL_THREADS_DEFAULT, len(configs), usable_cores()),
    )
    kernel_exec.ensure_built()
    built: dict[str, tuple] = {}
    digests: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        spec_path = directory / "spec.json"
        kernel_io.write_spec(spec, spec_path)
        tables_dir = directory / "tables" if out_dir is None else out_dir
        tables_dir.mkdir(parents=True, exist_ok=True)

        def build_one(config: str) -> tuple[str, tuple, str]:
            start = time.perf_counter()
            answered = kernel_exec.build_table_files(
                spec_path,
                tables_dir,
                [config],
                inputs=inputs if inputs is not None else kernel_exec.UNSTAMPED_WINDOWS,
                threads=1,
                timings=True,
                timings_tag=config,
            )
            print(f"[t] kernel_enumerate[{config}] {time.perf_counter() - start:.1f}s", flush=True)
            start = time.perf_counter()
            payload = tables_dir / f"windows-{config}.tsv"
            with payload.open("rt", encoding="utf-8") as handle:
                _stamp, decision = table_module.read_windows(handle, windows=False)
            treaty = table_module.read_treaty_tsv(tables_dir / f"treaties-{config}.tsv")
            if inputs is not None and out_dir is not None:
                _pack_windows(payload, table_module.windows_path(tables_dir, config))
            payload.unlink()
            print(f"[t] pack_windows[{config}] {time.perf_counter() - start:.1f}s", flush=True)
            return config, (decision, treaty), answered[config]

        with ThreadPoolExecutor(max_workers=threads) as kernels:
            for finished in as_completed([kernels.submit(build_one, config) for config in configs]):
                config, tables, digest = finished.result()
                built[config] = tables
                digests[config] = digest
                console.progress(len(built), len(configs), "configurations")
    return {config: built[config] for config in configs}, {config: digests[config] for config in configs}


def _pack_windows(payload: Path, path: Path) -> None:
    """The plain window enumeration the kernel wrote, packed into the artifact beside the TSVs — a zeroed gzip stamp so two builds of one table are byte-identical, and level 6 rather than zlib's maximum, which is a wall-clock choice and not a contract one: what anything states identity at is the decompressed bytes. The crate's `artifacts::write_windows` carries the same note beside the payload it writes."""
    with (
        payload.open("rb") as source,
        path.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as packed,
    ):
        shutil.copyfileobj(source, packed, length=1 << 20)


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


def _run_defect_gates(
    spec: ResolvedSpec,
    tables: Mapping[str, tuple],
    cell_glyphs: Mapping[CellId, GlyphRecord],
) -> defects.DefectReport:
    """The section 9 defect gates over one build's tables and minted glyphs: `defects.run_gates` under the reviewed allow-list. Shared by the build and by `--gates-only`, which re-runs it over the tables and glyphs already on disk — the allow-list is in no stamp and no fingerprint component, so blessing a signature is exactly the edit that re-adjudicates here instead of rebuilding."""
    allow = frozenset(entry["signature"] for entry in yaml.safe_load(CONTACT_ALLOW_YAML.read_text()) or ())
    return defects.run_gates(spec, tables, cell_glyphs, allow=allow)


def _defect_summary_fields(report: defects.DefectReport) -> dict:
    """The five `pipeline_summary.json` fields the defect gate owns, in the shapes `run` writes them — and the whole of what a `--gates-only` pass rewrites into the summary a build left behind."""
    return {
        "defect_errors": [f"{d.code} {d.signature}: {d.message}" for d in report.errors],
        "defect_flags": [f"{d.code} {d.signature}: {d.message}" for d in report.flags],
        "dead_in_alphabet": sorted(report.dead_in_alphabet),
        "deferred_partner": sorted(report.deferred_partner),
        "notes": report.notes,
    }


def run(
    out_dir: Path = OUT_DIR,
    spec: ResolvedSpec | None = None,
    inputs: str | None = None,
    kernel_threads: int | None = None,
) -> dict:
    """`inputs` is `tables_inputs` over the sources `spec` was loaded from, snapshotted before the load so it can only ever name content the tables are at least as new as. Supplying it serializes the window enumeration under `out_dir` for the conformance sweep; a caller running a spec of its own leaves it out. `kernel_threads` reaches the table build and nothing else."""
    out_dir.mkdir(parents=True, exist_ok=True)
    console.phase("spec_load")
    start = time.perf_counter()
    if spec is None:
        spec = load_default_spec()
    print(f"[t] spec_load {time.perf_counter() - start:.1f}s", flush=True)

    console.phase("build_tables_total")
    start = time.perf_counter()
    tables, _digests = build_tables(spec, out_dir, inputs=inputs, kernel_threads=kernel_threads)
    print(
        f"[t] build_tables_total {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
        flush=True,
    )

    console.phase("glyph_minting")
    start = time.perf_counter()
    cell_glyphs = mint_cell_glyphs(spec, tables)
    bare, twins, ss10_twins = mint_raw_glyphs(spec)
    dots = namer_dot_glyphs()
    print(f"[t] glyph_minting {time.perf_counter() - start:.1f}s", flush=True)

    console.phase("defect_gates")
    start = time.perf_counter()
    defect_report = _run_defect_gates(spec, tables, cell_glyphs)
    print(f"[t] defect_gates {time.perf_counter() - start:.1f}s", flush=True)

    console.phase("emit_gsub_gpos")
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

    console.phase("compile_font")
    start = time.perf_counter()
    all_glyphs = {**curs_glyphs, **dots}
    font_path = compile_font.build_mini_font(all_glyphs, fea, out_dir / "M1.otf")
    print(f"[t] compile_font {time.perf_counter() - start:.1f}s", flush=True)
    (out_dir / "M1.generated.fea").write_text(fea)

    console.phase("readback")
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
        "configs": list(tables),
        "rules_per_config": {config: len(decision.rules) for config, (decision, _treaty) in tables.items()},
        "settled_cell_glyphs": len(cell_glyphs),
        "total_glyphs": len(all_glyphs),
        "gsub_rule_count": gsub_plan.rule_count,
        **_defect_summary_fields(defect_report),
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
    """The stamp serialized windows carry: `fingerprint.tables_value` plus a token per semantics-mode default that is on (the simulated prospect, the stage-4b shifted vote slots, the issue-26 class-grain deep slots). The environment flags change settlement semantics or enumeration grain without moving any hashed source, so without the tokens a flag-on enumeration would read as fresh to a flag-off process (and the reverse) and the sweep would replay tables the in-process kernel no longer produces.

    The stamp is over what the fixpoint reads, which is why its data half is `fingerprint.table_data_value` and not `fingerprint.data_value`: the alias map, the divergence ledger and the kern sidecar are the baseline oracle's comparison inputs, and the contact allow-list — which is in no fingerprint component at all — is the defect gate's, every one of them consumed against tables that are already built. Editing one leaves every enumeration on disk exactly as fresh as it was, which is what lets `--gates-only` re-adjudicate over the tables and font already there instead of refusing to. The classifier those files feed comes along too: it lives in rebuild/pipeline/oracle.py, and the stamp's code half is `fingerprint.table_code_paths`, the pipeline tree minus that comparison side, so a classifier edit passes the same way — rebuild/test_build_code_closure.py is what holds the build to never importing it. None of that narrows what a comparison-side edit is checked by: every one of those labels is in the artifact cycle's run_m1 key (`artifact_cycle.run_m1_skip_lines`), so the green still moves and the gates they feed — the defect gate included — still re-run, over the artifacts on disk rather than over new ones.
    """
    inputs = fingerprint.tables_value(REPO_ROOT)
    for token in kernel_exec.enumeration_tokens():
        inputs = f"{inputs}+{token}"
    return inputs


def settle_memo_inputs() -> oracle_cache.SettleMemoInputs:
    """The disk-derived half of the settle memo's keys (`oracle_cache.SettleMemoInputs`), cut where `tables_inputs` is cut — before `load_default_spec`, so a key can only ever name content the settlements are at least as new as. Every entry point that shares a memo with another phase snapshots this beside the tables' stamp and hands both to `conform.settle_memo_files` once the spec is loaded."""
    return oracle_cache.settle_memo_inputs(REPO_ROOT)


def run_font_conformance(
    out_dir: Path = OUT_DIR,
    max_length: int = 4,
    jobs: int = 1,
    summary_name: str = "conform_summary.json",
) -> dict:
    """The exhaustive font-vs-settle sweep — the per-edit belt at `max_length` 4, and the same sweep deeper when rebuild.tools.deep_sweep asks for it under its own `summary_name`. The tables the build stage left under `out_dir` are read back here for one reason only, the glyph inventory `mint_cell_glyphs` needs to name settled cells and read their anchors; the sweep itself takes no table, because what it proves is HarfBuzz's behavior against the kernel's, and read-back already proved the font holds the rules the build planned. A stamp that fails to match is a hard stop rather than a rebuild: the enumeration costs a whole kernel fan-out, and a sweep that quietly built its own inventory would be measuring a font against runes that have since moved. The split-buffer structural check rides this sweep, on every text that carries a splitter.

    The fan-out spends the section 5.7 verdict surface once for the whole run rather than once per worker: a spawned worker inherits nothing, so each would otherwise build the crate it found and sweep the spec for itself. The mapping pickles, so it rides the submission; the serial arm sweeps inside `run_conformance` as before.

    At the per-edit horizon each configuration's walk shares its settle memo with the oracle's walk over the same texts, through a file under `out_dir` keyed per family the way the oracle row cache is (`conform.settle_memo_files`, off `settle_memo_inputs` snapshotted before the spec loads): whichever phase runs first settles and writes, the other loads, and a rune edit retires only the entries whose windows name an edited family. A deeper sweep shares nothing — its memo is a multiple of the belt's, and a file that size would cost the next belt and oracle workers more to decode than they save.
    """
    inputs = tables_inputs()
    memo_inputs = settle_memo_inputs()
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
    settle_memos = (
        conform.settle_memo_files(out_dir, spec, memo_inputs) if max_length == conform.BELT_HORIZON else {}
    )
    if jobs > 1:
        collected: dict[str, conform.ConformanceConfigResult] = {}
        kernel_exec.ensure_built()
        guard_verdicts = kernel_exec.guard_sweep(spec)
        with _spawn_pool(jobs) as pool:
            futures = {
                pool.submit(
                    conform.conformance_config_worker,
                    spec,
                    out_dir / "M1.otf",
                    config,
                    max_length,
                    cell_glyphs,
                    guard_verdicts,
                    settle_memos.get(config),
                ): config
                for config in conform.ACCEPTANCE_CONFIGS
            }
            for future in as_completed(futures):
                result = future.result()
                collected[result.config] = result
                console.progress(len(collected), len(conform.ACCEPTANCE_CONFIGS), "configurations")
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
            settle_memos=settle_memos,
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


def _promote_oracle_row_cache(
    spec: ResolvedSpec,
    out_dir: Path,
    scratch: Path,
    keys: Mapping[str, str],
    stamps: Mapping[str, oracle_cache.EnvironmentStamp],
    position_keys: Mapping[str, str] | None = None,
    position_stamp: oracle_cache.EnvironmentStamp | None = None,
) -> None:
    """Move this run's staged stores into place, but only after re-cutting every key off the sources as they stand now and finding them where the run left them — the row keys and stamps off the rune tree, the position keys and stamp off the font and the kern sidecar. A run whose inputs shifted under it built verdicts nothing on disk describes, and a store recorded under the wrong digest is the one failure that reads as green forever rather than failing once — so the answer to any movement is to write nothing and say so, which costs the next pass a cold oracle and nothing else. An alias map edited mid-run into a shape `alias_family_digests` refuses lands here as the same refusal, and so does a font that will not re-digest."""
    try:
        keys_now, stamps_now = oracle_row_cache_keys(spec, out_dir)
        position_keys_now, position_stamp_now = oracle_position_keys(keys_now, out_dir)
    except (OSError, ValueError, yaml.YAMLError) as error:
        console.warn(f"oracle row cache: not written — its inputs would not re-read ({error})")
        return
    moved = oracle_cache.moved_note(dict(keys), keys_now)
    if moved is None:
        for config in conform.ACCEPTANCE_CONFIGS:
            moved = oracle_cache.moved_note(stamps[config].labels, stamps_now[config].labels)
            if moved is not None:
                moved = f"{config} {moved}"
                break
    if moved is None and position_keys is not None:
        moved = oracle_cache.moved_note(dict(position_keys), position_keys_now or {})
        if moved is None and position_stamp is not None:
            moved = oracle_cache.moved_note(
                position_stamp.labels, position_stamp_now.labels if position_stamp_now else {}
            )
        if moved is not None:
            moved = f"positions {moved}"
    if moved is not None:
        console.warn(
            f"oracle row cache: not written — its inputs moved while the oracle ran ({moved}), so nothing it derived describes what is on disk"
        )
        return
    promoted = oracle_cache.promote_stores(scratch, out_dir, conform.ACCEPTANCE_CONFIGS)
    if promoted:
        print(f"oracle row cache: written for {', '.join(promoted)}", flush=True)
    else:
        console.warn("oracle row cache: not written — a configuration staged no store")


def oracle_row_cache_keys(
    spec: ResolvedSpec, out_dir: Path
) -> tuple[dict[str, str], dict[str, oracle_cache.EnvironmentStamp]]:
    """The oracle row cache's two keys as one read of the sources behind them: a digest per rune family, and a whole-store stamp per acceptance configuration. Cut once here and cut again at promotion, which is the only way the run can tell that what it recorded is what it built from."""
    keys = oracle_cache.family_keys(REPO_ROOT, spec, ALIAS_YAML)
    stamps = {
        config: oracle_cache.environment_stamp(
            REPO_ROOT,
            spec,
            config,
            conform.features_for_config(config),
            out_dir / f"baseline-{config}.subset.tsv.gz",
            ALIAS_YAML,
            keys.keys(),
        )
        for config in conform.ACCEPTANCE_CONFIGS
    }
    return keys, stamps


def oracle_position_keys(
    keys: Mapping[str, str], out_dir: Path
) -> tuple[dict[str, str], oracle_cache.EnvironmentStamp] | tuple[None, None]:
    """The position store's two keys, cut off the font the oracle is about to shape against and the kern sidecar it reads: `oracle_cache.position_keys` over the row keys already cut. A pass with no compiled font under `out_dir` has nothing to shape and answers the pair of Nones, which records every position as unshaped and serves none."""
    font = Path(out_dir) / "M1.otf"
    if not font.is_file():
        return None, None
    return oracle_cache.position_keys(REPO_ROOT, keys, font, KERN_SIDECAR_YAML)


def _report_oracle_cache(
    out_dir: Path,
    keys: Mapping[str, str],
    stamps: Mapping[str, oracle_cache.EnvironmentStamp],
    position_keys: Mapping[str, str] | None = None,
    position_stamp: oracle_cache.EnvironmentStamp | None = None,
) -> None:
    """Say, before the fan-out, what the stores on disk will and will not answer — because the hit rate here is bimodal and a whole-store drop looks exactly like a bug when nothing names the line that caused it. A stamp line that moved (a pipeline module, a predicate class gaining a member, the engine's semantics flags) drops every row of every configuration; a family key that moved re-derives only the rows that can reach it, which is the ordinary shape of a rune edit. The position store answers on its own second line: a position stamp line that moved (the oracle's module, the kern sidecar, the font's helpers) re-shapes every row while the rows are still served, and a position key that moved re-shapes only the rows that reach that family — the shape of a glyph edit."""
    recorded = oracle_cache.read_header(oracle_cache.store_path(out_dir, conform.ACCEPTANCE_CONFIGS[0]))
    if recorded is None:
        print("oracle row cache: no store on disk — this pass derives every row and writes one", flush=True)
        return
    stamp = stamps[conform.ACCEPTANCE_CONFIGS[0]]
    stored_lines = {
        label: digest
        for label, _, digest in (str(line).partition("\t") for line in recorded.get("environment") or ())
    }
    moved_stamp = oracle_cache.moved_note(stored_lines, stamp.labels)
    if moved_stamp is not None:
        console.warn(f"oracle row cache: dropped — the stamp moved at {moved_stamp}")
        return
    stored_keys = {str(name): str(value) for name, value in (recorded.get("family_keys") or {}).items()}
    moved_keys = oracle_cache.moved_note(stored_keys, dict(keys))
    if moved_keys is None:
        print("oracle row cache: the stamp and every family key still stand", flush=True)
    else:
        console.warn(f"oracle row cache: re-deriving the rows that reach {moved_keys}")
    if position_keys is None or position_stamp is None:
        print("oracle position store: no font to shape against — every position is shaped", flush=True)
        return
    stored_position_lines = {
        label: digest
        for label, _, digest in (
            str(line).partition("\t") for line in recorded.get("position_environment") or ()
        )
    }
    moved_position_stamp = oracle_cache.moved_note(stored_position_lines, position_stamp.labels)
    if moved_position_stamp is not None:
        console.warn(
            f"oracle position store: re-shaping every row — the position stamp moved at {moved_position_stamp}"
        )
        return
    stored_position_keys = {
        str(name): str(value) for name, value in (recorded.get("position_keys") or {}).items()
    }
    moved_position_keys = oracle_cache.moved_note(stored_position_keys, dict(position_keys))
    if moved_position_keys is None:
        print("oracle position store: the position stamp and every glyph key still stand", flush=True)
    else:
        console.warn(f"oracle position store: re-shaping the rows that reach {moved_position_keys}")


def run_oracle(
    out_dir: Path = OUT_DIR,
    spec: ResolvedSpec | None = None,
    jobs: int = 1,
    write_cache: bool = True,
    fresh_cache: bool = False,
    memo_inputs: oracle_cache.SettleMemoInputs | None = None,
) -> dict:
    """The section 6 oracle over the subset tables, one worker per `conform.ACCEPTANCE_CONFIGS` entry when `jobs` allows, with the row cache read before the first row and written after the last. `memo_inputs` is `settle_memo_inputs` as the caller snapshotted it before loading `spec`, and names the settle memo files this pass shares with the belt (`conform.settle_memo_files`): the oracle's rows are the belt's texts, so a configuration whose file the belt wrote under these keys settles nothing, and one the belt has not reached yet writes the file the belt will load. A caller with no inputs shares nothing.

    The cache's keys are cut once here — the row keys from the rune tree, the position keys from the compiled font and the kern sidecar — and handed to the workers, and then cut a second time at promotion, where a store is written only if neither a stamp nor a single key moved while the run held them. That second cut is the point: `fingerprint.rune_digests` reads the rune files off disk, a full run takes minutes, and the house style is to detach a long run and keep editing — so a rune touched mid-run would otherwise be recorded under a digest the verdicts on disk were never built from, and the next pass would serve pre-edit verdicts as fresh, green, forever. `_settle_green`'s recompute-before-recording and `artifact_cycle`'s green keys are the same discipline for the same reason.

    Cutting those keys is itself allowed to fail without taking the gate down with it. `alias_family_digests` refuses an alias head no rune digest stands behind, and the alias map is exactly the sort of hand-edited file that arrives one typo away from unreadable — so a key that will not cut leaves this pass with no cache at all: every row derived, no store written, the gate doing what it did before there was a cache. Whether a ledger can be re-adjudicated must never turn on whether a file the comparison does not read parses.

    Staging lives inside this run's pid-named audit scratch, so a killed run's stores are swept by `discard_oracle_audit_scratch` exactly as its shards are, and two oracles sharing an `out_dir` — a `--gates-only` pass beside a cycle — can neither read nor promote over one another. Promotion happens only after `join_oracle_audit` has accepted the audit: a store describing an audit that was never written is worse than no store.
    """
    if spec is None:
        spec = load_default_spec()
    oracle.discard_oracle_audit_scratch(out_dir)
    if fresh_cache and write_cache:
        oracle_cache.discard_stores(out_dir, conform.ACCEPTANCE_CONFIGS)
    scratch = oracle.oracle_audit_scratch(out_dir)
    keys: dict[str, str] | None = None
    stamps: dict[str, oracle_cache.EnvironmentStamp] | None = None
    position_keys: dict[str, str] | None = None
    position_stamp: oracle_cache.EnvironmentStamp | None = None
    row_cache: oracle.OracleRowCache | None = None
    try:
        keys, stamps = oracle_row_cache_keys(spec, out_dir)
        position_keys, position_stamp = oracle_position_keys(keys, out_dir)
    except (OSError, ValueError, yaml.YAMLError) as error:
        console.warn(
            f"oracle row cache: unavailable for this pass — its keys would not cut ({error}); every row is derived and no store is written"
        )
        keys = stamps = None
    else:
        if fresh_cache:
            print("oracle row cache: distrusted for this pass — every row is derived", flush=True)
        else:
            _report_oracle_cache(out_dir, keys, stamps, position_keys, position_stamp)
        row_cache = oracle.OracleRowCache(
            environment=stamps,
            family_keys=keys,
            read_dir=None if fresh_cache else out_dir,
            write_dir=scratch if write_cache else None,
            rotation=0 if write_cache else int(time.time()),
            position_environment=position_stamp,
            position_keys=position_keys,
        )
    settle_memos = conform.settle_memo_files(out_dir, spec, memo_inputs)
    try:
        if jobs > 1:
            collected: dict[str, oracle.OracleConfigResult] = {}
            with _spawn_pool(jobs) as pool:
                futures = {
                    pool.submit(
                        oracle.oracle_config_worker,
                        spec,
                        out_dir,
                        ALIAS_YAML,
                        DIVERGENCES_YAML,
                        config,
                        out_dir / "M1.otf",
                        KERN_SIDECAR_YAML,
                        audit_dir=scratch,
                        row_cache=row_cache,
                        settle_memo=settle_memos.get(config),
                    ): config
                    for config in conform.ACCEPTANCE_CONFIGS
                }
                for future in as_completed(futures):
                    result = future.result()
                    collected[result.config] = result
                    console.progress(len(collected), len(conform.ACCEPTANCE_CONFIGS), "configurations")
            ordered = [collected[config] for config in conform.ACCEPTANCE_CONFIGS]
            report = oracle.merge_oracle_results(ordered)
            oracle.join_oracle_audit(out_dir, scratch, conform.ACCEPTANCE_CONFIGS, report.divergent_rows)
        else:
            report = oracle.compare_against_baseline(
                spec,
                out_dir,
                ALIAS_YAML,
                DIVERGENCES_YAML,
                out_dir=out_dir,
                font_path=out_dir / "M1.otf",
                kern_sidecar_path=KERN_SIDECAR_YAML,
                row_cache=row_cache,
                settle_memos=settle_memos,
            )
        if write_cache and keys is not None and stamps is not None:
            _promote_oracle_row_cache(spec, out_dir, scratch, keys, stamps, position_keys, position_stamp)
    finally:
        oracle.discard_oracle_audit_scratch(out_dir)
    summary = {
        "rows_compared": report.rows_compared,
        "divergent_rows": report.divergent_rows,
        "positions_compared": report.positions_compared,
        "positions_excluded": report.positions_excluded,
        "positions_served": report.positions_served,
        "counts_by_entry": dict(sorted(report.counts_by_entry.items())),
        "unmatched": report.unmatched_count,
        "multi_matched": len(report.multi_matched),
        "notes": report.notes,
    }
    for row in report.unmatched_exemplars[: oracle.ORACLE_UNMATCHED_EXEMPLARS]:
        summary.setdefault("unmatched_exemplars", []).append(
            f"{row.config} {row.codepoints} {'|'.join(row.baseline_glyphs)} -> {'|'.join(row.new_cells)} {row.phenomena}"
        )
    (out_dir / "oracle_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _record_cli_check(verdict: CheckVerdict, started: float) -> None:
    """File this invocation's verdict in the timings journal, unless a cycle is already recording on its behalf. What is recorded is what the judge decided, and it is recorded before the trailing SystemExit so that exit can never rewrite it. CYCLE_RUN_ENV in the environment means the artifact cycle spawned this run and files the same judgment under its own run id, and one invocation is worth exactly one line — so this one stands down rather than writing a second."""
    if CYCLE_RUN_ENV in os.environ:
        return
    record_check(
        verdict,
        argv=sys.argv,
        elapsed_s=time.perf_counter() - started,
        peak_rss_bytes=process_peak_rss_bytes(),
    )


def _failed_check(check: str, message: str) -> CheckVerdict:
    """The verdict for a run that never reached its judge — a defect gate that stopped the build, a pin gate that refused it, a read-back or emit error. The message it died with is the whole of what is known, so it is the failure prose, and there are no failed ids because nothing here enumerated a case."""
    return CheckVerdict(check=check, verdict="red", status="FAILED", failures=[message], failed_ids=[])


def _run_pregate_guards() -> None:
    """The three things proven before anything is adjudicated, on the build path and on the `--gates-only` path alike: every source baseline table shaped by the site font on disk (its header's font_sha256 weighed against the font that header names, which `baseline_subset.ensure_fresh` does on every call because `make all` rewrites that font under a stamp key that never moves), the subset baselines refiltered when they no longer describe the sources on disk, and every old glyph name in those subsets carrying an alias. All three belong to the oracle rather than to the build — rows some other font shaped and an unaliased name each make every oracle number quietly wrong — so a pass that re-runs the oracle over a build it did not make has to run them exactly as the pass that built does. The ss06/ss07/ss06+ss07 identity is proven inside that refilter rather than here, since only a refilter can change the answer; a diverged configuration is never stamped fresh, so the refusal it raises surfaces as this guard's refusal on every run until it is dealt with."""
    console.phase("baseline_subset")
    start = time.perf_counter()
    try:
        refiltered = baseline_subset.ensure_fresh(REPO_ROOT)
    except (baseline_subset.SubsetIdentityError, baseline_subset.BaselineProvenanceError) as error:
        raise SystemExit(str(error)) from error
    print(
        f"[t] baseline_subset {time.perf_counter() - start:.1f}s ({'refiltered' if refiltered else 'fresh'})",
        flush=True,
    )
    console.phase("alias_completeness")
    start = time.perf_counter()
    missing_aliases = oracle.unaliased_subset_names(OUT_DIR, ALIAS_YAML)
    print(f"[t] alias_completeness {time.perf_counter() - start:.1f}s", flush=True)
    if missing_aliases:
        listing = "\n".join(f"  {name} ({', '.join(configs)})" for name, configs in missing_aliases.items())
        raise SystemExit(
            f"rebuild/m1-aliases.yaml is missing {len(missing_aliases)} old glyph names that appear in subset baseline rows — every oracle number would be quietly wrong, so author each entry (or map it to the literal `pending` to run anyway with those rows unaliased):\n{listing}"
        )


def run_gates_only(out_dir: Path = OUT_DIR, jobs: int = 1, fresh_cache: bool = False) -> None:
    """Everything a full run does after the table build except the stages that make the artifacts — the defect gate, the Manual-pin replay and the oracle, re-run over the tables and the M1.otf already on disk, rewriting the defect fields of `pipeline_summary.json`, the Stage A record, the gate summaries and `divergence-audit.tsv` without recompiling anything. What licenses the reuse is the stamp the build left on its serialized enumerations: it names the sources those tables came from, so a stamp that still matches the runes on disk says the M1.otf beside them is the font those runes describe, and a stamp that does not is a refusal rather than a silent sweep of a stale binary. Because that stamp names only what the build reads, every comparison-side edit passes it and re-adjudicates here — the divergence ledger, the alias map, the kern sidecar, the contact allow-list the defect gate reads, and the classifier those files feed, rebuild/pipeline/oracle.py being outside the stamp's code half.

    It may record run_m1's green, and the condition is what makes that sound: a prior green record must exist and every input that has moved since it must be comparison-side (`artifact_cycle.gates_only_reuse`). The prior green is the proof that the tables and font on disk came from a completed build over every build-side input; the stamp check is the proof that none of those inputs has moved since; and this pass is what re-proves the gates the moved inputs feed. With both in hand the recorded green covers the new inputs too, so the next cycle skips run_m1 outright. Without them the pass still runs, still files its check line, and says which label kept it from recording — a check line only claims how one invocation came out, where a green licenses a later pass to skip work.

    It opens the oracle row cache read-only all the same (`write_cache=False`): a ledger or alias edit moves no family key and no stamp line, so every row verdict and every position verdict is served and the re-adjudication costs seconds, while the store one of these passes would write is a store no build ever produced. Recording no ordinal has one consequence worth naming: the store's renewal slice and its verification sample both advance on the pass a store records, so these passes rotate them on the clock instead — a re-adjudication loop that re-proved one frozen twentieth of the table every time would be no guard at all. `--fresh-oracle-cache` here declines the read rather than taking the stores off disk, since deleting a build input is a write like any other.
    """
    from rebuild.tools.artifact_cycle import (
        RUN_M1_GREEN,
        comparison_side_label,
        evaluate_run_m1_gate,
        gates_only_reuse,
        moved_input_labels,
        read_green_record,
        run_m1_skip_files,
        run_m1_skip_fingerprint,
    )

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(REPO_ROOT)

    started = time.perf_counter()
    _run_pregate_guards()
    inputs = tables_inputs()
    memo_inputs = settle_memo_inputs()
    font_path = out_dir / "M1.otf"
    summary_path = out_dir / "pipeline_summary.json"
    serialized = serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; --gates-only re-runs the gates over a build, it does not make one"
        )
    if not font_path.is_file():
        raise SystemExit(
            f"no compiled font at {font_path} — run `uv run python -m rebuild.pipeline.run_m1` first"
        )
    try:
        pipeline_summary = json.loads(summary_path.read_text())
    except OSError, ValueError:
        pipeline_summary = None
    if not isinstance(pipeline_summary, dict):
        raise SystemExit(
            f"no readable {summary_path} — the defect fields are rewritten into the build's own summary, and a build that left none is not a build this pass can stand on; run `uv run python -m rebuild.pipeline.run_m1` first"
        )

    spec = load_default_spec()
    before = run_m1_key()
    record = read_green_record(RUN_M1_GREEN)
    current = run_m1_skip_files(REPO_ROOT)

    tables: dict[str, tuple] = {}
    for config, decision in serialized.items():
        treaty_path = out_dir / f"treaties-{config}.tsv"
        try:
            tables[config] = (decision, table_module.read_treaty_tsv(treaty_path))
        except (OSError, ValueError) as error:
            raise SystemExit(
                f"{treaty_path} is missing or unreadable ({error}) — the defect gate reads the treaty tables beside the enumeration, so this build is not one this pass can adjudicate; run `uv run python -m rebuild.pipeline.run_m1` first"
            )

    console.phase("defect_gates")
    start = time.perf_counter()
    cell_glyphs = mint_cell_glyphs(spec, tables)
    defect_fields = _defect_summary_fields(_run_defect_gates(spec, tables, cell_glyphs))
    print(f"[t] defect_gates {time.perf_counter() - start:.1f}s", flush=True)
    pipeline_summary.update(defect_fields)
    summary_path.write_text(json.dumps(pipeline_summary, indent=2) + "\n")
    fingerprint.write_stage_a(REPO_ROOT, out_dir)
    if defect_fields["defect_errors"]:
        message = f"{len(defect_fields['defect_errors'])} defect-gate errors; see pipeline_summary.json"
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", message), started)
        raise SystemExit(message)

    console.phase("run_manual_pin_gate")
    start = time.perf_counter()
    pin_gate = run_manual_pin_gate(out_dir=out_dir, spec=spec)
    print(f"[t] run_manual_pin_gate {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(pin_gate, indent=2))
    pin_failure = manual_pin_gate_failure(pin_gate)
    if pin_failure is not None:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", pin_failure), started)
        raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")

    console.phase("run_oracle")
    start = time.perf_counter()
    oracle_summary = run_oracle(
        out_dir=out_dir,
        spec=spec,
        jobs=jobs,
        write_cache=False,
        fresh_cache=fresh_cache,
        memo_inputs=memo_inputs,
    )
    print(f"[t] run_oracle {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(oracle_summary, indent=2))
    gate = evaluate_run_m1_gate(pipeline_summary, pin_gate, oracle_summary)
    _record_cli_check(gate, started)
    if not gate.ok:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        raise SystemExit("; ".join(gate.failures) + "; see oracle_summary.json and divergence-audit.tsv")
    if gates_only_reuse(record, current) is not None:
        _settle_green(
            RUN_M1_GREEN,
            before,
            True,
            run_m1_key,
            "run_m1",
            files_of=lambda: run_m1_skip_files(REPO_ROOT),
        )
        return
    labels = moved_input_labels(record, current) or []
    offenders = [label for label in labels if not comparison_side_label(label)]
    if offenders:
        why = f"these inputs are build-side, so the artifacts on disk are not the ones they describe: {', '.join(offenders)}"
    elif record is None:
        why = "there is no prior green M1 build for this pass to stand on"
    elif not isinstance(record.get("files"), dict):
        why = "the last green M1 build predates the per-file record this decision is taken over"
    else:
        why = "nothing has moved since the last green M1 build, so that green already stands"
    console.warn(f"run_m1: green, but this pass recorded no green — {why}")


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
        console.warn(f"{label}: green, but its inputs changed while it ran — green not recorded")
        return
    record_green(green_path, key, files=files_of() if files_of is not None else None)
    where = green_path.relative_to(REPO_ROOT) if green_path.is_relative_to(REPO_ROOT) else green_path
    print(f"{label}: green — fingerprint recorded in {where}", flush=True)


def main(argv: list[str] | None = None) -> None:
    from rebuild.tools.artifact_cycle import sweep_job_budget

    sweep_jobs = sweep_job_budget()
    parser = argparse.ArgumentParser(description="Run the M1 integration pipeline and its Phase-2 gates.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=sweep_jobs,
        help=f"worker budget for the oracle and conformance shards, one process per acceptance configuration and no more, since that is all `_spawn_pool` will start; the default is the same `sweep_job_budget()` width the artifact cycle already passes rather than a checked-in one — {sweep_jobs} on this box, a CPU ceiling rather than a memory one for the reasons that budget's own docstring argues — so a hand run no longer walks the two belts a configuration at a time. `--jobs 1` is serial. The table build's own width, which is the memory-bound one, is --kernel-threads.",
    )
    parser.add_argument(
        "--conform-only",
        action="store_true",
        help="run only the font-vs-settle conformance sweep against the existing M1.otf and exit nonzero unless it passes",
    )
    parser.add_argument(
        "--gates-only",
        action="store_true",
        help="re-run the defect gate, the Manual-pin gate and the oracle against the M1.otf and tables already on disk, rewriting the defect fields of pipeline_summary.json, the gate summaries, the Stage A record and divergence-audit.tsv; refuses when those tables were built from other sources than the ones on disk, and records run_m1's green when everything that moved since the last green build is comparison-side",
    )
    parser.add_argument(
        "--fresh-oracle-cache",
        action="store_true",
        help="derive every row rather than serving any from the oracle's per-row verdict stores, and write fresh ones over them; under --gates-only, which may not write a build input, it declines to read the stores and leaves them where they are",
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
        help=(
            "how many configurations the kernel enumerates and folds at once, capped at the configuration count and the cores this process may actually run on; the ceiling is memory rather than CPU, so the default is derived from the box in hand rather than checked in — on this one "
            f"{describe_fit(kernel_exec.CONFIG_PEAK_BYTES)} — which AMS_KERNEL_THREADS short-circuits and this flag beats in turn"
        ),
    )
    args = parser.parse_args(argv)
    jobs = args.jobs if args.jobs and args.jobs > 1 else 1
    started = time.perf_counter()

    if args.gates_only:
        run_gates_only(out_dir=OUT_DIR, jobs=jobs, fresh_cache=args.fresh_oracle_cache)
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
        console.phase("run_font_conformance")
        start = time.perf_counter()
        conformance = run_font_conformance(max_length=args.conform_horizon, jobs=jobs)
        print(
            f"[t] run_font_conformance {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
        print(json.dumps(conformance, indent=2))
        verdict = evaluate_conform_gate(conformance)
        _settle_green(
            CONFORM_GREEN,
            before,
            verdict.ok,
            conform_key,
            "gate:conform",
            files_of=lambda: conform_skip_files(REPO_ROOT, args.conform_horizon),
        )
        _record_cli_check(verdict, started)
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

    _run_pregate_guards()
    inputs = tables_inputs()
    memo_inputs = settle_memo_inputs()
    spec = load_default_spec()
    before = run_m1_key()
    try:
        console.phase("run_total")
        start = time.perf_counter()
        summary = run(spec=spec, inputs=inputs, kernel_threads=args.kernel_threads)
        print(
            f"[t] run_total {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
        print(json.dumps(summary, indent=2))
        if summary["defect_errors"]:
            raise SystemExit(f"{len(summary['defect_errors'])} defect-gate errors; see pipeline_summary.json")
        console.phase("run_manual_pin_gate")
        start = time.perf_counter()
        pin_gate = run_manual_pin_gate(spec=spec)
        print(f"[t] run_manual_pin_gate {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(pin_gate, indent=2))
        pin_failure = manual_pin_gate_failure(pin_gate)
        if pin_failure is not None:
            raise SystemExit(f"{pin_failure}; see manual_pins_summary.json")
        console.phase("run_oracle")
        start = time.perf_counter()
        oracle_summary = run_oracle(
            spec=spec,
            jobs=jobs,
            fresh_cache=args.fresh_oracle_cache,
            memo_inputs=memo_inputs,
        )
        print(f"[t] run_oracle {time.perf_counter() - start:.1f}s", flush=True)
        print(json.dumps(oracle_summary, indent=2))
    except (SystemExit, readback.ReadbackError, emit_gsub.EmitError) as error:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", str(error)), started)
        if isinstance(error, SystemExit):
            raise
        raise SystemExit(str(error))
    gate = evaluate_run_m1_gate(summary, pin_gate, oracle_summary)
    _settle_green(
        RUN_M1_GREEN, before, gate.ok, run_m1_key, "run_m1", files_of=lambda: run_m1_skip_files(REPO_ROOT)
    )
    _record_cli_check(gate, started)
    if not gate.ok:
        raise SystemExit("; ".join(gate.failures) + "; see oracle_summary.json and divergence-audit.tsv")


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
