"""Accept the table-diff corpus's changes, and remake the accepted snapshot on a machine without it (doc/review-design.md §2.3).

The accept, `uv run python -m engine.review.accept [--apply] [--retake]` (`make review-accept`), makes the build the corpus shows the accepted one. Without `--apply` it is a dry run: it prints every refusal `--apply` would make and what `--apply` would write, writes nothing but the four-letter sweep's record (below), and exits 1 when `--apply` would refuse. Its refusals, each with nothing written but that record, in this order:

- the corpus under engine/out/review has no manifest that reads;
- `make verdict-ready`'s blocking checks fail (`store_guards.readiness_refusal`), among them the snapshot row and the freshness of the corpus against the sources;
- the corpus does not show engine/out/build: its after font is not Senior-Regular.otf byte for byte, or its `tables` component is not the stamp of that build's windows heads (`snapshot.tables_stamp`);
- a pass holds the artifact cycle's pass lock (taken without waiting and held to the end, `store_guards.pass_lock`);
- the four-letter sweep's record for the current inputs holds changes the corpus was not built with (`accept_sweep.refusal`), which the next `make artifact-cycle` adds to the corpus;
- a human unit of the corpus is blank, skipped, rejected or neither.

Once the refusals before it are clear, the accept, dry run and `--apply` alike, holds the pass lock and runs the four-letter sweep (`engine/review/accept_sweep.py`) when no record answers for the current inputs: every text of at most four letters that holds a letter whose rules changed, split across processes, which writes what it finds that no candidate of the corpus build shows to var/review/accept-sweep.json (`engine/review/sweep_record.py`). A record that already answers is read instead, so a second accept over the same inputs sweeps nothing. While a refusal before it stands, the dry run only reads the record. `--retake` skips the sweep with the verdict checks.

`--apply`, holding the pass lock and then the store's lock (`landing.locked`, the path every writer of the store but the review server's saves takes, so it runs while the review server listens, which answers a save made meanwhile with a retryable 503), checks the verdicts again, then:

1. takes a snapshot of engine/out/build (`snapshot.take`, into a staging directory under var/keep/review-snapshot renamed into place, keeping the snapshot it replaces as the previous one);
2. writes the tracked pointer, engine/review-snapshot.json, naming it (`snapshot.write_pointer`), which goes into the commit with the edit it accepts, so the commit that last changed the pointer is the one the snapshot stands for (`snapshot.pointer_commit`);
3. keeps the store as found, gzipped, at var/keep/review-accept/store.json.gz, replacing the one the last accept kept, and writes an empty store stamped for the served corpus (`verdict_store.write_store`);
4. tells the open review tabs, when this checkout's review server listens (`store_guards.serves`), to reload onto the empty store (`ams:store/<snapshot digest>` through livereload's /forcereload, `review_server.force_reload`), so none goes on showing the verdicts the accept consumed.

`--retake` skips the verdict refusals and the readiness and corpus refusals, for a snapshot-format bump, which leaves no snapshot this checkout reads, and for a snapshot no build of its commit remakes, both of which leave the snapshot row failing and the corpus stale. It checks instead that engine/out/build's tables carry the stamp the sources on disk give (`run_build.tables_inputs`), so the snapshot is of this tree's build, and it prints the span it accepts unreviewed: the commit the old snapshot stands for, or that none is known, and the corpus's counts when it can read them.

The remake, `uv run python -m engine.review.accept remake [--rev REV] [--home DIR]` (`make review-snapshot-remake`), installs the snapshot the pointer names on a clone or machine without it; a cycle pass on such a machine starts with it (`artifact_cycle.install_accepted_snapshot`). It reads the pointer at `--rev`, by default the last commit that changed it (`snapshot.last_pointer_commit`); a snapshot under the home that already holds the pointer's digest is made current with no build. Otherwise, holding the pass lock, it exports that revision's tree with `git archive` into var/cycle/snapshot-remake/tree (no worktree, and no `.git`: the build names no commit it cannot read, `run_build.head_commit` answering None there), runs that tree's own build there (`build`, run_build's table build and glyph chain, then that tree's `engine.review.snapshot take` into a scratch home beside it), and installs the result (`snapshot.install`) once its digest is the pointer's. The children run with `UV_CACHE_DIR` at this checkout's uv cache, and with `CARGO_TARGET_DIR`, and the tree's `engine/kernel-rs/target` linked to it, at `REMAKE_CARGO_TARGET` inside this checkout's crate target, so the crate's dependencies compile once across remakes and the tree's crate never replaces this checkout's own binary, at `target/release`. A digest that is not the pointer's is reported with `make review-accept ARGS='--apply --retake'` as the remedy, and the work directory is left for inspection; a pass deletes it.

`uv run python -m engine.review.accept build --out DIR` is the remake's build, which it runs inside the exported tree: the tables of the tree's sources stamped as a full run stamps them, and the glyph chain over them, which writes Senior-Regular.otf and kern.tsv.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline import fingerprint  # noqa: E402
from engine.review import landing, snapshot  # noqa: E402
from engine.review.store_lock import LockBusy, holder  # noqa: E402
from engine.review.verdict_store import (  # noqa: E402
    AUTOSAVE_NAME,
    MalformedStore,
    latest_by_unit,
    read_store_document,
    write_store,
)
from engine.tools import store_guards  # noqa: E402

if TYPE_CHECKING:
    from engine.review.accept_sweep import Outcome

KEEP = Path("var") / "keep" / "review-accept"
KEPT_STORE = "store.json.gz"
REMAKE_WORK = ROOT / "var" / "cycle" / "snapshot-remake"
REMAKE_CARGO_TARGET = ROOT / "engine" / "kernel-rs" / "target" / "snapshot-remake"
RETAKE_REMEDY = "make review-accept ARGS='--apply --retake'"
# The verdicts that stand between a human unit and the accept: a blank (no record) counts too, and so does a skip.
OPEN_VERDICTS = ("skip", "reject", "neither")


class Refused(RuntimeError):
    """A refusal of the accept or the remake; the message names the cause and the remedy, and nothing was written."""


def read_corpus(corpus: Path) -> dict:
    """The corpus's manifest, refused when it does not read as a JSON object."""
    try:
        manifest = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(f"the corpus {corpus} has no manifest that reads ({error})") from None
    if not isinstance(manifest, dict):
        raise Refused(f"the corpus {corpus} has no manifest that reads (it is not a JSON object)")
    return manifest


