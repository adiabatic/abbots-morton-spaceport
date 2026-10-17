"""The M1 driver's gate half, the code the table build and the font compile never run: the gates that check the tables and the M1.otf a build has written under rebuild/out/m1, the guards that check the oracle's baselines, aliases and ledger before those gates run, and the command line `uv run python -m rebuild.pipeline.run_m1` hands to `main`.

It holds the pre-gate guards (`_run_pregate_guards`), the Manual-pin gate (`run_manual_pin_gate`), the baseline oracle and its row-cache helpers (`run_oracle`), the conformance sweep, serial or pooled (`run_font_conformance`), the units the pooled sweep, `make conform-deep` and the deep replay run in (`sweep_units`), the `--gates-only` rerun (`run_gates_only`), the check line and green settlement each invocation leaves when it finishes, stops at a gate, refuses or, as a bare run or a `--gates-only` pass, raises (`_record_cli_check`, `_settle_green`, `_exit_red` for a refusal or a stop before the run_m1 gate is evaluated, and `_recording_failure` for an exception raised past the run's own handlers; a child of the artifact cycle prints its check line for the cycle instead), and the Stage A record a build or a `--gates-only` pass deletes immediately before its first write under rebuild/out/m1 and writes back only once its gates pass (`_retract_stage_a`, `_settle_stage_a`). `main` parses the command line and runs `--gates-only` and `--conform-only` itself. A bare run goes to `run_m1.full_run`, which builds the tables and the font and then calls these gates.

`fingerprint.COMPARISON_CODE_MODULES` names this module, so it is in `pipeline_code_paths` and the run_m1 skip key but not in the tables' stamp (`fingerprint.table_code_paths`): an edit here re-runs the gates over the tables and font on disk instead of forcing a full build, and rebuild/test_build_code_closure.py checks that the build never reaches it. It reaches the build's own helpers through the `run_m1` module object at call time rather than importing their names, so a test that patches one on `run_m1` patches what this module calls. The `out_dir` defaults, the three YAML paths (`ALIAS_YAML`, `DIVERGENCES_YAML`, `KERN_SIDECAR_YAML`) and `load_default_spec` are bound when this module is imported, so a test patches those on this module or passes `out_dir`. `run_m1.full_run` loads the spec through `run_m1.load_default_spec`, so a test that drives the full run patches that binding instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import zlib
from concurrent.futures import as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterator, Mapping, NoReturn, Sequence

import yaml

from rebuild.pipeline import (
    baseline_subset,
    conform,
    fingerprint,
    kernel_exec,
    manual_pins,
    oracle,
    oracle_cache,
    run_m1,
)
from rebuild.pipeline import table as table_module
from rebuild.pipeline.labels import features_for_config
from rebuild.pipeline.model import CellId, GlyphRecord, ResolvedSpec
from rebuild.pipeline.settle import FormationGuard
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.table import DecisionTable
from rebuild.tools import console
from rebuild.tools.cycle_timings import (
    CYCLE_RUN_ENV,
    REFUSED_STATUS,
    CheckResult,
    parse_inner_timings,
    record_check,
    record_pool,
)
from rebuild.tools.memory_budget import describe_fit
from rebuild.tools.peak_rss import (
    peak_footprint_bytes,
    peak_rss_self_bytes,
    process_peak_rss_bytes,
    rss_token,
)

ALIAS_YAML = run_m1.REPO_ROOT / "rebuild" / "m1-aliases.yaml"
DIVERGENCES_YAML = run_m1.REPO_ROOT / "rebuild" / "m1-divergences.yaml"
KERN_SIDECAR_YAML = run_m1.REPO_ROOT / "glyph_data" / "senior_quikscript_kerning.yaml"


def run_font_conformance(
    out_dir: Path = run_m1.OUT_DIR,
    max_length: int = 4,
    jobs: int = 1,
    summary_name: str = "conform_summary.json",
) -> dict:
    """Run the exhaustive font-versus-settlement sweep: the per-edit sweep at `max_length` 4 over every settlement configuration, the overlay configuration at `conform.OVERLAY_MAX_LENGTH` whatever `max_length` is, and a hand run past `conform.SWEEP_MAX_LENGTH` through `--conform-max-length`. `make conform-deep` does not call it: `rebuild/tools/deep_sweep.py` runs its own pool. The tables under `out_dir` are read only for the glyph inventory `run_m1.mint_cell_glyphs` needs to name settled cells and read their anchors. The sweep itself takes no table, because it checks HarfBuzz's behavior against the kernel's, and read-back has already checked that the font holds the rules the build planned. A stamp mismatch stops with an error instead of rebuilding: the enumeration costs a whole kernel fan-out, and an inventory built here could describe runes that have since changed. The split-buffer check runs as part of this sweep, on every text that contains a splitter.

    Above `jobs` 1 the sweep runs in units (`_pooled_conformance`): the ss10 overlay whole and each settlement configuration once per symbol of the alphabet (`sweep_units`), one spawn process each. The serial path sweeps each configuration whole inside `conform.run_conformance`, and both write the same summary. The pooled path computes the `doc/rebuild-design.md` §5.7 formation-guard verdicts (`kernel_exec.guard_sweep`) once for the whole run and passes them with each submission. A spawned worker inherits nothing, so each would otherwise build the crate and sweep the spec itself.

    At the per-edit maximum length, each settlement configuration's walks share its settle memo with the string replay that fills it and with the oracle's walk over the same texts, through a file under `out_dir` keyed per family as the oracle row cache is (`conform.settle_memo_files`, from `run_m1.settle_memo_inputs` taken before the spec loads). The replay writes what it settled, each later phase loads it and writes back what it added, and a rune edit invalidates only the entries whose windows name an edited family. This sweep also prunes the rows no text reaches: the serial path in its walk's save, the pooled path in the parent over the units' reaches (`_pooled_conformance`). A deeper sweep shares nothing: its memo is a multiple of the conformance sweep's, and a file that size would cost the next conformance-sweep and oracle workers more to decode than they save.

    A pooled conformance sweep at `conform.SWEEP_MAX_LENGTH` writes one `conform-sweep` pool record (`cycle_timings.record_pool`), which `make job-costs` reports: one observation per configuration, its units' highest peak footprint (`_conformance_unit`), and one per settle-memo absorb that ran, labeled `<config> absorb`. The serial path starts no pool, and a hand sweep past `conform.SWEEP_MAX_LENGTH` has a different unit peak, so neither records one.
    """
    inputs = run_m1.tables_inputs()
    memo_inputs = run_m1.settle_memo_inputs()
    spec = load_default_spec()
    start = time.perf_counter()
    serialized = run_m1.serialized_tables(out_dir, inputs)
    if serialized is None:
        raise SystemExit(
            f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; the sweep no longer rebuilds the fixpoint in process"
        )
    decisions: Mapping[str, DecisionTable | tuple[DecisionTable, ...]] = serialized
    print(f"[t] load_tables {time.perf_counter() - start:.1f}s", flush=True)
    cell_glyphs = run_m1.mint_cell_glyphs(spec, decisions)
    settle_memos = (
        conform.settle_memo_files(out_dir, spec, memo_inputs)
        if max_length == conform.SWEEP_MAX_LENGTH
        else {}
    )
    if jobs > 1:
        report = _pooled_conformance(spec, out_dir, max_length, jobs, cell_glyphs, settle_memos)
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
        "divergences": report.divergence_count,
        "pass": report.passed,
        "notes": report.notes,
    }
    for divergence in (exemplar.divergence for exemplar in report.exemplars):
        summary.setdefault("divergence_exemplars", []).append(
            f"{divergence.config} {':'.join(f'{ord(ch):04X}' for ch in divergence.text)} position {divergence.position} [{divergence.kind}] expected {divergence.expected} got {divergence.got}"
        )
    return summary


def run_manual_pin_gate(out_dir: Path = run_m1.OUT_DIR, spec: ResolvedSpec | None = None) -> dict:
    if spec is None:
        spec = load_default_spec()
    report = manual_pins.run_gate(out_dir / "M1.otf", spec)
    summary = manual_pins.summarize(report)
    (out_dir / "manual_pins_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def manual_pin_gate_failure(summary: Mapping) -> str | None:
    """Return why the Manual-pin gate does not count as passed, or None. `pass` alone means no disagreements, which a gate that replayed nothing also satisfies, so this also requires at least one pin in scope and every pin in scope replayed against the font."""
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
    """Move this run's staged stores into place, but only if every key recomputed from the sources now matches what the run used: the row keys and stamps from the rune tree, and the position keys and stamp from the font and the kern sidecar. If the inputs changed during the run, its verdicts describe nothing on disk, and a store recorded under the wrong digest would be served as current indefinitely instead of failing once. So on any change nothing is written and a warning names the change, which costs the next pass an uncached oracle and nothing else. An alias map edited during the run into a form `alias_family_digests` rejects, or a font that cannot be digested again, is handled the same way."""
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
        console.say(f"oracle row cache: written for {', '.join(promoted)}")
    else:
        console.warn("oracle row cache: not written — a configuration staged no store")


