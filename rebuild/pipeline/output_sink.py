"""The writer helper for whole-file outputs that leaves an output whose bytes come out the same in place, and the per-pass counts of what it wrote and kept.

`write_output` and `replace_output` write an output that changed exactly as a plain write does, each with its own discipline. `write_output` writes in place, as `Path.write_bytes` and `Path.write_text` do. `replace_output` fills a `<name>.partial` file beside the target and renames it over the target, so a reader gets the old bytes or the new ones and never a torn file; it takes the new bytes, a source file to copy, or a writer that fills the staging file. With `keep_if_equal=True` either first compares the new output with the file on disk and, when they are equal, writes nothing, so the old file keeps its inode and modification time. The compare reads the old file in chunks after a size check, against bytes the caller already holds or a source file read in chunks too, or hashes it in chunks against a digest the caller holds (`replace_output`'s `sha256`), so it never holds a second copy of the output. It compares only a regular file (`os.stat` follows a link): a missing target, a FIFO, a device or a link to the null device is written as the plain form writes it, and the compare never opens one, since opening a FIFO that has no writer blocks forever. Only the call sites below opt in, each with `keep_if_equal=True`. The segment format's parts (`row_delta.write_part`: the unit store's and the windows rows' bases and segments, and `row_delta.write_plain_part`: the settle memos' bases and segments, each written under a name no earlier part had), a materialized unit store (`unit_cache.write_materialized_store`) and the pass's fold claim (`row_delta.claim_fold`, under `cycle_paths.CYCLE_VAR`) go through `replace_output` without the compare; no other writer goes through the helper. `same_bytes` is the same compare between two files on disk, with which the verdict update tells the land that it left the prepared store as the snapshot's bytes (`landing.record_unchanged`).

Each call counts what it did, when `OUTPUT_COUNTS_ENV` names a file: one line of `<outcome>\t<bytes>\n`, appended with `O_APPEND` and never creating the file, where the outcome is `changed` (written after the compare found other bytes or no file), `written` (written with no compare: `keep_if_equal` false or a target that is not a regular file) or `kept` (left in place, the bytes it would have written). `note_rewritten` appends `rewritten\t<output>\n` beside them for an output held as a base and segments (`row_delta`) whose writer wrote a new base outside a fold, the share rule or a letter batch, which the cycle prints as `rewritten whole: <output>` beside its `written` line, a finding to explain (#618's comment 08). The artifact cycle creates the file for each pass under var/cycle/ and names it in that variable, which every process of the pass inherits, pool workers among them, and `cycle_timings.CycleTimings` sums it into the run line's `written.outputs` block and removes it. A process without the variable (a hand run, the fault kit, the memory series, the tests, which `rebuild/conftest.py` strips it from) counts nothing, and neither does one that outlives its pass, since the removed file is not created again. `cycle_timings` spells the variable's name and the outcomes out too, because it must not import rebuild/pipeline (its `SUBSET_STAMP` comment says why), and this module spells them out because importing `cycle_timings` would pull its imports into every closure that holds this module; rebuild/test_output_sink.py ties the two.

The opted-in outputs, each with every reader of its modification time or inode:

- The settlement and join tables, `rebuild/out/m1/settlement-` and `joins-<config>.tsv`. The crate makes the same compare itself (`fanout::write_text`), so these writes show in the step lines' `written_bytes` and not in the `outputs` block. Their readers read their content: the crate's string replay, the read-back and the gates. `review.build._generated_at` reads the table-diff build's `--new` directory's modification time, which a write in place over an existing file never moves, whether or not it writes.
- run_m1's and the gates' summaries under `rebuild/out/m1/`: `pipeline_summary.json` (`run_m1._run_glyph_chain`, and `run_m1_gates.run_gates_only`'s rewrite of its defect fields), `readback_summary.json`, `witness_summary.json`, `emitted_order_summary.json`, `manual_pins_summary.json`, `oracle_summary.json`, `conform_summary.json` and the deep sweep's `deep_sweep_summary.json` and `deep_sweep_targeted_summary.json` (`conform.ConformReport.write`), and `coretext_summary.json`, each written in place through `write_output`. Their readers parse their content. The cycle deletes the Manual-pin and oracle summaries before every run_m1 spawn it makes, the pipeline summary too except before a gates-only rerun (`artifact_cycle._do_run_m1`), which rewrites that file in place and may leave it, and the conform summary before a gate:conform spawn, so a full build's summaries are new files; no reader depends on that, since the cycle reads a gates-only rerun's summaries for their content alone and never asks `_gate_that_stopped_a_full_build` about them.
- The review corpus's font copies and static app files (`review.build._copy_font`, `copy_static` with its fallback `index.html`, in the m1-audit and table-diff builds and `refresh_assets`). `_copy_font` compares the target with the digest the build took when it loaded the font, so a kept copy is the font the units describe. `review.build._generated_at` reads the source fonts' modification times, not the copies'; `review.ink.shaper_for` keys its shapers on the source fonts' path, modification time and size; the review server sends every corpus file with `Cache-Control: no-store` and never answers 304 (`serve.NoCacheStaticHandler`). A corpus file gets a new inode at every land anyway, since the corpus is built in a clone of the served tree, which keeps each file's modification time.
- `review-facts.json` beside the corpus and `rebuild/review-facts-pins.json` (`facts.write_facts`, `facts.main`), whose readers parse their content: `facts.load_facts`, `facts.compute_pins` and the cycle's compare with the copy in the git index. The pins file is tracked, so `fault_kit.prepare_tree` keys its sync of the kit's tree on the file's size and modification time (`_stat_key`), and a kept file only spares the kit a fresh clone of the same bytes.
- The review corpus's unit store head, `unit-cache.ndjson.gz.head` (`row_delta.commit`), which a pass that changes no record and moves no stamp leaves as it was. Its readers parse its content through `row_delta.read_head`: `unit_cache.open_store`, the live-fragment iterator (`unit_index.LiveCorpus`) and `perf_fingerprint`; none reads its modification time or inode.
- Each settlement configuration's windows head, `rebuild/out/m1/windows-<config>.tsv.gz.head` (`row_delta.commit`, from `run_m1._pack_config`), which a build that changes no row and moves no stamp leaves as it was. Its readers parse its content through `row_delta.read_head`: the one reader of a configuration's table head (`table.read_windows_head`, and through it `run_m1.serialized_tables`, `artifact_cycle.tables_imports_digest` and `deep_sweep.targeted_lines`), the materialized form (`table.materialized_windows`, which `perf_fingerprint` reads), the build's own plan (`run_m1._plan_windows`) and the shipped-order walks (`kernel_exec.replay_emitted`); none reads its modification time or inode. Its head file, `windows-<config>.head.json.gz`, is renamed into place by `_pack_config`, outside the helper, and every reader checks it against the size and sha256 the head records, never its modification time or inode; `artifact_cycle.m1_artifacts_present` checks only that both exist.
- Each settlement configuration's settle memo head, `rebuild/out/m1/settle-memo-<config>.bin.head` (`row_delta.commit`, from `conform.SettleMemoRoute.commit`), which a full replay whose refill settles every window as the memo holds it rewrites under its new stamps, and which a commit whose document comes out the same leaves as it was. Its readers parse its content: `conform._MemoStore.load`, which opens every part it lists, `conform.settle_memo_standing`, `conform.settle_memo_source` and each writer's plan (`conform.plan_settle_memos`); none of them uses a `_generated_at` or `file_signature`. `conform._MemoStore.identity`, and `conform.settle_memo_identity` beside it, read the head file's device and inode, beside its content generation and the parts it lists, as the version of the memo a store mapped: the pooled conformance sweep's absorb prunes only when the memo on disk is the version every unit mapped (`conform.SweepPrune`). A head the compare leaves in place keeps its inode and content, so the units' version and the absorb's still agree.
- `var/cycle/journal-scan.json` (`journal.save_scan_state`), whose payload comes out the same when the journal has not grown. `journal.load_scan_state` reads its content, and the scan checks that against the journal itself (`journal._holds`), never the file's own inode or time.
- `site/DepartureMono-Regular.otf`, which the Makefile's `all` target copies only when `cmp` finds it differs. `fault_kit.prepare_tree` keys its sync of every site font on the font's size and modification time, and `fault_kit.control_key` reads the size and modification time of the kit's own copy, so a kept font spares the kit a fresh clone and keeps its control. `tools/serve.py`'s livereload watch on `site/**/*.otf` reloads the open site tabs when a font's modification time moves, so a `make all` that changes no font reloads none.

The outputs left out because a reader depends on their being rewritten: `replay_summary.json`, a plain write, since the cycle takes the replay record as a full build's own only when its modification time moved during the spawn (`artifact_cycle._replay_record_mtime`), and the contracts closures' sidecar, which `contracts_closure.start_run` deletes before every spawn so that a failed suite cannot publish stale closures. The tree's readers of a modification time or an inode not named above read none of the opted-in outputs: `verdict_store.file_signature` and the server's manifest signature (the store, the accepted list, the corpus manifest), `status`'s stat key (the verdicts files), `review_queue.read_accepted` (the accepted list), `conform._MemoStore.identity` for a memo with no head (the single file a tree from before the segment format holds, which no helper writes), `coretext_smoke`'s rebuild check (its Swift binary against its source), `standing_daemon`'s socket identity, and the journal's and the land's inode checks (the journal, the store, its stashes and the staged corpus).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from io import BufferedReader
from pathlib import Path

OUTPUT_COUNTS_ENV = "AMS_OUTPUT_COUNTS"
CHANGED = "changed"
WRITTEN = "written"
KEPT = "kept"
REWRITTEN = "rewritten"
STAGING_SUFFIX = ".partial"
_CHUNK = 1 << 18


def write_output(target: Path, data: bytes | str, *, keep_if_equal: bool = False) -> bool:
    """Write `data` to `target` in place, as `Path.write_bytes` does (a `str` encoded as UTF-8, as `Path.write_text` writes it), and return True; with `keep_if_equal`, write nothing and return False when `target` is a regular file that already holds those bytes."""
    target = Path(target)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    outcome = (
        _compare(target, len(payload), lambda handle: _holds_bytes(handle, payload))
        if keep_if_equal
        else WRITTEN
    )
    if outcome == KEPT:
        _count(KEPT, len(payload))
        return False
    target.write_bytes(payload)
    _count(outcome, len(payload))
    return True


def replace_output(
    target: Path,
    data: bytes | str | None = None,
    *,
    source: Path | None = None,
    writer: Callable[[Path], object] | None = None,
    sha256: str | None = None,
    keep_if_equal: bool = False,
) -> bool:
    """Put a new `target` in place through a staging file beside it (`target` with `.partial` added) renamed over it, and return True. The staging file is filled from exactly one of `data` (a `str` encoded as UTF-8), `source`, a file copied as `shutil.copyfile` copies it, or `writer`, called with the staging path. An exception while it is filled leaves `target` as it was and removes the staging file. With `keep_if_equal`, write nothing and return False when `target` is a regular file that already holds the new bytes: compared with `data` or `source` in chunks, and for a `writer`, whose bytes are not known before it runs, hashed in chunks against `sha256`, which the caller must then give."""
    target = Path(target)
    if sum(given is not None for given in (data, source, writer)) != 1:
        raise TypeError("replace_output takes exactly one of data, source and writer")
    if keep_if_equal and writer is not None and sha256 is None:
        raise TypeError("replace_output keeps a writer's output only against its sha256")
    payload = data.encode("utf-8") if isinstance(data, str) else data
    outcome, kept = WRITTEN, 0
    if keep_if_equal:
        if payload is not None:
            kept = len(payload)
            outcome = _compare(target, kept, lambda handle: _holds_bytes(handle, payload))
        elif source is not None:
            kept = os.stat(source).st_size
            outcome = _compare(target, kept, lambda handle: _holds_file(handle, Path(source)))
        else:
            outcome = _compare(target, None, lambda handle: _digest_is(handle, sha256))
            kept = os.stat(target).st_size if outcome == KEPT else 0
    if outcome == KEPT:
        _count(KEPT, kept)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + STAGING_SUFFIX)
    try:
        if writer is not None:
            writer(staging)
        elif source is not None:
            shutil.copyfile(source, staging)
        else:
            assert payload is not None
            staging.write_bytes(payload)
        written = staging.stat().st_size
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    _count(outcome, written)
    return True


def same_bytes(path: Path, other: Path) -> bool:
    """Return whether two regular files hold the same bytes, comparing their sizes and then reading both in chunks. A path that is missing or not a regular file holds bytes the other does not, and neither is opened."""
    try:
        ours, theirs = os.stat(path), os.stat(other)
    except FileNotFoundError:
        return False
    if not (stat.S_ISREG(ours.st_mode) and stat.S_ISREG(theirs.st_mode)) or ours.st_size != theirs.st_size:
        return False
    with open(path, "rb") as handle:
        return _holds_file(handle, Path(other))


def _compare(target: Path, size: int | None, same: Callable[[BufferedReader], bool]) -> str:
    """Return `KEPT` when `target` is a regular file of `size` bytes (any size when None) for which `same` holds, `CHANGED` when it is missing or another regular file, and `WRITTEN` when it is not a regular file, which it never opens."""
    try:
        found = os.stat(target)
    except FileNotFoundError:
        return CHANGED
    if not stat.S_ISREG(found.st_mode):
        return WRITTEN
    if size is not None and found.st_size != size:
        return CHANGED
    try:
        with open(target, "rb") as handle:
            return KEPT if same(handle) else CHANGED
    except FileNotFoundError:
        return CHANGED


def _holds_bytes(handle: BufferedReader, payload: bytes) -> bool:
    expected = memoryview(payload)
    buffer = bytearray(_CHUNK)
    chunk = memoryview(buffer)
    offset = 0
    while count := handle.readinto(buffer):
        if offset + count > len(expected) or chunk[:count] != expected[offset : offset + count]:
            return False
        offset += count
    return offset == len(expected)


def _holds_file(handle: BufferedReader, source: Path) -> bool:
    ours, theirs = bytearray(_CHUNK), bytearray(_CHUNK)
    with open(source, "rb") as other:
        while True:
            count = handle.readinto(ours)
            if other.readinto(theirs) != count:
                return False
            if not count:
                return True
            if memoryview(ours)[:count] != memoryview(theirs)[:count]:
                return False


def _digest_is(handle: BufferedReader, sha256: str | None) -> bool:
    return hashlib.file_digest(handle, "sha256").hexdigest() == sha256


def note_rewritten(output: str) -> None:
    """Count that `output`, held as a base and segments, was rewritten whole outside a fold, the share rule or a letter batch, on the counts file `OUTPUT_COUNTS_ENV` names, if any."""
    _append(f"{REWRITTEN}\t{output}\n")


def _count(outcome: str, size: int) -> None:
    """Append one counts line to the file `OUTPUT_COUNTS_ENV` names, if any. It never creates the file, so a process that outlives its pass counts nothing, and a counts file that cannot be written loses its line rather than failing the write it counts."""
    _append(f"{outcome}\t{size}\n")


def _append(line: str) -> None:
    path = os.environ.get(OUTPUT_COUNTS_ENV)
    if not path:
        return
    try:
        handle = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(handle, line.encode())
        finally:
            os.close(handle)
    except OSError:
        return
