"""The accept's four-letter sweep (doc/review-design.md §2.3), which `make review-accept` runs before it takes the snapshot, so nothing enters the accepted snapshot unchecked at four letters.

The corpus build (`engine.review.build`) finds a letter's changes by shaping the certificates of its rules and every text that holds it with at most `diff_units.SWEEP_CONTEXT` letters of context around it, so a change that only a longer context shows can be missed. For each configuration and each letter whose rules changed there (`diff_units.changed_letters`, the letters the corpus build finds units for), one task (`Task`, `diff_units.sweep_finds`) shapes the letter's certificates and every text that holds it with at most `diff_units.ACCEPT_SWEEP_CONTEXT` letters of context, in both fonts, and the merge keeps only what no candidate of the corpus build shows (`diff_units.merge_sweep_finds`). The tasks run in a spawn pool, one task per process, `sweep_width` of them at once.

`ensure` writes what the sweep found to its record (`engine/review/sweep_record.py`), keyed by the inputs it swept, and a record whose key is the current inputs' answers for them, so no sweep runs again until an input moves. `refusal` is the accept's refusal while the record holds finds that the served corpus was not built with; the next corpus build reads the record, and the units its texts show are ordinary units of the corpus.
"""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

from engine.pipeline import fingerprint
from engine.pipeline.labels import spec_alphabet
from engine.review import build, diff_units, snapshot, sweep_record, tablediff
from engine.tools import memory_budget, peak_rss

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# The peak footprint of one task (`run_task`), the divisor of the sweep's width (`sweep_width`): a spawn process that imports the review build, opens both fonts (`diff_units.Fonts`: a HarfBuzz shaper and a glyph set for each, and the ink comparator and junction classifiers the build's `Fonts` makes beside them), and shapes one letter's certificates and sweep texts under one configuration, the texts made one at a time from `diff_units.sweep_layers` and never held as a list, keeping only the cell changes and texts it finds, so what it holds does not grow with the alphabet or the edit. The whole four-letter diff run in one process peaked at 0.50 GB, because that process held every configuration's finder state and each configuration's 5.5 million texts as one set; a task holds neither. The constant comes from the first sweep on the 18-core M5 Pro 48 GiB MacBook Pro (`doc/fleet.md`), in Low Power Mode, at 56 runes and 47 alphabet symbols, taken on 2026-11-27: the one-join edit that adds ·Ah to ·It's baseline-exit extension, 14 tasks of 422,014 sweep texts and their certificates each, read 0.070 GB peak footprint at most (the record's `cost.peak_task_bytes`), and one such task run alone over the accepted font on both sides read 0.068 GB; plus a quarter, rounded up to the next tenth of a gigabyte, 0.1. No pool books anything beside it: the accept refuses while a pass holds the pass lock, and holds the lock while the pool runs, so no gate's pool runs at the same time and a pass that starts meanwhile waits for it. No job-costs row checks it, since the accept writes no pool record; each record's `cost.peak_task_bytes` is the reading to re-measure it from.
SWEEP_TASK_BYTES = 100_000_000


class SweepError(RuntimeError):
    """A sweep that cannot run: the snapshot it compares with does not read."""


@dataclass(frozen=True)
class Task:
    """One task of the sweep: the two fonts' paths (the snapshot's, then the new build's), the configuration, the letter's rune and spelling, the spellings of every letter whose rules changed under the configuration, the letter's certificate texts from both tables, the alphabet, and the two contexts, the corpus build's and the sweep's (`diff_units.sweep_finds`)."""

    before: str
    after: str
    config: str
    rune: str
    word: str
    words: tuple[str, ...]
    certificates: tuple[str, ...]
    alphabet: tuple[str, ...]
    ordinary_context: int
    context: int

    @property
    def texts(self) -> int:
        """How many texts the task shapes: its certificates and its sweep texts (`diff_units.sweep_size`)."""
        return len(self.certificates) + diff_units.sweep_size(len(self.alphabet), self.context)


def load_spec(repo_root: Path = REPO_ROOT):
    return build.load_quiet_spec(repo_root)