def match_refusal(manifest: Mapping, tables: Path) -> str | None:
    """Why the corpus does not show the build in `tables`, or None when it does: its after font is that build's Senior-Regular.otf byte for byte, and its `tables` component is the stamp that build's windows heads record (`snapshot.tables_stamp`)."""
    fonts = manifest.get("fonts")
    after = fonts.get("after") if isinstance(fonts, dict) else None
    recorded = after.get("sha256") if isinstance(after, dict) else None
    try:
        built = fingerprint.file_sha256(Path(tables) / snapshot.FONT_NAME)
    except OSError:
        built = None
    if built is None or recorded != built:
        return (
            f"the corpus's after font is not {Path(tables) / snapshot.FONT_NAME}; run `make artifact-cycle`"
        )
    fp = manifest.get("inputs_fingerprint")
    stamp = snapshot.tables_stamp(tables)
    if stamp is None or not isinstance(fp, dict) or fp.get("tables") != stamp:
        return f"the corpus was built over other tables than {tables} holds; run `make artifact-cycle`"
    return None


@dataclass(frozen=True)
class Standing:
    """The store as the accept reads it against the corpus: its bytes (None with no store file), the corpus's human unit ids, and each human unit's verdict, None for a blank."""

    raw: bytes | None
    human: frozenset[str]
    verdicts: Mapping[str, str | None]

    def units(self, verdict: str | None) -> list[str]:
        return sorted(unit for unit, found in self.verdicts.items() if found == verdict)

    def refusal(self) -> str | None:
        """Why the corpus's changes cannot be accepted as the store stands, or None: every human unit holds a verdict, and none is a skip, a reject or a neither."""
        parts = [
            f"{len(units):,} {label}"
            for label, units in (
                ("blank", self.units(None)),
                *((verdict, self.units(verdict)) for verdict in OPEN_VERDICTS),
            )
            if units
        ]
        if not parts:
            return None
        return (
            f"of the corpus's {len(self.human):,} human units, {', '.join(parts)}: verdict every unit, "
            "approving, finding either fine or calling identical, before the accept, or fix what was rejected and "
            "run `make artifact-cycle` first"
        )