def oracle_row_cache_keys(
    spec: ResolvedSpec, out_dir: Path
) -> tuple[dict[str, str], dict[str, oracle_cache.EnvironmentStamp]]:
    """Return the oracle row cache's two keys from one read of their sources: a digest per rune family, and a whole-store stamp per acceptance configuration. They are computed once before the run and again at promotion, which is how the run detects that what it would record differs from what it built from."""
    keys = oracle_cache.family_keys(run_m1.REPO_ROOT, spec, ALIAS_YAML)
    stamps = {
        config: oracle_cache.environment_stamp(
            run_m1.REPO_ROOT,
            spec,
            config,
            features_for_config(config),
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
    """Return the position store's two keys, computed from the font the oracle is about to shape against and the kern sidecar it reads: `oracle_cache.position_keys` over the row keys already computed. With no compiled font under `out_dir` there is nothing to shape, and it returns `(None, None)`, which records every position as unshaped and serves none."""
    font = Path(out_dir) / "M1.otf"
    if not font.is_file():
        return None, None
    return oracle_cache.position_keys(run_m1.REPO_ROOT, keys, font, KERN_SIDECAR_YAML)


def _report_oracle_cache(
    spec: ResolvedSpec,
    out_dir: Path,
    keys: Mapping[str, str],
    stamps: Mapping[str, oracle_cache.EnvironmentStamp],
    position_keys: Mapping[str, str] | None = None,
    position_stamp: oracle_cache.EnvironmentStamp | None = None,
) -> None:
    """Print, before the fan-out, which stored verdicts will and will not be served. The hit rate is bimodal, and a whole-store drop looks like a bug unless the line that caused it is named. A changed stamp line (a pipeline module, a predicate class gaining a member, the settlement mode flags `kernel_exec.settlement_flags` returns) re-derives every row of every configuration; unless the line is one of `oracle_cache.POSITION_ROW_STAMP_LABELS` or `oracle_cache.position_stale_mask` makes every position stale (the position stamp moved too, a moved family is one the registry cannot place, or there is no font), each position whose settled cells did not change is still served, and otherwise the store is dropped, the same test `oracle_cache.load_store` makes. A changed family key re-derives only the rows that can reach that family, the usual result of a rune edit. The position store is reported on its own line: a changed position stamp line (the position comparison's code closure, the kern sidecar, the font's helpers) re-shapes every row while the row verdicts are still served, and a changed position key re-shapes only the rows that reach that family, the usual result of a glyph edit."""
    recorded = oracle_cache.read_header(oracle_cache.store_path(out_dir, conform.ACCEPTANCE_CONFIGS[0]))
    if recorded is None:
        console.say("oracle row cache: no store on disk — this pass derives every row and writes one")
        return
    stamp = stamps[conform.ACCEPTANCE_CONFIGS[0]]
    stored_lines = {
        label: digest
        for label, _, digest in (str(line).partition("\t") for line in recorded.get("environment") or ())
    }
    moved_stamp = oracle_cache.moved_note(stored_lines, stamp.labels)
    stored_position_lines = {
        label: digest
        for label, _, digest in (
            str(line).partition("\t") for line in recorded.get("position_environment") or ()
        )
    }
    moved_position_stamp = (
        None
        if position_stamp is None
        else oracle_cache.moved_note(stored_position_lines, position_stamp.labels)
    )
    stored_keys = {str(name): str(value) for name, value in (recorded.get("family_keys") or {}).items()}
    if moved_stamp is not None:
        if any(
            stored_lines.get(label) != stamp.labels.get(label)
            for label in oracle_cache.POSITION_ROW_STAMP_LABELS
        ):
            console.warn(f"oracle row cache: dropped — the stamp moved at {moved_stamp}")
            return
        positions_outlive = not oracle_cache.position_stale_mask(
            spec,
            oracle_cache.moved_families(stored_keys, dict(keys)),
            recorded,
            position_stamp,
            position_keys,
        ).everything
        console.warn(
            f"oracle row cache: re-deriving every row — the stamp moved at {moved_stamp}; each position whose settled cells did not change is still served"
            if positions_outlive
            else f"oracle row cache: dropped — the stamp moved at {moved_stamp}, and no stored position can be served"
        )
    else:
        moved_keys = oracle_cache.moved_note(stored_keys, dict(keys))
        if moved_keys is None:
            console.say("oracle row cache: the stamp and every family key still stand")
        else:
            console.warn(f"oracle row cache: re-deriving the rows that reach {moved_keys}")
    if position_keys is None or position_stamp is None:
        console.say("oracle position store: no font to shape against — every position is shaped")
        return
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
        console.say("oracle position store: the position stamp and every glyph key still stand")
    else:
        console.warn(f"oracle position store: re-shaping the rows that reach {moved_position_keys}")


@dataclass(frozen=True)
class SweepUnit:
    """One process's share of a pooled conformance sweep (`run_font_conformance` and `make conform-deep`'s `deep_sweep.run_sweep`), or of the deep replay, which walks the settlement units (`deep_replay.walk_units`): a configuration, the symbol its texts end in or None for a configuration swept whole, the count of texts it shapes, which is the `sequences` its worker returns, and its trigger letters: when there are any, the unit shapes only the texts that contain one (`conform.sweep_texts`), which only the deep sweep asks for."""

    config: str
    last: str | None
    texts: int
    triggers: frozenset[str] = frozenset()

    @property
    def overlay(self) -> bool:
        return self.config in conform.OVERLAY_CONFIGS


def sweep_units(
    alphabet: Sequence[str], max_length: int, triggers: Mapping[str, frozenset[str]] | None = None
) -> tuple[SweepUnit, ...]:
    """Return the sweep's units in the order the pool takes them: each overlay configuration whole, to `conform.OVERLAY_MAX_LENGTH`, then each settlement configuration in `conform.SETTLEMENT_CONFIGS` order, one unit per symbol of `alphabet` in its order, each shaping the texts of length 1 to `max_length` that end in its symbol. A settlement configuration that `triggers` maps to letters shapes only the texts that contain one of them: all of a unit's texts when its symbol is one, and otherwise each length's texts less the ones whose other characters avoid them all."""
    size = len(alphabet)
    overlay = sum(size**length for length in range(1, conform.OVERLAY_MAX_LENGTH + 1))
    unit = sum(size ** (length - 1) for length in range(1, max_length + 1))

    def settlement_unit(config: str, symbol: str) -> SweepUnit:
        letters = (triggers or {}).get(config, frozenset())
        if not letters or symbol in letters:
            return SweepUnit(config, symbol, unit, letters)
        rest = size - len(letters.intersection(alphabet))
        texts = sum(size ** (length - 1) - rest ** (length - 1) for length in range(1, max_length + 1))
        return SweepUnit(config, symbol, texts, letters)

    return tuple(SweepUnit(config, None, overlay) for config in conform.OVERLAY_CONFIGS) + tuple(
        settlement_unit(config, symbol) for config in conform.SETTLEMENT_CONFIGS for symbol in alphabet
    )


def config_texts(
    spec: ResolvedSpec, max_length: int, triggers: Mapping[str, frozenset[str]] | None = None
) -> dict[str, int]:
    """Return how many texts each acceptance configuration's units shape between them: every text of one letter up to its maximum length over the spec's alphabet, which is `max_length` for a settlement configuration and `conform.OVERLAY_MAX_LENGTH` for an overlay, and for a settlement configuration that `triggers` maps to letters, only the texts that contain one of them. `run_font_conformance` and `deep_sweep.run_sweep` check each configuration's merged `sequences` against it before they write anything, and `deep_replay.main` each settlement configuration's merged texts and skipped texts."""
    alphabet = conform.spec_alphabet(spec)
    size = len(alphabet)
    counts: dict[str, int] = {}
    for config in conform.ACCEPTANCE_CONFIGS:
        longest = conform.OVERLAY_MAX_LENGTH if config in conform.OVERLAY_CONFIGS else max_length
        letters = (triggers or {}).get(config, frozenset())
        rest = size - len(letters.intersection(alphabet))
        counts[config] = sum(
            size**length - (rest**length if letters else 0) for length in range(1, longest + 1)
        )
    return counts


def _peak_footprint() -> int:
    """This process's peak footprint (`peak_rss.peak_footprint_bytes`), or its peak resident set where the footprint cannot be read."""
    peak = peak_footprint_bytes()
    return peak if peak is not None else peak_rss_self_bytes()


def _conformance_unit(
    spec: ResolvedSpec,
    font_path: Path,
    config: str,
    last: str | None,
    max_length: int,
    glyphs: Mapping[CellId, GlyphRecord] | None,
    guard_verdicts: FormationGuard | None,
    settle_memo: conform.SettleMemoFile | None,
) -> tuple[conform.ConformanceConfigResult, int]:
    """Run one unit of the pooled conformance sweep in a pool worker (`conform.conformance_config_worker` with `last`) and return its peak footprint (`_peak_footprint`) with the result, so the parent can write the `conform-sweep` pool record. The pool runs one unit per process, so the reading is this unit's own. It is a footprint rather than a resident peak because every settlement unit of a configuration maps the same settle memo file, whose clean pages the page cache holds once however many units touch them, while each unit's resident peak counts them again. The peak is returned in a pair instead of as a field on the result because conform.py is in `oracle_cache.ORACLE_ROW_CODE_PATHS`, and an edit there would invalidate every settle memo and row store."""
    result = conform.conformance_config_worker(
        spec, font_path, config, max_length, glyphs, guard_verdicts, settle_memo, last=last
    )
    return result, _peak_footprint()


def _absorb_sweep_memo(
    memo: conform.SettleMemoFile,
    parts: Sequence[Path],
    spec: ResolvedSpec,
    identity: tuple[int, int, int, int] | None,
    reached: bytes,
    prune: bool,
    discard: bool,
) -> tuple[bool, int, int, float, int]:
    """Prune one configuration's settle memo file to the rows its conformance-sweep units reached and merge their parts into it (`conform.absorb_sweep_memo`) in a pool worker. Return whether the file was written, the rows pruned, the windows the parts added, the seconds the absorb took, and the worker's peak footprint (`_peak_footprint`), which goes into the `conform-sweep` pool record as `<config> absorb`. The write holds the configuration's rows, their remapped copies, and the index at once, so it runs in a worker rather than in the parent."""
    started = time.perf_counter()
    written, pruned, fresh = conform.absorb_sweep_memo(memo, parts, spec, identity, reached, prune, discard)
    return written, pruned, fresh, time.perf_counter() - started, _peak_footprint()


@dataclass
class _MemoFold:
    """One settlement configuration's settle-memo use across its conformance-sweep units, folded in the parent as each unit finishes: the version of the file the first unit mapped, the union of every unit's reached rows as one integer (each flag byte is zero or one, so OR-ing the little-endian integers ORs the flags), the parts the units wrote, and their summed memo seconds. `agree` turns False when a unit mapped another version of the file than the first, and then nothing is pruned. `retired` turns True when a unit's probe found the file corrupt, and then the absorb drops every row of the file rather than the unreached ones (`conform.absorb_sweep_memo`'s `discard`)."""

    identity: tuple[int, int, int, int] | None = None
    rows: int = 0
    live: int = 0
    loaded: int = 0
    stale: int = 0
    reached: int = 0
    parts: list[Path] = field(default_factory=list)
    seconds: float = 0.0
    units: int = 0
    agree: bool = True
    retired: bool = False

    def add(self, reach: conform.MemoReach, part: Path) -> None:
        """Fold in one unit's reach, whose part, if it wrote one, is at `part`."""
        if not self.units:
            self.identity, self.rows, self.live = reach.identity, reach.rows, reach.live
            self.loaded, self.stale = reach.loaded, reach.stale
        elif (reach.identity, reach.rows) != (self.identity, self.rows):
            self.agree = False
        self.units += 1
        self.retired = self.retired or reach.retired
        if self.agree:
            self.reached |= int.from_bytes(zlib.decompress(reach.reached), "little")
        if reach.part:
            self.parts.append(part)
        self.seconds += reach.seconds

    @property
    def pruned(self) -> int:
        """The live rows no unit reached, or zero when the units disagree about the file or a unit retired it."""
        return self.live - self.reached.bit_count() if self.agree and not self.retired else 0


def _pooled_conformance(
    spec: ResolvedSpec,
    out_dir: Path,
    max_length: int,
    jobs: int,
    glyphs: Mapping[CellId, GlyphRecord],
    settle_memos: Mapping[str, conform.SettleMemoFile],
) -> conform.ConformReport:
    """Sweep every unit of `sweep_units` at `max_length`, `jobs` at a time, one spawn process per unit (`run_m1._spawn_pool` with `max_tasks_per_child=1`), and return the merged report; `run_font_conformance` writes it.

    Every unit is submitted up front, in unit order. A settlement unit whose configuration has a settle memo maps the shared file and writes the windows it settled fresh as a part in a scratch directory that lives as long as the pool, so a failed run leaves no part where a later run would read it. As each unit finishes, the parent folds its reach into its configuration's `_MemoFold` and drops the unit's flags, so the parent holds one integer the size of the file's row count per configuration however many units there are. When a unit raises, the units still queued are cancelled and the error propagates before any absorb is submitted.

    Once every unit has finished, each configuration's units merge into its result (`conform.merge_unit_results`), whose sequences must be every text of its lengths (`config_texts`), or this raises before any file is written, so no memo is ever pruned from a partial set of units. Only then does the pool absorb each configuration whose units wrote a part, left a live row unreached, or found the file corrupt (`_absorb_sweep_memo`); a configuration with neither (a current memo) gets no task and its file is not touched. One `[t] settle_memo` line per settlement configuration follows (`conform.settle_memo_line`), with the units' loaded and stale counts, the absorb's fresh and pruned counts, and the units' memo seconds plus the absorb's, the same line the serial sweep's walk prints. The configurations then merge in `conform.ACCEPTANCE_CONFIGS` order, so the report is the serial sweep's at any width and in any finishing order. At `conform.SWEEP_MAX_LENGTH` this writes the `conform-sweep` pool record.
    """
    font_path = out_dir / "M1.otf"
    units = sweep_units(conform.spec_alphabet(spec), max_length)
    kernel_exec.ensure_built()
    guard_verdicts = kernel_exec.guard_sweep(spec)
    collected: dict[int, conform.ConformanceConfigResult] = {}
    worker_peaks: dict[str, int] = {}
    folds = {config: _MemoFold() for config in settle_memos}
    absorbed: dict[str, tuple[bool, int, int, float]] = {}
    with tempfile.TemporaryDirectory(prefix="conform-memo-parts.") as scratch:
        parts = {slot: Path(scratch) / f"{unit.config}.{slot}.gz" for slot, unit in enumerate(units)}
        with run_m1._spawn_pool(jobs, len(units), max_tasks_per_child=1) as pool:
            futures = {
                pool.submit(
                    _conformance_unit,
                    spec,
                    font_path,
                    unit.config,
                    unit.last,
                    max_length,
                    glyphs,
                    guard_verdicts,
                    (
                        replace(settle_memos[unit.config], write_path=parts[slot])
                        if unit.config in settle_memos
                        else None
                    ),
                ): slot
                for slot, unit in enumerate(units)
            }
            try:
                for future in as_completed(futures):
                    slot = futures[future]
                    result, peak = future.result()
                    worker_peaks[result.config] = max(worker_peaks.get(result.config, 0), peak)
                    if result.memo_reach is not None:
                        folds[result.config].add(result.memo_reach, parts[slot])
                        result.memo_reach = None
                    collected[slot] = result
                    console.progress(len(collected), len(units), "units")
            except BaseException:
                pool.shutdown(wait=True, cancel_futures=True)
                raise
            expected = config_texts(spec, max_length)
            results = []
            for config in conform.ACCEPTANCE_CONFIGS:
                result = conform.merge_unit_results(
                    config, [collected[slot] for slot, unit in enumerate(units) if unit.config == config]
                )
                if result.sequences != expected[config]:
                    raise RuntimeError(
                        f"conformance sweep[{config}]: its units shaped {result.sequences} texts, not the {expected[config]} of every length it sweeps"
                    )
                results.append(result)
            absorbs = {
                config: pool.submit(
                    _absorb_sweep_memo,
                    settle_memos[config],
                    fold.parts,
                    spec,
                    fold.identity,
                    zlib.compress(fold.reached.to_bytes(fold.rows, "little"), 1),
                    fold.agree,
                    fold.retired,
                )
                for config, fold in folds.items()
                if fold.parts or fold.pruned or fold.retired
            }
            for config, future in absorbs.items():
                written, pruned, fresh, seconds, peak = future.result()
                absorbed[config] = (written, pruned, fresh, seconds)
                worker_peaks[f"{config} absorb"] = peak
    for config in conform.SETTLEMENT_CONFIGS:
        fold = folds.get(config)
        if fold is None:
            continue
        if not fold.agree:
            console.warn(
                f"settle memo: {settle_memos[config].path} was mapped at more than one version by {config}'s units; nothing is pruned this sweep"
            )
        written, pruned, fresh, seconds = absorbed.get(config, (False, 0, 0, 0.0))
        print(
            conform.settle_memo_line(
                config, fold.seconds + seconds, fold.loaded, fold.stale, fresh, pruned, written
            ),
            file=sys.stderr,
            flush=True,
        )
    if max_length == conform.SWEEP_MAX_LENGTH:
        record_pool(
            "conform-sweep",
            width=min(jobs, len(units)),
            worker_peaks=worker_peaks,
            controller_peak_bytes=peak_rss_self_bytes(),
        )
    return conform.merge_conformance_results(font_path, results)


def _absorb_settle_memo_parts(
    memo: conform.SettleMemoFile, parts: Sequence[Path], spec: ResolvedSpec
) -> tuple[bool, float, int]:
    """Merge one configuration's parts into its shared settle memo file (`conform.absorb_settle_memo_parts`) in a pool worker. Return whether the file was rewritten, the seconds the merge took, and the worker's peak memory (`peak_rss_self_bytes`). The parent reports a rewrite as `[t] settle_memo_absorb <config>` beside the replay's `settle_memo_emit` lines; it is a whole-file write, once per pass for each configuration that settled anything fresh. The peak goes into the pool record beside the row ranges' peaks. Like every reading there it is the process's peak memory, so it reads at or above the range its worker ran before it."""
    started = time.perf_counter()
    written = conform.absorb_settle_memo_parts(memo, parts, spec)
    return written, time.perf_counter() - started, peak_rss_self_bytes()


def _shard_settle_memo(
    memo: conform.SettleMemoFile | None, scratch: Path, shard: oracle.OracleShard
) -> conform.SettleMemoFile | None:
    """Return the settle memo file one row range reads and writes: it reads the configuration's shared file and writes its own part under the run's scratch directory, whether or not the configuration is split into several ranges. So no range replaces the shared file, either over another range of its configuration or over the witness stage's process, which can be merging its own part into that file while the pool runs. The parent merges every part once all the ranges have finished and the witness stage's process has returned."""
    if memo is None:
        return None
    return replace(memo, write_path=oracle.settle_memo_part(scratch, shard.config, shard.index))


def run_oracle(
    out_dir: Path = run_m1.OUT_DIR,
    spec: ResolvedSpec | None = None,
    jobs: int = 1,
    write_cache: bool = True,
    fresh_cache: bool = False,
    memo_inputs: oracle_cache.SettleMemoInputs | None = None,
    memo_ready: Callable[[], None] | None = None,
) -> dict:
    """Run the baseline oracle (`rebuild/M1-PLAN.md` §6) over the subset tables, reading the row cache before the first row and writing it after the last. Above `--jobs 1` the unit of work is a row range of one configuration's table: `oracle.oracle_shard_plan` splits every table (by the row counts the subset stamp records, `baseline_subset.subset_row_counts`; a table the stamp does not count stays one range) so that each of `jobs` workers gets an equal share of the whole, and `run_m1._spawn_pool` starts up to `jobs` workers, one per range at most. Each range writes its own audit segment and store segment, and writes the settle memo windows it settled fresh as a part. The parent merges the ranges' tallies (`oracle.merge_config_shards`), concatenates the segments (`oracle.join_oracle_audit`, `oracle_cache.join_store_segments`), and merges the parts into the shared memo files (`conform.absorb_settle_memo_parts`, on the same pool, each merge's peak recorded in the pool record as `<config> absorb` beside the ranges'), all in row order, so the summary, the audit and every store's records match an unsplit run. The crate's formation-guard verdicts are computed once here and passed with every submission, as in the conformance sweep's fan-out. `[t] oracle_setup` times this process's work before the pool takes over, from entry to the last range's submission: the cache keys, the shard plan, the guard sweep and the submissions.

    `memo_inputs` is `run_m1.settle_memo_inputs` as the caller took it before loading `spec`, and names the settle memo files this pass shares with the conformance sweep (`conform.settle_memo_files`). The oracle's rows are the conformance sweep's texts, so a settlement configuration whose file the replay or the conformance sweep already wrote under these keys settles nothing here, and one neither has reached yet writes the file the conformance sweep will load. A caller with no inputs shares nothing, and the overlay configuration has no memo, since its worker settles nothing. `memo_ready`, when given, is called before this pass writes a shared settle memo file. On the pooled path that is once every range has finished, because ranges write only parts and the parent's merges are the pass's only writes to the files. On the `--jobs 1` path it is before the first walk, because those walks replace the files whole as they go. `run_m1.full_run` passes `run_m1.TableGates.wait_for_memo`, which returns once the witness stage has merged its own part into each file. That lets the pool start after the string replay alone while the witness stage still runs: a range reads a whole file whether it maps it before or after the witness stage's merge, and every write this pass makes includes what the witness stage wrote.

    The cache keys are computed once here (the row keys from the rune tree, the position keys from the compiled font and the kern sidecar) and passed to the workers, then computed again at promotion, where a store is written only if no stamp and no key changed while the run used them. The second computation is needed because `fingerprint.rune_digests` reads the rune files from disk, a full run takes minutes, and a long run is usually detached while editing continues. Without it, a rune edited mid-run would be recorded under a digest the stored verdicts were never built from, and later passes would serve pre-edit verdicts as current indefinitely. `_settle_green`'s recompute-before-recording and `artifact_cycle`'s green keys follow the same rule for the same reason.

    Failing to compute those keys does not fail the gate. `alias_family_digests` rejects an alias head with no rune digest behind it, and the alias map is a hand-edited file that one typo can make unreadable. So a key that cannot be computed leaves this pass with no cache: every row is derived, no store is written, and the gate runs as it does without a cache.

    Staging is inside this run's pid-named audit scratch directory, so a killed run's stores, segments and memo parts are deleted by `discard_oracle_audit_scratch` along with its audit segments, and two oracles sharing an `out_dir` (a `--gates-only` pass beside a cycle) cannot read or promote each other's files. Promotion happens only after `join_oracle_audit` has accepted the audit, because a store describing an audit that was never written is worse than no store. A split configuration where one range staged no segment is neither joined nor promoted, the all-or-nothing rule `promote_stores` already follows.

    Every range's peak is recorded as one observation of the `oracle-shard` pool (`cycle_timings.record_pool`), which `make job-costs` compares against `artifact_cycle.ORACLE_SHARD_BYTES`, and every absorb's, labeled `<config> absorb`, which it compares against `artifact_cycle.ORACLE_ABSORB_BYTES`.
    """
    setup_started = time.perf_counter()
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
            console.say("oracle row cache: distrusted for this pass — every row is derived")
        else:
            _report_oracle_cache(spec, out_dir, keys, stamps, position_keys, position_stamp)
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
            shards = oracle.oracle_shard_plan(jobs, baseline_subset.subset_row_counts(out_dir))
            segments = {config: 0 for config in conform.ACCEPTANCE_CONFIGS}
            for shard in shards:
                segments[shard.config] = shard.of
            kernel_exec.ensure_built()
            guard_verdicts = kernel_exec.guard_sweep(spec)
            landed: dict[str, list[tuple[oracle.OracleShard, oracle.OracleConfigResult]]] = {
                config: [] for config in conform.ACCEPTANCE_CONFIGS
            }
            width = min(jobs, len(shards))
            with run_m1._spawn_pool(jobs, len(shards)) as pool:
                futures = {
                    pool.submit(
                        oracle.oracle_config_worker,
                        spec,
                        out_dir,
                        ALIAS_YAML,
                        DIVERGENCES_YAML,
                        shard.config,
                        out_dir / "M1.otf",
                        KERN_SIDECAR_YAML,
                        audit_dir=scratch,
                        row_cache=row_cache,
                        settle_memo=_shard_settle_memo(settle_memos.get(shard.config), scratch, shard),
                        guard_verdicts=guard_verdicts,
                        shard=shard,
                    ): shard
                    for shard in shards
                }
                console.timing("oracle_setup", time.perf_counter() - setup_started)
                done = 0
                for future in as_completed(futures):
                    result = future.result()
                    landed[result.config].append((futures[future], result))
                    done += 1
                    console.progress(done, len(shards), "shards")
                if memo_ready is not None:
                    memo_ready()
                merges = {
                    config: pool.submit(
                        _absorb_settle_memo_parts,
                        memo,
                        [
                            oracle.settle_memo_part(scratch, config, index)
                            for index in range(segments[config])
                        ],
                        spec,
                    )
                    for config, memo in settle_memos.items()
                }
                absorbed = []
                worker_peaks = {
                    shard.label: result.peak_rss_bytes for pairs in landed.values() for shard, result in pairs
                }
                for config, future in merges.items():
                    written, seconds, peak = future.result()
                    worker_peaks[f"{config} absorb"] = peak
                    if written:
                        absorbed.append(config)
                        console.timing(f"settle_memo_absorb {config}", seconds)
            if absorbed:
                console.say(f"settle memo: absorbed the ranges' parts for {', '.join(absorbed)}")
            record_pool(
                "oracle-shard",
                width=width,
                worker_peaks=worker_peaks,
                controller_peak_bytes=peak_rss_self_bytes(),
            )
            in_row_order = {
                config: [
                    result for _shard, result in sorted(landed[config], key=lambda pair: pair[0].first_row)
                ]
                for config in conform.ACCEPTANCE_CONFIGS
            }
            ordered = [
                oracle.merge_config_shards(in_row_order[config]) for config in conform.ACCEPTANCE_CONFIGS
            ]
            report = oracle.merge_oracle_results(ordered)
            oracle.join_oracle_audit(
                out_dir, scratch, conform.ACCEPTANCE_CONFIGS, report.divergent_rows, segments=segments
            )
            if row_cache is not None and row_cache.write_dir is not None:
                for merged in ordered:
                    if segments[merged.config] > 1 and merged.pass_ordinal is not None:
                        stamp = row_cache.environment[merged.config]
                        oracle_cache.join_store_segments(
                            scratch,
                            merged.config,
                            [result.rows_compared for result in in_row_order[merged.config]],
                            stamp,
                            stamp.labels["subset"],
                            merged.pass_ordinal,
                            row_cache.family_keys,
                            row_cache.position_environment,
                            row_cache.position_keys,
                        )
        else:
            if memo_ready is not None:
                memo_ready()
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
        "multi_matched": report.multi_matched_count,
        "notes": report.notes,
    }
    for row in report.unmatched_exemplars[: oracle.ORACLE_UNMATCHED_EXEMPLARS]:
        summary.setdefault("unmatched_exemplars", []).append(
            f"{row.config} {row.codepoints} {'|'.join(row.baseline_glyphs)} -> {'|'.join(row.new_cells)} {row.divergence_tags}"
        )
    (out_dir / "oracle_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _record_cli_check(result: CheckResult, started: float) -> None:
    """Record this invocation's check result in the timings journal, unless the artifact cycle is recording it. The result is recorded before the final `SystemExit`, so the exit cannot change it. `CYCLE_RUN_ENV` in the environment means the artifact cycle started this run and records its result under its own run id, so this function prints the result on one `console.CHECK` line instead, which the cycle files when the summaries the child left do not say why it stopped (`artifact_cycle._reported_red`), keeping one journal line per invocation. When this invocation built tables, the line carries the build's phase lines (`run_m1._build_phase_lines`) as `inner`, parsed as the cycle parses a run_m1 step's output, so a build run by hand can be paired with another as a cycle's can."""
    if CYCLE_RUN_ENV in os.environ:
        reported = {
            "check": result.check,
            "outcome": result.outcome,
            "status": result.status,
            "failures": list(result.failures),
        }
        console.say(console.CHECK + json.dumps(reported))
        return
    record_check(
        result,
        argv=sys.argv,
        elapsed_s=time.perf_counter() - started,
        peak_rss_bytes=process_peak_rss_bytes(),
        inner=parse_inner_timings("\n".join(run_m1._build_phase_lines)) or None,
    )


def _failed_check(check: str, message: str, status: str = "FAILED") -> CheckResult:
    """Return the check result for a run that failed before its gate was evaluated: a defect gate that stopped the build, a pin gate failure, a read-back or emit error, or, with `REFUSED_STATUS`, a refusal before any gate ran. The error message is all that is known, so it is the only failure text, and there are no failed ids because nothing enumerated cases."""
    return CheckResult(check=check, outcome="red", status=status, failures=[message], failed_ids=[])


def _record_red(message: str, started: float, *, status: str, key: str | None = None) -> None:
    """Settle run_m1's green red over `key`, the key the run took before its gates, or over the key of the sources on disk when it stopped before taking one, then record the red check line (`_failed_check` with `message` and `status`), timed from `started`. A red clears only a green over the same content (`_settle_green`). So a hand run settles its red as the cycle settles a red run_m1 child (`artifact_cycle._do_run_m1`), and no later `--gates-only` pass can stand on a green over the content this run refused or failed on. Under `CYCLE_RUN_ENV` the cycle settles the green, so the key is not computed, and the check line goes to the cycle (`_record_cli_check`)."""
    from rebuild.tools.artifact_cycle import run_m1_skip_fingerprint
    from rebuild.tools.cycle_paths import RUN_M1_GREEN

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(run_m1.REPO_ROOT)

    if CYCLE_RUN_ENV not in os.environ:
        _settle_green(RUN_M1_GREEN, run_m1_key() if key is None else key, False, run_m1_key, "run_m1")
    _record_cli_check(_failed_check("run_m1", message, status), started)


def _exit_red(
    message: str, started: float, *, status: str = REFUSED_STATUS, key: str | None = None
) -> NoReturn:
    """Stop a run_m1 invocation that refused, or stopped before its run_m1 gate was evaluated, with `message`, once `_record_red` has settled the red and recorded its check line under `status`."""
    _record_red(message, started, status=status, key=key)
    raise SystemExit(message)


@contextmanager
def _recording_failure(started: float) -> Iterator[None]:
    """Record an exception a bare run or a `--gates-only` pass raises past its own handlers as a FAILED red naming the exception's type and message (`_record_red`), then let it propagate with its traceback, which names where it was raised. `main` runs both under it, so such a run that fails leaves a check line unless it is interrupted or killed, whatever it failed on: a contact allow-list or a rune that does not load, or a ledger or alias map the pre-gate guards accept and the oracle cannot use. A SystemExit passes through untouched, because the run records its line before each exit it takes."""
    try:
        yield
    except Exception as error:
        _record_red(f"{type(error).__name__}: {error}", started, status="FAILED")
        raise


def _run_pregate_guards(out_dir: Path = run_m1.OUT_DIR) -> None:
    """Run the checks that come before any gate, on both the build path and the `--gates-only` path, before either builds a table. They check that every source baseline table was shaped by the site font on disk (`baseline_subset.ensure_fresh` compares each header's font_sha256 with the font that header names on every call, because `make all` rewrites that font without changing the stamp key), refilter the subset baselines when they no longer match the sources on disk, check that every old glyph name in those subsets has an alias in an alias map that loads (`oracle.load_aliases`, which refuses a file that does not parse and an entry `labels.load_alias_map` cannot read), and load the divergence ledger (`oracle.load_ledger`), which refuses a file that does not parse, a document that is not a list, and the entries `oracle.compile_ledger` refuses. Each protects the oracle, not the build: rows shaped by another font, or an unaliased name, make every oracle number wrong without any error, and a ledger the oracle cannot compile would otherwise stop the pass only when the oracle loads it, after the build or the defect and Manual-pin gates have run. So a pass that re-runs the oracle over a build it did not make runs them as the building pass does. The ss06, ss07 and ss06+ss07 identity check runs inside the refilter, since only a refilter can change its result. A configuration that fails it is never stamped fresh, so its error is raised here on every run until it is fixed. The refilter is the only one of these checks that writes under rebuild/out/m1, so `ensure_fresh` deletes the Stage A record under `out_dir` (`_retract_stage_a`) just before it refilters, and a refusal over subset tables that were already fresh leaves the record as it was, like every artifact the record vouches for. A refusal here settles run_m1's green red and records its check line before it exits (`_exit_red`, timed from the guards' start; a cycle's child prints the line for the cycle instead). A ledger or alias map these checks accept can still hold a shape the oracle cannot use, which stops the oracle with an exception that `main` records red (`_recording_failure`)."""
    began = time.perf_counter()
    console.phase("baseline_subset")
    start = time.perf_counter()
    try:
        refiltered = baseline_subset.ensure_fresh(
            run_m1.REPO_ROOT, before_refilter=lambda: _retract_stage_a(out_dir)
        )
    except (baseline_subset.SubsetIdentityError, baseline_subset.BaselineProvenanceError) as error:
        _exit_red(str(error), began)
    print(
        f"[t] baseline_subset {time.perf_counter() - start:.1f}s ({'refiltered' if refiltered else 'fresh'})",
        flush=True,
    )
    console.phase("alias_completeness")
    start = time.perf_counter()
    try:
        missing_aliases = oracle.unaliased_subset_names(run_m1.OUT_DIR, ALIAS_YAML)
    except SystemExit as refusal:
        _exit_red(str(refusal), began)
    print(f"[t] alias_completeness {time.perf_counter() - start:.1f}s", flush=True)
    if missing_aliases:
        listing = "\n".join(f"  {name} ({', '.join(configs)})" for name, configs in missing_aliases.items())
        _exit_red(
            f"rebuild/m1-aliases.yaml is missing {len(missing_aliases)} old glyph names that appear in subset baseline rows — every oracle number would be quietly wrong, so author each entry (or map it to the literal `pending` to run anyway with those rows unaliased):\n{listing}",
            began,
        )
    console.phase("divergence_ledger")
    start = time.perf_counter()
    try:
        oracle.load_ledger(DIVERGENCES_YAML)
    except SystemExit as refusal:
        _exit_red(str(refusal), began)
    print(f"[t] divergence_ledger {time.perf_counter() - start:.1f}s", flush=True)


@dataclass(frozen=True)
class GatesOnlyBase:
    """The build a `--gates-only` pass re-runs the gates over: each settlement configuration's decision table paired with its join table, and the build's own pipeline_summary.json, whose defect fields the pass rewrites."""

    tables: dict[str, tuple]
    pipeline_summary: dict


def gates_only_base(out_dir: Path, inputs: str) -> GatesOnlyBase | str:
    """Return the build under `out_dir` that a `--gates-only` pass reads, or the reason the pass refuses it. The pass reads four things: the window enumerations stamped with `inputs` (`run_m1.serialized_tables`), M1.otf, a pipeline_summary.json that parses to an object, and every configuration's join table. `run_gates_only` exits with the returned reason, and the artifact cycle plans a gates-only rerun only when this returns a build (`artifact_cycle.m1_gates_only_base_present`), so the cycle never plans a rerun the pass would refuse over the build on disk, which it would otherwise plan again on every pass until `--fresh`. The pre-gate guards (`_run_pregate_guards`) run before this check and can still stop a planned pass, but they stop a full build the same way, so they are no reason to plan one instead. It leaves out manual_pins_summary.json, oracle_summary.json, divergence-audit.tsv and the Stage A record, because the pass writes them. Nor does it read the table-only branch's results (the string replay, the rule witnesses and the shipped-order walks), which the pass does not rerun: the pass records a green only over a prior green, which shows that branch passed over a build of the same build-side inputs its key names, and the stamp shows the tables on disk were stamped from those inputs. The build is deterministic, so those can be the tables that branch passed even when the full build that wrote them went red in that branch, and the two checks are necessary conditions for that, not sufficient ones (`run_gates_only` names the gaps). What they show rests on the prior green, never on the red build: a failed run never vouches for the artifacts it left. The cycle deletes pipeline_summary.json before it spawns a full build, and the build writes that file only after the compile and the read-back, so a build the cycle spawned that stopped earlier has no summary and is refused here."""
    serialized = run_m1.serialized_tables(out_dir, inputs)
    if serialized is None:
        return f"the stamped window enumerations under {out_dir} are missing, unreadable, or were built from other sources than the ones on disk — run `uv run python -m rebuild.pipeline.run_m1` (or a cycle pass) first; --gates-only re-runs the gates over a build, it does not make one"
    font_path = out_dir / "M1.otf"
    if not font_path.is_file():
        return f"no compiled font at {font_path} — run `uv run python -m rebuild.pipeline.run_m1` first"
    summary_path = out_dir / "pipeline_summary.json"
    try:
        pipeline_summary = json.loads(summary_path.read_text())
    except OSError, ValueError:
        pipeline_summary = None
    if not isinstance(pipeline_summary, dict):
        return f"no readable {summary_path} — the defect fields are rewritten into the build's own summary, and a build that left none is not a build this pass can stand on; run `uv run python -m rebuild.pipeline.run_m1` first"
    tables: dict[str, tuple] = {}
    for config, decision in serialized.items():
        joins_path = out_dir / f"joins-{config}.tsv"
        try:
            tables[config] = (decision, table_module.read_join_tsv(joins_path))
        except (OSError, ValueError) as error:
            return f"{joins_path} is missing or unreadable ({error}) — the defect gate reads the join tables beside the enumeration, so this pass cannot rerun the gates over this build; run `uv run python -m rebuild.pipeline.run_m1` first"
    return GatesOnlyBase(tables=tables, pipeline_summary=pipeline_summary)


def run_gates_only(out_dir: Path = run_m1.OUT_DIR, jobs: int = 1, fresh_cache: bool = False) -> None:
    """Re-run everything a full run does after the table build except the stages that produce artifacts: the defect gate, the Manual-pin replay and the oracle, over the tables and the M1.otf already on disk. It captures the Stage A values before it reads any input and deletes the Stage A record immediately before its first write (`_retract_stage_a`), which is the pre-gate refilter when the subset tables are stale and the defect fields otherwise, so a refusal before then leaves the record and the build as it found them. It rewrites the defect fields of `pipeline_summary.json`, the gate summaries and `divergence-audit.tsv`, writes the record back only once its gates pass over a prior green it stands on (`_settle_stage_a`; a later paragraph says when), and compiles nothing. The rerun depends on the stamp the build left on its serialized enumerations. The stamp names the sources those tables came from, not the build that wrote the M1.otf beside them. A stamp that matches the runes on disk is a necessary condition for the green this pass may record; that green also needs a prior green over the same build-side inputs (`artifact_cycle.gates_only_rerun`), because a failed run never vouches for the artifacts it left, and the build is deterministic, so the passing build need not be the one that stamped the tables. Even both together do not show that the tables or M1.otf on disk are a passing build's (the next paragraph names the gaps). A mismatch stops with an error instead of sweeping a stale binary. That refusal and the others over the build on disk are `gates_only_base`, the same check the artifact cycle makes before it plans a gates-only rerun. Each refusal, and a stop inside the oracle, records a red check line and clears a green over the same content (`_exit_red`), as a red gate does. Because the stamp names only what the build reads, every comparison-side edit passes it and is re-checked here: the divergence ledger, the alias map, the kern sidecar, the contact allow-list the defect gate reads, and the code `fingerprint.COMPARISON_CODE_MODULES` names, which is outside the stamp's code half.

    It may record run_m1's green under two conditions: a prior green record exists, and every input that changed since it is comparison-side (`artifact_cycle.gates_only_rerun`). The prior green shows that a completed build over every build-side input its key names passed, the stamp check shows that each enumeration's head carries the stamp of the current sources and semantics tokens, and this pass re-checks the gates the changed inputs feed. With all three it records a green over the new inputs, so the next cycle skips run_m1. The two checks are necessary conditions for the tables and M1.otf on disk to be the green build's, not sufficient ones: a build that stops without settling the green (an interrupt or a kill) can leave artifacts that pass both. The green's key (`artifact_cycle.run_m1_skip_lines`) leaves out the semantics tokens the stamp includes, so a pass run under other semantics than the green build's accepts tables a hand build stamped under those semantics. The stamp check reads only each enumeration's head, and `run_m1._pack_windows` writes in place, so an enumeration whose packing a killed build cut short after its head passes it. Neither the stamp nor the Stage A record covers the font's bytes, nothing else this pass or the cycle checks ties them to a passing build, and a hand build stamps its tables before `compile_font` writes the font, so a hand build interrupted between the two leaves whatever M1.otf an earlier build wrote, which can be one a red build over other build-side inputs left. In each case a green this pass records vouches for artifacts no passing build wrote. Of the runs behind the pass, only the prior green vouches for the build it stands on: a failed run never vouches for the artifacts it left, even one that went red only at a comparison gate over current tables (`artifact_cycle.gates_only_rerun` says why). Otherwise the pass still runs, still records its check line, and prints which input kept it from recording a green. A check line reports only how one invocation came out, while a green record lets a later pass skip work.

    It writes the Stage A record back (through `_settle_stage_a`, which declines when a Stage A input has moved since the pass began) only when its gates pass, the prior green has per-file lines, and no build-side input has moved since that green, whether or not this pass records a green. Otherwise a pass that reached its first write leaves no record, which would vouch for tables and a font that no green build over these build-side inputs passed (`artifact_cycle.m1_stage_a_current` says what the cycle trusts the record for).

    It opens the oracle row cache read-only (`write_cache=False`). A ledger edit serves every row and position verdict, and an alias edit re-derives only the rows that reach the families it names, so the re-check is fast; a store this pass wrote would be one no build produced. Because no store is written, nothing this pass re-derives is kept, so it skips the scheduled re-derivation and serves every row its keys allow except those a writing pass recorded at `UNCOVERED_AGE` (`oracle_cache.RowStore`); writing passes keep the scheduled re-derivation, so no verdict stands `oracle_cache.MAX_RECORD_AGE` of them. The store's pass ordinal does not advance either, so the verification sample, which is drawn against it, rotates on the clock instead. Otherwise repeated re-checks would verify the same rows every time. `--fresh-oracle-cache` here skips reading the stores instead of deleting them, since deleting a build input is also a write.
    """
    from rebuild.tools.artifact_cycle import (
        _defect_gate_failure,
        comparison_side_label,
        evaluate_run_m1_gate,
        gates_only_rerun,
        moved_input_labels,
        read_green_record,
        run_m1_skip_files,
        run_m1_skip_fingerprint,
    )
    from rebuild.tools.cycle_paths import RUN_M1_GREEN

    def run_m1_key() -> str:
        return run_m1_skip_fingerprint(run_m1.REPO_ROOT)

    started = time.perf_counter()
    stage = fingerprint.stage_a(run_m1.REPO_ROOT)
    _run_pregate_guards(out_dir)
    inputs = run_m1.tables_inputs()
    memo_inputs = run_m1.settle_memo_inputs()
    summary_path = out_dir / "pipeline_summary.json"
    base = gates_only_base(out_dir, inputs)
    if isinstance(base, str):
        _exit_red(base, started)
    pipeline_summary = base.pipeline_summary

    spec = load_default_spec()
    before = run_m1_key()
    record = read_green_record(RUN_M1_GREEN)
    current = run_m1_skip_files(run_m1.REPO_ROOT)

    console.phase("defect_gates")
    start = time.perf_counter()
    cell_glyphs = run_m1.mint_cell_glyphs(spec, base.tables)
    defect_fields = run_m1._defect_summary_fields(run_m1._run_defect_gates(spec, base.tables, cell_glyphs))
    print(f"[t] defect_gates {time.perf_counter() - start:.1f}s", flush=True)
    pipeline_summary.update(defect_fields)
    _retract_stage_a(out_dir)
    summary_path.write_text(json.dumps(pipeline_summary, indent=2) + "\n")
    defect_failure = _defect_gate_failure(pipeline_summary)
    if defect_failure is not None:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        _record_cli_check(_failed_check("run_m1", defect_failure), started)
        raise SystemExit(f"{defect_failure}; see pipeline_summary.json")

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
    try:
        oracle_summary = run_oracle(
            out_dir=out_dir,
            spec=spec,
            jobs=jobs,
            write_cache=False,
            fresh_cache=fresh_cache,
            memo_inputs=memo_inputs,
        )
    except SystemExit as stop:
        _exit_red(str(stop), started, status="FAILED", key=before)
    print(f"[t] run_oracle {time.perf_counter() - start:.1f}s", flush=True)
    print(json.dumps(oracle_summary, indent=2))
    gate = evaluate_run_m1_gate(pipeline_summary, pin_gate, oracle_summary)
    _record_cli_check(gate, started)
    if not gate.ok:
        _settle_green(RUN_M1_GREEN, before, False, run_m1_key, "run_m1")
        raise SystemExit("; ".join(gate.failures) + "; see oracle_summary.json and divergence-audit.tsv")
    labels = moved_input_labels(record, current) or []
    offenders = [label for label in labels if not comparison_side_label(label)]
    if record is not None and isinstance(record.get("files"), dict) and not offenders:
        _settle_stage_a(out_dir, stage)
    if gates_only_rerun(record, current) is not None:
        _settle_green(
            RUN_M1_GREEN,
            before,
            True,
            run_m1_key,
            "run_m1",
            files_of=lambda: run_m1_skip_files(run_m1.REPO_ROOT),
        )
        return
    if offenders:
        why = f"these build-side inputs moved since the last green M1 build, and a failed run never vouches for the artifacts it left, so only a full build can record a green over them: {', '.join(offenders)}"
    elif record is None:
        why = "there is no prior green M1 build for this pass to stand on"
    elif not isinstance(record.get("files"), dict):
        why = "the last green M1 build predates the per-file record this decision is taken over"
    else:
        why = "nothing has moved since the last green M1 build, so that green already stands"
    console.warn(f"run_m1: green, but this pass recorded no green — {why}")


def _retract_stage_a(out_dir: Path) -> None:
    """Delete the Stage A record under `out_dir`. Each run_m1 path captures the record's values (`fingerprint.stage_a`) before it reads an input, and calls this immediately before its first write under `out_dir`: the pre-gate guards' subset refilter when the subset tables are stale (`_run_pregate_guards` hands it to `baseline_subset.ensure_fresh`), and otherwise the table build (`run_m1.run`) on a build and the defect fields of pipeline_summary.json on a `--gates-only` pass. A build then writes the tables, M1.otf, the summaries and the audit; a `--gates-only` pass writes the gate summaries and the audit. The corpus build copies the record into its manifest, and the artifact cycle trusts a matching record beside the audit, the subset tables and M1.otf (`artifact_cycle.m1_stage_a_current`). So a run that writes under `out_dir` and then stops, fails a gate or raises before `_settle_stage_a` leaves no record to vouch for what it left: a corpus built over it carries null Stage A values, which `make verdict-ready` refuses, and the next cycle skips neither run_m1 nor the corpus build. A run that a check refuses, or that fails, before its first write (a pre-gate guard over fresh subset tables, `gates_only_base`, a rune that does not load) has changed nothing the record vouches for, so it leaves the record as well: deleting it would keep the next cycle from skipping run_m1 and the corpus build once the edit is fixed or reverted, though no artifact changed."""
    (Path(out_dir) / fingerprint.STAGE_A_FILENAME).unlink(missing_ok=True)


def _settle_stage_a(out_dir: Path, values: Mapping[str, str]) -> None:
    """Write the Stage A record under `out_dir` (`fingerprint.write_stage_a`) once a run's gates have passed: the values the run captured (`fingerprint.stage_a`) before it read its inputs, and only while the sources on disk still give them. A Stage A input edited while the run ran may have reached the artifacts after its values were captured, so a record of the captured values could match the sources again once the edit is reverted, beside artifacts built from the edit. The run warns and leaves no record instead, as `_settle_green` declines a green whose key moved. The check catches a Stage A edit still on disk at the settle, but not one made and reverted while the run was going: the run may have read the edit, and the record names the sources without it. `_settle_green`'s recompute has the same limit. The cycle never writes this record, so unlike a green it is written under `CYCLE_RUN_ENV` too."""
    if fingerprint.stage_a(run_m1.REPO_ROOT) != dict(values):
        console.warn(
            "run_m1: green, but its Stage A inputs changed while it ran — Stage A record not written"
        )
        return
    fingerprint.write_stage_a(out_dir, values)


def _settle_green(
    green_path: Path,
    key: str,
    ok: bool,
    recompute: Callable[[], str],
    label: str,
    files_of: Callable[[], dict[str, str]] | None = None,
) -> None:
    """Finalize a standalone green through `green_record.settle_green` and report publication or input drift. `CYCLE_RUN_ENV` gives the cycle ownership of both publication and invalidation, so a child leaves its records untouched. `files_of` supplies the per-file digest lines behind the key, so a later skip miss can name the input that changed."""
    if CYCLE_RUN_ENV in os.environ:
        return
    from rebuild.tools.green_record import settle_green

    if not settle_green(green_path, key, ok, recompute, files_of=files_of):
        if ok:
            console.warn(f"{label}: green, but its inputs changed while it ran — green not recorded")
        return
    where = (
        green_path.relative_to(run_m1.REPO_ROOT)
        if green_path.is_relative_to(run_m1.REPO_ROOT)
        else green_path
    )
    print(f"{label}: green — fingerprint recorded in {where}", flush=True)


def main(argv: list[str] | None = None) -> None:
    from rebuild.tools.artifact_cycle import conform_job_budget, conform_job_derivation, sweep_job_budget

    sweep_jobs = sweep_job_budget()
    conform_jobs = conform_job_budget(skip_gates=True, skip_corpus=True)
    parser = argparse.ArgumentParser(description="Run the M1 integration pipeline and its Phase-2 gates.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help=f"worker budget for the oracle and the conformance sweep: the oracle cuts every configuration's table into row ranges and runs this many at once, while the conformance sweep runs this many of its units at once, one process each (the ss10 overlay whole and each settlement configuration once per final symbol), and no more than there are units. Each default is a budget the artifact cycle derives from the machine rather than a checked-in width: a bare run takes the oracle's `sweep_job_budget()`, the width the cycle hands run_m1 — {sweep_jobs} on this machine, the cores under the memory clamp that budget's own docstring argues from `ORACLE_SHARD_BYTES` and `ORACLE_ABSORB_BYTES` — and --conform-only takes the conformance sweep's own `conform_job_budget()` with the build lane idle, since a hand sweep shares the machine with no corpus build and no make-test pool — on this machine {conform_job_derivation(skip_gates=True, skip_corpus=True)}. `--jobs 1` is serial. The table build's own width is --kernel-threads.",
    )
    parser.add_argument(
        "--conform-only",
        action="store_true",
        help="run only the font-vs-settle conformance sweep against the existing M1.otf and exit nonzero unless it passes",
    )
    parser.add_argument(
        "--gates-only",
        action="store_true",
        help="re-run the defect gate, the Manual-pin gate and the oracle against the M1.otf and tables already on disk, rewriting the defect fields of pipeline_summary.json, the gate summaries and divergence-audit.tsv, and writing the Stage A record, which it deletes just before its first write, only once those gates pass over a prior green build of the same build-side inputs; refuses when those tables were built from other sources than the ones on disk, and records run_m1's green when everything that moved since the last green build is comparison-side",
    )
    parser.add_argument(
        "--fresh-oracle-cache",
        action="store_true",
        help="derive every row rather than serving any from the oracle's per-row verdict stores, and write fresh ones over them; under --gates-only, which may not write a build input, it declines to read the stores and leaves them where they are",
    )
    parser.add_argument(
        "--conform-max-length",
        "--conform-horizon",
        type=int,
        default=4,
        help="exhaustive sweep length for --conform-only (the per-edit sweep over the settlement configurations; the overlay configuration keeps its own maximum length); `make conform-deep` runs the same sweep deeper on demand",
    )
    parser.add_argument(
        "--kernel-threads",
        type=int,
        default=None,
        help=(
            "how many worker slots the kernel's table build runs at once beside default's memo, each holding a delta configuration or default's fold preparation, capped at the configuration count and the cores this process may actually run on; the ceiling is memory rather than CPU, so the default is derived from this machine rather than checked in — on this one "
            f"{kernel_exec.describe_kernel_threads(configs=len(conform.SETTLEMENT_CONFIGS))}, the booking being default's retained memo, every configuration's parked fold product, what each delta in flight adds beyond its own, and default's fold preparation in a slot of its own once every delta has one — which AMS_KERNEL_THREADS short-circuits and this flag beats in turn; the string replay after the build has its own width, --replay-threads"
        ),
    )
    parser.add_argument(
        "--overlap-memo-writes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "whether the kernel's table build writes its memo files on threads of their own beside the delta wave and the folds (the same bytes, sooner) or each ahead of the work that follows it; the default is derived from this machine at the build's width: on when the booking --kernel-threads is sized by, with kernel_exec.MEMO_WRITE_OVERLAP_BYTES added for those writers, still fits the memory — "
            f"{'on' if kernel_exec.memo_writes_overlap(len(conform.SETTLEMENT_CONFIGS), configs=len(conform.SETTLEMENT_CONFIGS)) else 'off'} on this one at the whole wave's width; the artifact cycle passes the choice it derives beside gate:make-test's pool"
        ),
    )
    parser.add_argument(
        "--scratch-beside-default",
        type=int,
        default=None,
        help=(
            "at most how many of the heaviest delta configurations the kernel's table build enumerates from scratch beside default instead of over default's memo once it has enumerated (the same tables, sooner, for more memory and CPU), taken in whole tiers of equal unlocking-rune count and capped at the delta count and one less than --kernel-threads; the default is derived from this machine at the build's width and memo-write order: as many as fit the memory with each booked at kernel_exec.SCRATCH_PEAK_BYTES — "
            f"{kernel_exec.deltas_from_scratch(len(conform.SETTLEMENT_CONFIGS), configs=len(conform.SETTLEMENT_CONFIGS), overlap=kernel_exec.memo_writes_overlap(len(conform.SETTLEMENT_CONFIGS), configs=len(conform.SETTLEMENT_CONFIGS)))} on this one at the whole wave's width; the artifact cycle passes the count it derives beside gate:make-test's pool"
        ),
    )
    parser.add_argument(
        "--replay-threads",
        type=int,
        default=None,
        help=(
            "how many settlement configurations the string replay after the build walks at once in its one crate process, capped at the configuration count and the cores this process may actually run on; a replay engine's peak memory is estimated on its own rather than at the table build's width, so the default is derived from this machine — on this one "
            f"{describe_fit(kernel_exec.REPLAY_PEAK_BYTES, cap=len(conform.SETTLEMENT_CONFIGS))}, nothing co-resident since the build's process has exited — which AMS_REPLAY_THREADS short-circuits and this flag beats in turn"
        ),
    )
    args = parser.parse_args(argv)
    if args.scratch_beside_default is not None and args.scratch_beside_default < 0:
        parser.error("--scratch-beside-default must be zero or more")
    conform_default = args.conform_only and not args.gates_only
    stated = args.jobs if args.jobs is not None else conform_jobs if conform_default else sweep_jobs
    jobs = stated if stated > 1 else 1
    started = time.perf_counter()
    run_m1._build_phase_lines.clear()

    if args.gates_only:
        with _recording_failure(started):
            run_gates_only(out_dir=run_m1.OUT_DIR, jobs=jobs, fresh_cache=args.fresh_oracle_cache)
        return

    if args.conform_only:
        from rebuild.tools.artifact_cycle import (
            conform_skip_fingerprint,
            conform_skip_files,
            evaluate_conform_gate,
        )
        from rebuild.tools.cycle_paths import CONFORM_GREEN

        def conform_key() -> str:
            return conform_skip_fingerprint(run_m1.REPO_ROOT, args.conform_max_length)

        before = conform_key()
        console.phase("run_font_conformance")
        start = time.perf_counter()
        try:
            conformance = run_font_conformance(max_length=args.conform_max_length, jobs=jobs)
        except SystemExit as refusal:
            _settle_green(CONFORM_GREEN, before, False, conform_key, "gate:conform")
            _record_cli_check(_failed_check("conform", str(refusal), REFUSED_STATUS), started)
            raise
        print(
            f"[t] run_font_conformance {time.perf_counter() - start:.1f}s {rss_token(process_peak_rss_bytes())}",
            flush=True,
        )
        print(json.dumps(conformance, indent=2))
        result = evaluate_conform_gate(conformance)
        _settle_green(
            CONFORM_GREEN,
            before,
            result.ok,
            conform_key,
            "gate:conform",
            files_of=lambda: conform_skip_files(run_m1.REPO_ROOT, args.conform_max_length),
        )
        _record_cli_check(result, started)
        if not result.ok:
            raise SystemExit("font conformance failed; see conform_summary.json")
        return

    with _recording_failure(started):
        run_m1.full_run(
            jobs=jobs,
            started=started,
            kernel_threads=args.kernel_threads,
            replay_threads=args.replay_threads,
            overlap_memo_writes=args.overlap_memo_writes,
            scratch_beside_default=args.scratch_beside_default,
            fresh_oracle_cache=args.fresh_oracle_cache,
        )


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