def plan(
    accepted: snapshot.Snapshot,
    tables: Path,
    *,
    spec=None,
    repo_root: Path = REPO_ROOT,
    ordinary_context: int = diff_units.SWEEP_CONTEXT,
    context: int = diff_units.ACCEPT_SWEEP_CONTEXT,
) -> list[Task]:
    """The sweep's tasks for the build in `tables` against `accepted`: one for each configuration and each letter whose rules changed there and that spells out, in configuration order, then by rune, from the same diff the corpus build reads (`build.config_diffs`), and none when no letter's rules changed. The spec is loaded from `repo_root` only when some configuration's rows changed."""
    loaded = [spec]

    def spec_of():
        if loaded[0] is None:
            loaded[0] = load_spec(repo_root)
        return loaded[0]

    tasks: list[Task] = []
    font = Path(tables) / snapshot.FONT_NAME
    for diff in build.config_diffs(accepted, Path(tables), spec_of):
        changed, _provenance = diff_units.changed_letters(diff.settlement)
        words = {
            rune: "".join(map(chr, values))
            for rune in changed
            if (values := diff_units.spelling(spec_of(), rune)) is not None
        }
        if not words:
            continue
        alphabet = tuple(spec_alphabet(spec_of()))
        certificates = diff_units.rune_certificates(words, diff.old_certificates, diff.new_certificates)
        spelled = tuple(sorted(set(words.values())))
        for rune in sorted(words):
            tasks.append(
                Task(
                    str(accepted.font),
                    str(font),
                    diff.config,
                    rune,
                    words[rune],
                    spelled,
                    tuple(sorted(certificates.get(rune, ()), key=lambda text: (len(text), text))),
                    alphabet,
                    ordinary_context,
                    context,
                )
            )
        del diff
    return tasks


def sweep_width(tasks: int, *, total_bytes: int | None = None) -> tuple[int, str]:
    """How many tasks run at once, and the arithmetic as a clause: the machine's memory less the reserve, divided by `SWEEP_TASK_BYTES`, capped at the usable cores and the task count (`memory_budget.how_many_fit`). Nothing else is subtracted: the accept refuses while a pass holds the pass lock and holds it while the sweep runs, so no gate's pool runs beside it, and the accept's own process, which holds the spec and has diffed the tables by then, sits inside the reserve."""
    cap = min(memory_budget.usable_cores(), max(tasks, 1))
    width = memory_budget.how_many_fit(SWEEP_TASK_BYTES, cap=cap, total_bytes=total_bytes)
    return width, memory_budget.describe_fit(SWEEP_TASK_BYTES, cap=cap, total_bytes=total_bytes)


def run_task(task: Task) -> diff_units.SweepFinds:
    """Run one task in this process (`diff_units.sweep_finds` over both fonts), with the process's peak footprint, which is the task's own in a pool that runs one task a process."""
    fonts = diff_units.Fonts(Path(task.before), Path(task.after))
    found = diff_units.sweep_finds(
        fonts,
        task.config,
        task.rune,
        task.word,
        task.alphabet,
        words=task.words,
        certificates=task.certificates,
        ordinary_context=task.ordinary_context,
        context=task.context,
    )
    return replace(found, peak_bytes=peak_rss.peak_footprint_self_bytes())


def run_pool(tasks: Sequence[Task], width: int) -> list[diff_units.SweepFinds]:
    """Run `tasks` in a spawn pool of `width` processes, one task a process (`max_tasks_per_child=1`), so a task's peak is its own and nothing it held stays for the next; the results come back in the order the tasks finish, which the merge does not depend on."""
    pool = ProcessPoolExecutor(
        max_workers=width, mp_context=multiprocessing.get_context("spawn"), max_tasks_per_child=1
    )
    try:
        futures = [pool.submit(run_task, task) for task in tasks]
        return [future.result() for future in as_completed(futures)]
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


@dataclass(frozen=True)
class Outcome:
    """What `ensure` found: the record's path, the record for the current inputs (None when there is none and no sweep ran, or nothing can be swept), and whether this call ran the sweep and wrote it."""

    path: Path
    record: dict | None
    swept: bool = False


def summary(record: Mapping | None) -> str:
    """What a record found, in a clause."""
    found = sweep_record.changes(record)
    if not found:
        return "nothing the corpus build's candidates do not show"
    configs = sorted({entry["config"] for entry in (record or {}).get("changes") or ()})
    return (
        f"{len(found)} change{'' if len(found) == 1 else 's'} the corpus build's candidates do not show, "
        f"under {', '.join(configs)}"
    )