def read_standing(store: Path, human: frozenset[str], raw: bytes | None = None) -> Standing:
    """Read the store, or its bytes `raw`, as `Standing`, refusing one that does not read or holds a record the carry refuses (`verdict_store.read_store_document`)."""
    if raw is None:
        try:
            raw = Path(store).read_bytes()
        except FileNotFoundError:
            raw = None
    document = None
    if raw is not None:
        try:
            document = read_store_document(raw, f"the store {store}")
        except MalformedStore as malformed:
            raise Refused(str(malformed)) from None
    records = latest_by_unit(document["verdicts"]) if document is not None else {}
    verdicts = {unit: (records.get(unit) or {}).get("verdict") for unit in human}
    return Standing(raw, human, verdicts)


def human_units(manifest: Mapping) -> frozenset[str]:
    ids = manifest.get("human_unit_ids")
    return frozenset(unit for unit in ids if isinstance(unit, str)) if isinstance(ids, list) else frozenset()


def span_line(manifest: Mapping | None, standing: Standing | None, root: Path) -> str:
    """What a retake accepts unreviewed: every change since the commit the old snapshot stands for, and the corpus's counts when it reads."""
    since = "an unknown commit (no snapshot this checkout reads is current)"
    if manifest is not None:
        from engine.review import status

        since = status.baseline_since(manifest, root)
    line = f"--retake accepts unreviewed every change since {since}"
    if manifest is not None and standing is not None:
        units = (manifest.get("totals") or {}).get("units")
        open_units = sum(1 for verdict in standing.verdicts.values() if verdict in (None, *OPEN_VERDICTS))
        line += f": the corpus lists {units} changes, {len(standing.human):,} of them human units, {open_units:,} not accepted"
    return line


def sweep_line(swept: Outcome | None, retake: bool) -> str:
    """The plan's line for the four-letter sweep: what its record for these inputs found, that it runs once the refusals clear, or that `--retake` skips it. `swept` is the sweep's `accept_sweep.Outcome`, None when it did not run and no record answers for the inputs."""
    if retake:
        return "skip the four-letter sweep, as --retake skips the verdict checks"
    if swept is None or swept.record is None:
        return "run the four-letter sweep once the refusals below clear, and refuse while it finds a change the corpus does not show"
    from engine.review import accept_sweep

    return f"take the four-letter sweep's record {swept.path.name}, which found {accept_sweep.summary(swept.record)}"


def plan_lines(
    store: Path,
    tables: Path,
    home: Path,
    pointer: Path,
    standing: Standing | None,
    stamp: str | None,
    sweep: str | None = None,
) -> list[str]:
    """What `--apply` checks and writes, in its order: `sweep` is the four-letter sweep's line (`sweep_line`)."""
    previous = snapshot.current(home)
    records = sum(1 for verdict in (standing.verdicts.values() if standing else ()) if verdict is not None)
    return [
        *([sweep] if sweep else []),
        f"take a snapshot of {tables} (tables {snapshot.tables_stamp(tables)}) into {home}, make it current"
        + (f", and keep {previous.name} as the previous one" if previous is not None else ""),
        f"write {pointer} naming it",
        (
            f"keep {store.name} as found at {store.parent / KEEP / KEPT_STORE}"
            if standing is not None and standing.raw is not None
            else f"keep no store ({store.name} does not exist)"
        ),
        f"write an empty {store.name} stamped for the corpus {stamp}, dropping its {records:,} verdicts on the corpus's human units",
        "tell the open review tabs to reload onto the empty store, when this checkout's review server listens",
    ]


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _clear_store(store: Path, stamp: str, raw: bytes | None) -> None:
    """Keep the store as found and write the empty store stamped `stamp`. The caller holds the store's lock."""
    if raw is not None:
        _write_atomic(store.parent / KEEP / KEPT_STORE, gzip.compress(raw, compresslevel=6, mtime=0))
    write_store(store, stamp, {}, {})