def ensure(
    *,
    store: Path,
    tables: Path,
    home: Path,
    record_path: Path | None = None,
    run: bool = True,
    spec=None,
    repo_root: Path = REPO_ROOT,
    ordinary_context: int = diff_units.SWEEP_CONTEXT,
    context: int = diff_units.ACCEPT_SWEEP_CONTEXT,
    execute: Callable[[Sequence[Task], int], list[diff_units.SweepFinds]] = run_pool,
    say: Callable[[str], None] = print,
) -> Outcome:
    """Find the sweep's record for the current inputs: the current snapshot under `home`, the build in `tables` and the two contexts, at `record_path`, by default `var/review/accept-sweep.json` beside the store (`sweep_record.RECORD_RELATIVE`). When none is there and `run` is set, run the sweep (`plan`, `execute` at `sweep_width`, `diff_units.merge_sweep_finds`), printing first how many tasks and texts it shapes and at the end what it found and what it cost, and write the record; with no letter whose rules changed it writes a record that found nothing and shapes nothing. Raises `SweepError` when the snapshot does not read."""
    path = Path(record_path) if record_path is not None else Path(store).parent / sweep_record.RECORD_RELATIVE
    directory = snapshot.current(Path(home))
    if directory is None:
        say(f"accept sweep: no snapshot is current under {home}, so there is nothing to sweep against")
        return Outcome(path, None)
    try:
        accepted = snapshot.load(directory)
    except snapshot.SnapshotError as error:
        raise SweepError(str(error)) from None
    try:
        font_sha = fingerprint.file_sha256(Path(tables) / snapshot.FONT_NAME)
    except OSError:
        font_sha = None
    key = sweep_record.key_for(
        accepted.digest, snapshot.tables_stamp(Path(tables)), font_sha, ordinary_context, context
    )
    if key is None:
        say(f"accept sweep: {tables} holds no font or no tables of one stamp, so there is nothing to sweep")
        return Outcome(path, None)
    found = sweep_record.applicable(path, key)
    if found is not None or not run:
        if found is not None and run:
            say(
                f"accept sweep: {path} holds the sweep of these inputs, run at {found.get('ran_at')}; it found {summary(found)}"
            )
        return Outcome(path, found)
    start = time.perf_counter()
    tasks = plan(
        accepted,
        Path(tables),
        spec=spec,
        repo_root=repo_root,
        ordinary_context=ordinary_context,
        context=context,
    )
    texts = sum(task.texts for task in tasks)
    width, fit = sweep_width(len(tasks))
    if tasks:
        say(
            f"accept sweep: {len(tasks)} task{'' if len(tasks) == 1 else 's'}, one per configuration and letter whose rules changed, "
            f"shaping {texts:,} texts with up to {context} letters of context in both fonts (the corpus build's stop at "
            f"{ordinary_context}); {width} at once: {fit}"
        )
        finds = execute(tasks, width)
    else:
        say(
            "accept sweep: no letter's rules changed since the accepted snapshot, so there is nothing to sweep"
        )
        finds = []
    merged = diff_units.merge_sweep_finds(finds)
    seconds = time.perf_counter() - start
    peak = max((found.peak_bytes for found in finds), default=0)
    record = sweep_record.write(
        path,
        key,
        ran_at=snapshot.utc_now(),
        cost={
            "tasks": len(tasks),
            "texts": sum(found.texts for found in finds),
            "width": width if tasks else 0,
            "seconds": round(seconds, 1),
            "peak_task_bytes": peak,
        },
        configs=tablediff.SETTLED_CONFIGS,
        finds={
            config: [(entry.text, entry.change) for entry in entries] for config, entries in merged.items()
        },
    )
    say(
        f"accept sweep: found {summary(record)}, in {seconds:.0f}s"
        + (f", at most {peak_rss.format_gb(peak)} GB a task" if peak else "")
        + f"; recorded in {path}"
    )
    return Outcome(path, record, swept=True)


def refusal(manifest: Mapping, outcome: Outcome | None) -> str | None:
    """The accept's refusal while the record for the current inputs holds finds that the corpus was not built with (its `accept_sweep` component is not the record's, `sweep_record.component`), or None."""
    record = outcome.record if outcome is not None else None
    if outcome is None or not sweep_record.has_finds(record):
        return None
    recorded = manifest.get("inputs_fingerprint")
    if isinstance(recorded, dict) and recorded.get("accept_sweep") == sweep_record.component(record):
        return None
    count = len(sweep_record.changes(record))
    return (
        f"the four-letter sweep found {count} change{'' if count == 1 else 's'} that the Queue does not show "
        f"({outcome.path}); `make artifact-cycle` adds {'it' if count == 1 else 'them'} to the corpus for verdicts"
    )