def tell_open_tabs(store: Path, digest: str) -> bool:
    """Tell the tabs open on this checkout's review server, when it listens and may write `store` (`store_guards.serves`), to reload onto the store the accept emptied (`ams:store/<digest>`, `review_server.force_reload`), and return whether the server took it."""
    from engine.tools.review_server import force_reload

    return store_guards.serves(store) and force_reload(f"ams:store/{digest}")


def tables_current(tables: Path) -> bool:
    """Whether the tables in `tables` carry the stamp the sources on disk give (`run_build.tables_inputs`), which a retake requires of the build it snapshots."""
    from engine.pipeline import run_build

    return snapshot.tables_stamp(tables) == run_build.tables_inputs()


def default_sweep(*, store: Path, tables: Path, home: Path, run: bool = True) -> Outcome:
    """The four-letter sweep over the current snapshot under `home` and the build in `tables`, with its record beside `store` (`accept_sweep.ensure`); with `run` unset it only reads the record that answers for these inputs."""
    from engine.review import accept_sweep

    return accept_sweep.ensure(store=store, tables=tables, home=home, run=run)


def run(
    *,
    store: Path,
    corpus: Path,
    tables: Path,
    home: Path,
    pointer: Path,
    apply: bool = False,
    retake: bool = False,
    readiness: Callable[[], tuple[dict, bool]] | None = None,
    stamped: Callable[[Path], bool] = tables_current,
    take: Callable[..., snapshot.Snapshot] = snapshot.take,
    tell: Callable[[Path, str], bool] = tell_open_tabs,
    sweep: Callable[..., Outcome] | None = None,
) -> int:
    """Run the accept of the corpus at `corpus` into `home` and `pointer` from the build in `tables`, clearing `store`: the dry run, or with `apply` the accept. `readiness` is `make verdict-ready`'s answer without the server row, `stamped` whether the build's tables carry the sources' stamp (the retake's check), `take` takes the snapshot, `tell` tells the open review tabs to reload onto the emptied store (`tell_open_tabs`), and `sweep(run=…)` is the four-letter sweep (`default_sweep`), which returns an `accept_sweep.Outcome` and with `run=False` only reads the record for the current inputs."""
    if sweep is None:
        sweep = functools.partial(default_sweep, store=store, tables=tables, home=home)
    if readiness is None:
        from engine.tools import verdict_ready

        readiness = functools.partial(
            verdict_ready.readiness,
            with_server=False,
            repo_root=store.parent,
            review_dir=corpus,
            build_out=tables,
            autosave_path=store,
        )
    refusals: list[str] = []
    manifest: dict | None = None
    try:
        manifest = read_corpus(corpus)
    except Refused as refused:
        print(f"ERROR: {refused}. Nothing was written.")
        return 1
    human = human_units(manifest)
    stamp = manifest.get("generated_at")
    if retake:
        if not stamped(tables):
            refusals.append(
                f"{tables}'s tables do not carry the stamp the sources on disk give, so a snapshot of them is not of this tree's build; run `uv run python -m engine.pipeline.run_build` or `make artifact-cycle`"
            )
    else:
        refusal = store_guards.readiness_refusal(
            *readiness(), "the corpus may not show the build the accept would take"
        )
        if refusal is not None:
            refusals.append(refusal)
        else:
            mismatch = match_refusal(manifest, tables)
            if mismatch is not None:
                refusals.append(mismatch)
    swept: Outcome | None = None
    if not apply:
        if holder(store_guards.pass_lock_path_for(store)) is not None:
            refusals.append(store_guards.pass_refusal(store))
        if not retake:
            swept, refusal = _dry_sweep(sweep, store, manifest, blocked=bool(refusals))
            if refusal is not None:
                refusals.append(refusal)
        standing: Standing | None = None
        try:
            standing = read_standing(store, human)
            if not retake and (verdicts := standing.refusal()) is not None:
                refusals.append(verdicts)
        except Refused as refused:
            refusals.append(str(refused))
        if retake:
            print(span_line(manifest, standing, store.parent))
        print("--apply would:")
        for line in plan_lines(store, tables, home, pointer, standing, stamp, sweep_line(swept, retake)):
            print(f"  {line}")
        for refusal in refusals:
            print(f"ERROR: --apply would refuse: {refusal}.")
        wrote = swept is not None and swept.swept
        print(
            "dry run: nothing written"
            + (
                f" but the four-letter sweep's record, {swept.path}, which the corpus build reads"
                if swept is not None and wrote
                else ""
            )
            + "; `make review-accept ARGS=--apply` writes the accept"
        )
        return 1 if refusals else 0
    try:
        if refusals:
            raise Refused(refusals[0])
        if not isinstance(stamp, str):
            raise Refused(f"the corpus {corpus} names no generated_at")
        with store_guards.pass_lock(store):
            if not retake:
                from engine.review import accept_sweep

                try:
                    swept = sweep()
                except accept_sweep.SweepError as error:
                    raise Refused(str(error)) from None
                refusal = accept_sweep.refusal(manifest, swept)
                if refusal is not None:
                    raise Refused(refusal)
            with landing.locked(store):
                standing = read_standing(store, human)
                if retake:
                    print(span_line(manifest, standing, store.parent))
                elif (verdicts := standing.refusal()) is not None:
                    raise Refused(verdicts)
                for line in plan_lines(
                    store, tables, home, pointer, standing, stamp, sweep_line(swept, retake)
                ):
                    print(line)
                try:
                    taken = take(tables, home)
                except snapshot.SnapshotError as error:
                    raise Refused(str(error)) from None
                snapshot.write_pointer(taken, pointer)
                _clear_store(store, stamp, standing.raw)
        told = tell(store, taken.digest[:12])
        print(
            f"accepted: the snapshot {taken.digest[:12]} is current and {pointer.name} names it; {store.name} is "
            "empty" + ("; the open review tabs reload onto it" if told else "")
        )
        print(
            f"next: commit {pointer.name} with the edit it accepts, and run `make artifact-cycle`, which builds the "
            "corpus against the new snapshot"
        )
        return 0
    except LockBusy:
        message = store_guards.pass_refusal(store)
    except Refused as refused:
        message = str(refused)
    print(
        f"ERROR: {message}. Nothing was written"
        + (f" but the four-letter sweep's record, {swept.path}" if swept is not None and swept.swept else "")
        + "."
    )
    return 1


def _dry_sweep(
    sweep: Callable[..., Outcome], store: Path, manifest: Mapping, *, blocked: bool
) -> tuple[Outcome | None, str | None]:
    """The dry run's four-letter sweep: with the refusals so far `blocked`, only the record that answers for the current inputs, else the sweep itself, holding the pass lock, so no pass starts while its pool runs; then the refusal the record makes, if any (`accept_sweep.refusal`). Returns the sweep's outcome, None when no record answers, and the refusal."""
    from engine.review import accept_sweep

    swept: Outcome | None = None
    try:
        if blocked:
            swept = sweep(run=False)
        else:
            with store_guards.pass_lock(store):
                swept = sweep()
    except LockBusy:
        return None, store_guards.pass_refusal(store)
    except accept_sweep.SweepError as error:
        return None, str(error)
    if swept is not None and swept.record is None:
        swept = None
    return swept, accept_sweep.refusal(manifest, swept)


# --- the remake ------------------------------------------------------------------------


def export_tree(revision: str, target: Path, repo_root: Path = ROOT) -> None:
    """Write the tree `revision` names into `target` with `git archive`, which leaves out `.git` and every ignored file."""
    target.mkdir(parents=True)
    archive = subprocess.Popen(
        ["git", "archive", "--format=tar", revision], cwd=repo_root, stdout=subprocess.PIPE
    )
    try:
        subprocess.run(["tar", "-x", "-f", "-", "-C", str(target)], stdin=archive.stdout, check=True)
    finally:
        assert archive.stdout is not None
        archive.stdout.close()
        status = archive.wait()
    if status != 0:
        raise Refused(f"`git archive {revision}` exited {status}")


def remake_environment(repo_root: Path = ROOT) -> dict[str, str]:
    """The environment the remake's children run with: this one without the virtual environment `uv run` names for this checkout, so the tree's `uv run` makes its own, with `UV_CACHE_DIR` at this checkout's uv cache unless it names one already, and `CARGO_TARGET_DIR` at `REMAKE_CARGO_TARGET`."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
    }
    environment.setdefault("UV_CACHE_DIR", str(Path(repo_root) / ".uv-cache"))
    environment["CARGO_TARGET_DIR"] = str(Path(repo_root) / REMAKE_CARGO_TARGET.relative_to(ROOT))
    return environment


def build_in_tree(tree: Path, out: Path, scratch_home: Path, environment: Mapping[str, str]) -> None:
    """Run the exported tree's own build and snapshot: its `engine.review.accept build` into `out`, then its `engine.review.snapshot take` of `out` into `scratch_home`, each with `uv run` in the tree, its output on this terminal."""
    target = tree / "engine" / "kernel-rs" / "target"
    cargo_target = Path(environment["CARGO_TARGET_DIR"])
    cargo_target.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.symlink_to(cargo_target, target_is_directory=True)
    for argv in (
        ["uv", "run", "python", "-m", "engine.review.accept", "build", "--out", str(out)],
        [
            "uv",
            "run",
            "python",
            "-m",
            "engine.review.snapshot",
            "take",
            "--tables",
            str(out),
            "--home",
            str(scratch_home),
        ],
    ):
        print(f"remake: {' '.join(argv[3:])}", flush=True)
        finished = subprocess.run(argv, cwd=tree, env=dict(environment), stdin=subprocess.DEVNULL)
        if finished.returncode != 0:
            raise Refused(f"`{' '.join(argv)}` in {tree} exited {finished.returncode}")


def remake(
    *,
    revision: str | None = None,
    home: Path = snapshot.DEFAULT_HOME,
    work: Path = REMAKE_WORK,
    store: Path = ROOT / AUTOSAVE_NAME,
    repo_root: Path = ROOT,
    export: Callable[[str, Path], None] | None = None,
    build: Callable[[Path, Path, Path], None] | None = None,
    pass_held: bool = False,
) -> int:
    """Install the snapshot the pointer at `revision` names (the module docstring). `pass_held` says the caller holds the pass lock, as the cycle pass that starts with a remake does (`artifact_cycle.install_accepted_snapshot`), so the remake does not take it. `export(revision, tree)` writes the tree and `build(tree, out, scratch_home)` builds and snapshots it; both default to the real ones. The pass lock is the one beside `store`."""
    started = time.perf_counter()
    try:
        revision = revision or snapshot.last_pointer_commit(repo_root)
        if revision is None:
            raise Refused(
                f"no commit has changed {snapshot.POINTER_RELATIVE}, so no build remakes a snapshot; take one with {RETAKE_REMEDY}"
            )
        try:
            pointer = snapshot.pointer_at(revision, repo_root)
        except snapshot.SnapshotError as error:
            raise Refused(f"{error}; take a snapshot with {RETAKE_REMEDY}") from None
        digest = str(pointer["digest"])
        if pointer.get("snapshot_format") != snapshot.SNAPSHOT_FORMAT:
            raise Refused(
                f"{revision}'s pointer names an {pointer.get('snapshot_format')!r} snapshot, and this checkout reads only "
                f"{snapshot.SNAPSHOT_FORMAT}; take one with {RETAKE_REMEDY}"
            )
        found = snapshot.find(home, digest)
        if found is not None:
            snapshot.make_current(found, home)
            print(
                f"remake: {home} already holds {digest[:12]}, the snapshot {revision[:12]}'s pointer names; it is current"
            )
            return 0
        with contextlib.nullcontext() if pass_held else store_guards.pass_lock(store):
            shutil.rmtree(work, ignore_errors=True)
            tree, out, scratch_home = work / "tree", work / "out", work / "home"
            print(f"remake: exporting {revision[:12]} into {tree}", flush=True)
            (export or functools.partial(export_tree, repo_root=repo_root))(revision, tree)
            environment = remake_environment(repo_root)
            (build or functools.partial(build_in_tree, environment=environment))(tree, out, scratch_home)
            directory = snapshot.current(scratch_home)
            if directory is None:
                raise Refused(f"the build of {revision[:12]} took no snapshot into {scratch_home}")
            try:
                remade = snapshot.load(directory)
            except snapshot.SnapshotError as error:
                raise Refused(
                    f"the snapshot the build of {revision[:12]} took does not read here: {error}"
                ) from None
            if remade.digest != digest:
                raise Refused(
                    f"the build of {revision[:12]} gives the snapshot {remade.digest[:12]}, not {digest[:12]}, the one its "
                    f"pointer names, so that commit does not remake it ({work} is left for inspection); take one with "
                    f"{RETAKE_REMEDY}"
                )
            installed = snapshot.install(remade.directory, home)
            shutil.rmtree(work, ignore_errors=True)
        print(
            f"remake: installed {installed.digest[:12]}, the snapshot {revision[:12]}'s pointer names, as current under "
            f"{home} in {time.perf_counter() - started:.0f}s"
        )
        return 0
    except LockBusy:
        message = store_guards.pass_refusal(store)
    except Refused as refused:
        message = str(refused)
    print(f"ERROR: {message}.")
    return 1


def build_sources(out: Path) -> None:
    """Build what a snapshot is taken from into `out` from this tree's sources: run_build's table build (`run_build.build_tables`), stamped with `run_build.tables_inputs` as a full run stamps them, and its glyph chain over the tables (`run_build._run_glyph_chain`), which writes Senior-Regular.otf and kern.tsv beside them."""
    from engine.pipeline import run_build
    from engine.pipeline.spec_load import load_default_spec

    out.mkdir(parents=True, exist_ok=True)
    inputs = run_build.tables_inputs()
    spec = load_default_spec()
    tables, _digests = run_build.build_tables(spec, out, inputs=inputs)
    run_build._run_glyph_chain(spec, tables, out, None)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "build":
        parser = argparse.ArgumentParser(prog="engine.review.accept build", description=build_sources.__doc__)
        parser.add_argument("--out", type=Path, required=True, help="the directory to build into")
        args = parser.parse_args(argv[1:])
        build_sources(args.out.absolute())
        return 0
    if argv and argv[0] == "remake":
        parser = argparse.ArgumentParser(
            prog="engine.review.accept remake",
            description="Install the snapshot engine/review-snapshot.json names, from a build of the commit that last changed it.",
        )
        parser.add_argument(
            "--rev",
            help="the commit or tree whose pointer and sources to remake from (default: the last commit that changed the pointer)",
        )
        parser.add_argument(
            "--home",
            type=Path,
            default=snapshot.DEFAULT_HOME,
            help="where the snapshots live (default: %(default)s)",
        )
        args = parser.parse_args(argv[1:])
        return remake(revision=args.rev, home=args.home.absolute())
    parser = argparse.ArgumentParser(
        prog="engine.review.accept",
        description="Accept the table-diff corpus's changes: take a snapshot of engine/out/build, point engine/review-snapshot.json at it and empty the verdict store; without --apply, a dry run.",
    )
    parser.add_argument("--apply", action="store_true", help="write the accept")
    parser.add_argument(
        "--retake",
        action="store_true",
        help="skip the verdict, readiness and corpus refusals, for a snapshot-format bump or a snapshot no build remakes, and say what span is accepted unreviewed",
    )
    parser.add_argument(
        "--autosave",
        type=Path,
        default=ROOT / AUTOSAVE_NAME,
        help="the store to empty (default: %(default)s)",
    )
    parser.add_argument(
        "--corpus", type=Path, help="the served corpus (default: engine/out/review beside --autosave)"
    )
    parser.add_argument(
        "--tables",
        type=Path,
        default=snapshot.BUILD_DIR,
        help="the build to accept (default: %(default)s)",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=snapshot.DEFAULT_HOME,
        help="where the snapshots live (default: %(default)s)",
    )
    parser.add_argument(
        "--pointer",
        type=Path,
        default=snapshot.POINTER_PATH,
        help="the pointer to write (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    store = args.autosave.absolute()
    return run(
        store=store,
        corpus=(args.corpus or store.parent / "engine" / "out" / "review").absolute(),
        tables=args.tables.absolute(),
        home=args.home.absolute(),
        pointer=args.pointer.absolute(),
        apply=args.apply,
        retake=args.retake,
    )


if __name__ == "__main__":
    sys.exit(main())
