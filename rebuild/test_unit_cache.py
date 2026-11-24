"""Tests for the persisted per-unit corpus cache, whose contract the docstring of rebuild/review/unit_cache.py states. The tests check that an incremental rebuild over an edited audit writes the same bytes as a from-scratch build of the same inputs, the store included. They also check that a no-change rebuild reuses every unit, that a corrupt or bypassed store falls back to a full build instead of reusing stale bytes, that a cold build ships each unit's per-config class map in the order the audit lists its configs, and that builds at one thread and at several agree.

None of these properties depends on a glyph, so the workload is the frozen mini-M1 bundle under rebuild/review/fixtures/mini/ (real windows, their subset-table slices, and the after font they were extracted with) instead of the live build. That keeps the module in the contracts lane, with each build taking seconds. `fixtures/mini/regenerate.py` refreshes the bundle. The stamp, store and id tests further down use synthetic inputs.

The builds run in the review crate (`build.build_m1`), which reads every store and stamp this module checks; the stamp tests call the Python side that computes the stamps the crate's stores carry (`unit_cache.environment_stamp`, `unit_cache.signature_environment`).
"""

import gzip
import hashlib
import json
import os
import re
import shutil
import zlib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import fingerprint, fixtures, kernel_exec, row_delta, output_sink, spec_load
from rebuild.review import app_index, corpus_exec, landing, unit_cache, unit_index
from rebuild.review import build as review_build
from rebuild.review.audit import SLIM_OMITTED_KEYS, slim_fragment
from rebuild.review.build import SITE_BEFORE_FONT, SITE_JUNIOR_FONT, _write_shard, build_m1

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"
# The review crate's digest as the stamp tests pass it (`units_exec.crate_digest` in a build), so a stamp's other lines are what a test moves.
CRATE = "the review crate's digest"
# The two header keys an ink-signature store records beside its stamp, the audit's identity and the family keys' digest, which say which audit and family keys it was written over (`unit_cache.audit_identity`, `unit_cache.family_keys_digest`).
SIGNATURE_HISTORY_KEYS = ("audit_identity", "family_keys_digest")


def _build(out, bundle, audit_path=MINI_AUDIT, ledger_path=None, **kwargs):
    """Build one mini corpus over the frozen bundle: its subset tables, its after font, and the ledger and spec that the `mini_bundle` fixture materializes from the bundle's pin. The pinned spec keeps the module independent of the working tree's runes: the enricher re-settles every window from it, so reading the live runes would break this module on every rune edit until the bundle was regenerated. Pass `ledger_path` to use an edited ledger in place of the pinned one."""
    return build_m1(
        out,
        audit_path=audit_path,
        ledger_path=ledger_path or bundle.ledger,
        subset_dir=MINI,
        after_font=MINI_FONT,
        spec_root=bundle.spec_root,
        subset_pack=bundle.subset_pack,
        **kwargs,
    )


def _tree(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(Path(path).rglob("*")) if p.is_file()
    }


def _counts(text: str, pattern: str) -> tuple[int, ...]:
    match = re.search(pattern, text)
    assert match, f"the build did not report {pattern!r}"
    return tuple(int(group.replace(",", "")) for group in match.groups())


CACHED = r"reused (\d[\d,]*) of (\d[\d,]*) units from the cache"
BYTE_COPIED = r"byte-copied (\d[\d,]*) of (\d[\d,]*) fragments, copied forward (\d[\d,]*) sidecar rows"
MISS_NOTE = re.compile(r"(unit cache|ink-signature store): .*")


def _cached(capfd) -> tuple[int, int]:
    cached, total = _counts(capfd.readouterr().err, CACHED)
    return cached, total


def _part_mtimes(corpus: Path) -> dict[str, int]:
    return {path.name: path.stat().st_mtime_ns for path in _shard_paths(corpus)}


def _head_stats(corpus: Path) -> dict[str, tuple[int, int]]:
    """The units index's head and the store's head by name, each as its inode and mtime, which a write that keeps a head in place leaves as they were."""
    paths = (row_delta.head_path(corpus, name) for name in (unit_index.INDEX_NAME, unit_cache.STORE_NAME))
    return {path.name: (path.stat().st_ino, path.stat().st_mtime_ns) for path in paths}


def _copy(base: Path, tmp_path: Path) -> Path:
    target = tmp_path / "corpus"
    shutil.copytree(base, target)
    return target


def _stamps(corpus: Path) -> dict:
    stamps = unit_cache.store_stamps(corpus)
    assert stamps is not None
    return stamps


def _store_lines(corpus: Path) -> tuple[dict, list[bytes]]:
    materialized = unit_cache.materialized_store(corpus)
    assert materialized is not None
    header, lines = materialized
    return header, list(lines)


def _rewrite_store(
    corpus: Path,
    edit: Callable[[dict], dict] | None = None,
    *,
    lines: list[bytes] | None = None,
    parts: list[str] | None = None,
) -> None:
    """Rewrite the store whole as its next generation under its own stamps, with `edit` applied to every record, or with `lines` in place of its records, recording the sizes of the parts it records or of `parts`."""
    header, held = _store_lines(corpus)
    if lines is None:
        assert edit is not None
        lines = [(json.dumps(edit(json.loads(line))) + "\n").encode() for line in held]
    stamp: fingerprint.EnvironmentStamp | str = header["environment"]
    if "environment_lines" in header:
        stamp = fingerprint.EnvironmentStamp(
            tuple(header["environment_lines"]),
            tuple((label, tuple(values)) for label, values in header["environment_detail"].items()),
        )
    unit_cache.write_store(corpus, stamp, lines, parts=header["parts"] if parts is None else parts)


def _signature_lines(data: bytes) -> bytes:
    """An ink-signature store's bytes decompressed, with its header line re-serialized without the two stamps that say which audit and family keys it was written over (`SIGNATURE_HISTORY_KEYS`), as `perf_fingerprint` projects them: a build that leaves the store as it is keeps the stamps of the build that wrote it, so they record the store's history and not its entries."""
    header, _, lines = gzip.decompress(data).partition(b"\n")
    fields = json.loads(header)
    for key in SIGNATURE_HISTORY_KEYS:
        fields.pop(key, None)
    return json.dumps(fields).encode() + b"\n" + lines


def _form(corpus: Path, tmp_path: Path) -> dict[str, bytes]:
    """Return the corpus's materialized form (`build.materialize_corpus`), the files a from-scratch build of its units writes, as a tree, with the ink-signature store read without its stamps (`_signature_lines`)."""
    target = tmp_path / f"form-{len(list(tmp_path.glob('form-*')))}"
    review_build.materialize_corpus(corpus, target)
    tree = _tree(target)
    if unit_cache.SIGNATURE_STORE_NAME in tree:
        tree[unit_cache.SIGNATURE_STORE_NAME] = _signature_lines(tree[unit_cache.SIGNATURE_STORE_NAME])
    return tree


def _deflated(body: bytes) -> bytes:
    """A fragment's plain shard bytes deflated as a shard part holds a deflated fragment (#618's change 35): alone, as raw deflate at level 1 with no header, trailer or preset dictionary, the stream `unit_index.inflate_fragment` reads back."""
    return zlib.compress(body, 1, wbits=-15)


def _live(corpus: Path) -> dict[str, dict]:
    """Return the corpus's live fragments by id, through the live-fragment iterator, which inflates a deflated one."""
    return {fragment["id"]: fragment for fragment in unit_index.iter_shard_fragments(corpus)}


def _held_deflated(corpus: Path) -> set[str]:
    """Return the ids of the units whose fragments the corpus holds deflated (#618's change 35), read three ways that must agree: the live slots the unit store gives, each with its inflated length, which it inflates to; the store's records, whose address has a fourth element, that length, exactly then (decision 76); and the app-index and locator rows, which end with `"deflated": true` exactly then and never carry the key otherwise (decision 77)."""
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "store"
        slots = [slot for meta in live.ordered() for slot in live.slots(meta)]
        held = {slot.unit_id: slot.inflated for slot in slots if slot.inflated}
        for slot in slots:
            if slot.inflated:
                assert len(live.read(slot)) == slot.inflated
    _header, lines = _store_lines(corpus)
    recorded = {unit_index.record_id(line): unit_index.record_address(line) for line in lines}
    assert {unit_id: address[3] for unit_id, address in recorded.items() if address and address[3]} == held
    rows = (app_index.load_rows(corpus, app_index.APP_INDEX_NAME) or []) + (
        app_index.load_locator_rows(corpus) or []
    )
    assert len(rows) == len(slots)
    for row in rows:
        assert ("deflated" in row) == (row["id"] in held), row["id"]
        assert "deflated" not in row or (row["deflated"] is True and list(row)[-1] == "deflated"), row
    return set(held)


def _fold_due(corpus: Path, *class_ids: str) -> None:
    """Give each class in `class_ids` enough dead bytes that the next build folds it (#618's decision 53): copies of its first part as overflow parts (`units/<class>.x<NNN>.json`) the manifest lists after its own and no store record names, until its dead bytes reach its live bytes. The manifest and the store are rewritten over the new parts."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    added: list[str] = []
    with unit_index.LiveCorpus(corpus) as live:
        for meta in manifest["classes"]:
            if meta["id"] not in class_ids:
                continue
            parts = unit_index.class_shards(meta)
            live_bytes = sum(slot.length + 1 for slot in live.slots(meta))
            held = sum((corpus / part).stat().st_size - len(unit_index.PART_CLOSING) for part in parts)
            raw = (corpus / parts[0]).read_bytes()
            numbers = [unit_index.overflow_index(meta["id"], part) for part in parts]
            number = max((found for found in numbers if found is not None), default=-1) + 1
            while held - live_bytes < live_bytes:
                name = f"units/{meta['id']}.x{number:03d}.json"
                (corpus / name).write_bytes(raw)
                parts.append(name)
                added.append(name)
                held += len(raw) - len(unit_index.PART_CLOSING)
                number += 1
            meta.pop("shard", None)
            meta["shards"] = parts
    assert added, class_ids
    review_build._write_json(corpus / "manifest.json", manifest)
    _rewrite_store(corpus, lambda record: record, parts=[*_stamps(corpus)["parts"], *added])


FOLDED_HUMAN_CLASS = "same-joins-narrower"
DEFLATED_FROM = "identical-variant-name"


def deflated_corpus_of(build_once, mini_corpus: Path, mini_bundle) -> tuple[Path, Path, Path]:
    """Return the mini corpus held partly deflated (#618's change 35), built once a run (`build_once`), beside a from-scratch build of its units, for the tests that read such a corpus. A copy of the mini corpus is rebuilt over `_edited_audit`'s two retags, which appends both retagged units, deflated, to `RETAG_CLASS`'s first overflow part, and then over its one retag with `RETAG_CLASS` and `FOLDED_HUMAN_CLASS` due to fold (`_fold_due`). That build folds both, deflated: the one retagged unit that stays is copied in as it was stored, and every plain fragment is deflated. The other retagged unit returns to `DEFLATED_FROM`, appended there deflated beside its plain base part. So the corpus holds a folded class of machine units and one of human units, all deflated, a deflated append beside a plain base part, and plain classes. Beside it are a from-scratch build over the one-retag audit and that audit, whose path the manifest records, so a test rebuilds over the same path."""

    def build(directory: Path) -> None:
        corpus = directory / "corpus"
        shutil.copytree(mini_corpus, corpus)
        audits = [_edited_audit(directory, retags) for retags in (1, 2)]
        _build(corpus, mini_bundle, audit_path=audits[1], jobs=1)
        _fold_due(corpus, RETAG_CLASS, FOLDED_HUMAN_CLASS)
        _build(corpus, mini_bundle, audit_path=audits[0], jobs=1)
        _build(directory / "scratch", mini_bundle, audit_path=audits[0], jobs=1)

    directory = build_once("mini-corpus-deflated", build)
    return directory / "corpus", directory / "scratch", directory / "audit-edited-1.tsv"


@pytest.fixture(scope="module")
def deflated_corpus(build_once, mini_corpus, mini_bundle) -> tuple[Path, Path, Path]:
    return deflated_corpus_of(build_once, mini_corpus, mini_bundle)


@pytest.mark.parametrize("held", ["plain", "deflated"])
def test_no_change_rebuild_reuses_every_unit_and_is_byte_stable(
    mini_corpus, deflated_corpus, mini_bundle, tmp_path, capfd, held
):
    """A rebuild over unchanged inputs reuses every unit, keeps every fragment at its address without rewriting any shard part (the parts' mtimes do not change) or appending any, projects every sidecar row from the previous sidecars without parsing a fragment, and writes the units index's head alone and the store's head alone, since no record changed, each left in place, the same file with its inode and mtime, since it comes out the same. The corpus it leaves is byte for byte the one it found. That holds over the mini corpus, all plain, and over one that holds folded classes and an append deflated (`deflated_corpus`, #618's change 35), whose deflated fragments keep their bytes and whose records and copied-forward rows keep their marker."""
    if held == "plain":
        corpus, audit = _copy(mini_corpus, tmp_path), MINI_AUDIT
    else:
        corpus, audit = _copy(deflated_corpus[0], tmp_path), deflated_corpus[2]
        assert _held_deflated(corpus)
    before = _tree(corpus)
    mtimes = _part_mtimes(corpus)
    heads = _head_stats(corpus)
    capfd.readouterr()
    _build(corpus, mini_bundle, audit_path=audit, jobs=1)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert cached == total
    assert MISS_NOTE.search(report) is None, report
    byte_copied, written, copied_forward = _counts(report, BYTE_COPIED)
    assert (byte_copied, written, copied_forward) == (total, total, total)
    assert "(store: head only)" in report and "(index: head only)" in report
    assert (
        "appended 0.0 MB (0.0 MB inflated), kept 0 by compare, folded 0 classes, wrote 0 directly, "
        "deflated 0 fragments, dead 0.0 MB"
    ) in report
    assert _part_mtimes(corpus) == mtimes
    assert _head_stats(corpus) == heads
    assert _tree(corpus) == before


RETAG_CLASS = "identical-unused-join-name"


def _edited_audit(tmp_path: Path, retags: int = 1) -> Path:
    """Return the mini audit with one window dropped and `retags` windows moved to another ledger class, the first `retags` of the same list each time. Dropping a window shifts the triage positions, and so the batches, of the units after it. Retagging a window changes its class and so its duplicate-group key. An incremental rebuild must recompute both, not only patch units in place.

    The retag uses a matched class rather than UNMATCHED for a data reason: `build_m1` refuses an ink-identical window that claims a change group, which is true of the live corpus (every UNMATCHED window is a real new join under review) but not of a window a test declares UNMATCHED by editing a TSV. Every row of the window moves together, since two matched classes on one triple is a classification bug the loader raises on.
    """
    lines = MINI_AUDIT.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]
    windows: list[str] = []
    classes: dict[str, str] = {}
    for row in rows:
        fields = row.split("\t")
        if fields[1] not in classes:
            windows.append(fields[1])
        classes.setdefault(fields[1], fields[3])
    dropped = windows[3]
    retagged = [window for window in windows[4:] if classes[window] != RETAG_CLASS and window != dropped][
        :retags
    ]
    edited = []
    for row in rows:
        fields = row.split("\t")
        if fields[1] == dropped:
            continue
        if fields[1] in retagged:
            fields[3] = RETAG_CLASS
        edited.append("\t".join(fields))
    path = tmp_path / f"audit-edited-{retags}.tsv"
    path.write_text("\n".join([header] + edited) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def edited_scratch(build_once, mini_bundle) -> tuple[list[Path], list[Path]]:
    """Return two edited mini audits (`_edited_audit`, with one window retagged and then two) and a from-scratch build of each, built once a run (`build_once`) for every case of the incremental-rebuild tests, which only read them."""

    def build(directory: Path) -> None:
        for retags in (1, 2):
            _build(
                directory / f"scratch-{retags}",
                mini_bundle,
                audit_path=_edited_audit(directory, retags),
                jobs=1,
            )

    directory = build_once("mini-corpus-edited-audits", build)
    return [directory / f"audit-edited-{retags}.tsv" for retags in (1, 2)], [
        directory / f"scratch-{retags}" for retags in (1, 2)
    ]


def _overflow_parts(corpus: Path) -> list[str]:
    return sorted(path.name for path in (corpus / "units").glob("*.x*.json"))


@pytest.mark.parametrize("seed", ["copy", "clone"])
def test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit(
    mini_corpus, mini_bundle, edited_scratch, tmp_path, capfd, seed
):
    """After an audit edit that drops one window and retags another, an incremental rebuild reuses all but at most two units, leaves every shard part it found in place, appends the retagged unit's fragment to its new class's first overflow part, deflated (#618's change 35), with its store record's fourth address element and its row's marker (`_held_deflated`), leaves its old slot dead, and writes the store as a segment and the units index as one segment of the records the edit changed. The files on disk differ from a from-scratch build's (decision 48), but read through the live-fragment iterator, which inflates, its fragments are that build's by id, and materialized (`build.materialize_corpus`) the two corpora are byte for byte the same files, store included. A second edit that retags one more window into the same class appends to that overflow part, beside the deflated fragment it holds, to the same bytes. The same holds for a build seeded as the artifact cycle seeds it beside the served corpus, a copy-on-write clone (`landing.clone_tree`), and that build leaves the tree it was cloned from byte for byte as it was. Every case compares against the from-scratch builds `edited_scratch` makes."""
    audits, scratches = edited_scratch
    served = _copy(mini_corpus, tmp_path)
    before = _tree(served)
    if seed == "clone":
        incremental = tmp_path / "corpus.next"
        landing.clone_tree(served, incremental)
    else:
        incremental = served
    mtimes = _part_mtimes(incremental)
    capfd.readouterr()
    _build(incremental, mini_bundle, audit_path=audits[0], jobs=1)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert 0 < total - cached <= 2
    byte_copied, _written, _copied_forward = _counts(report, BYTE_COPIED)
    assert 0 < byte_copied <= cached
    assert {name: stamp for name, stamp in _part_mtimes(incremental).items() if name in mtimes} == mtimes
    assert _overflow_parts(incremental) == [f"{RETAG_CLASS}.x000.json"]
    assert "(store: a segment of " in report and "(index: a segment of " in report
    index_head = row_delta.read_head(incremental, unit_index.INDEX_NAME)
    assert index_head is not None and len(index_head.segments) == 1
    assert 0 < index_head.segments[0].rows < index_head.base.rows
    assert not unit_index.index_path(incremental).exists()
    appended = _held_deflated(incremental)
    with unit_index.LiveCorpus(incremental) as live:
        overflow = [
            slot.unit_id for slot in live.slots(_class_meta(incremental, RETAG_CLASS)) if "x000" in slot.part
        ]
    assert len(appended) == 1 and appended == set(overflow)
    assert _live(incremental) == _live(scratches[0])
    assert _form(incremental, tmp_path) == _form(scratches[0], tmp_path)
    _build(incremental, mini_bundle, audit_path=audits[1], jobs=1)
    assert _overflow_parts(incremental) == [f"{RETAG_CLASS}.x000.json"]
    assert len(_held_deflated(incremental)) == 2 and appended < _held_deflated(incremental)
    assert _live(incremental) == _live(scratches[1])
    assert _form(incremental, tmp_path) == _form(scratches[1], tmp_path)
    if seed == "clone":
        assert _tree(served) == before


def _class_meta(corpus: Path, class_id: str) -> dict:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    return next(meta for meta in manifest["classes"] if meta["id"] == class_id)


def _class_fragments(corpus: Path, class_id: str) -> list[dict]:
    """Return a class's live fragments in id order through the live-fragment iterator, which inflates a deflated one."""
    with unit_index.LiveCorpus(corpus) as live:
        return [fragment for _meta, _slot, fragment in live.fragments(classes=[class_id])]


def _ledger_with(bundle, tmp_path: Path, class_id: str, *, no_verdict: bool) -> Path:
    """Return the bundle's pinned ledger with one class's `no_verdict` set as given. This edit moves a unit between the slim and the full fragment shape without changing its content key."""
    entries = yaml.safe_load(bundle.ledger.read_text(encoding="utf-8"))
    entry = next(entry for entry in entries if entry["id"] == class_id)
    assert entry.get("no_verdict", False) != no_verdict
    entry["no_verdict"] = no_verdict
    path = tmp_path / f"ledger-{class_id}.yaml"
    path.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def test_a_slim_fragment_is_the_shape_of_every_unit_that_takes_no_verdict(mini_corpus):
    """Over the whole mini corpus, a fragment omits the explain, the drafts, and the highlight if and only if its unit is machine-approved or in a no-verdict class, and the store records which shape it wrote for each unit."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    store = unit_cache.load_store(mini_corpus, _store_environment(mini_corpus))
    assert store is not None
    slim_by_id = {record["id"]: record["slim"] for record in store.values()}
    shapes = {True: 0, False: 0}
    for meta in manifest["classes"]:
        for fragment in _class_fragments(mini_corpus, meta["id"]):
            slim = slim_fragment(fragment)
            assert slim == (
                bool(meta["no_verdict"])
                or any(fragment[c] for c in ("ink_identical", "picture_identical", "junior_equivalent"))
            )
            assert [key in fragment for key in SLIM_OMITTED_KEYS] == [not slim] * len(
                SLIM_OMITTED_KEYS
            ), fragment["id"]
            assert slim_by_id[fragment["id"]] is slim
            shapes[slim] += 1
    assert shapes[True] and shapes[False], "the mini corpus must hold both fragment shapes"


def _store_environment(corpus: Path) -> str:
    return _stamps(corpus)["environment"]


def _flipping_class(corpus: Path, *, no_verdict: bool) -> tuple[str, int]:
    """Return a mini-corpus class whose `no_verdict` is as given and that holds units no machine check approves, with the number of those units. Flipping the class's exemption moves those units between the fragment shapes."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    for meta in manifest["classes"]:
        flipping = meta["unit_count"] - meta["machine_approved_count"]
        if bool(meta["no_verdict"]) is no_verdict and flipping > 0:
            return meta["id"], flipping
    raise AssertionError(
        f"the mini corpus holds no {'exempt' if no_verdict else 'human'} class a flip could move"
    )


def test_a_unit_that_loses_its_exemption_is_re_enriched_in_full(mini_corpus, mini_bundle, tmp_path, capfd):
    """`no_verdict` comes from the ledger and is outside the content key, so a unit whose key does not change could be given a fragment of the wrong shape unless the store records which shape it holds. When a class loses its `no_verdict`, every unit in it that no machine check approves is a cache miss and is drafted in full, the machine-approved ones stay cached, and the corpus is byte-identical to a from-scratch build under the edited ledger."""
    EXEMPT_CLASS, flipping = _flipping_class(mini_corpus, no_verdict=True)
    assert not _class_meta(mini_corpus, EXEMPT_CLASS)["batches"]
    ledger = _ledger_with(mini_bundle, tmp_path, EXEMPT_CLASS, no_verdict=False)
    incremental = _copy(mini_corpus, tmp_path)
    capfd.readouterr()
    _build(incremental, mini_bundle, ledger_path=ledger, jobs=1)
    cached, total = _cached(capfd)
    assert total - cached == flipping
    for fragment in _class_fragments(incremental, EXEMPT_CLASS):
        assert fragment["no_verdict"] is False
        whole = not slim_fragment(fragment)
        assert all((key in fragment) == whole for key in SLIM_OMITTED_KEYS), fragment["id"]
        if whole:
            assert fragment["drafts"]["pin"]["expect"]
    scratch = tmp_path / "scratch"
    _build(scratch, mini_bundle, ledger_path=ledger, jobs=1)
    assert _form(incremental, tmp_path) == _form(scratch, tmp_path)


def test_a_unit_that_gains_its_exemption_is_written_slim(mini_corpus, mini_bundle, tmp_path, capfd):
    """The reverse flip: when a class gains `no_verdict`, its human units are re-drafted slim instead of being reused whole with drafts nobody will read, so the cached corpus matches what a from-scratch build writes."""
    HUMAN_CLASS, flipping = _flipping_class(mini_corpus, no_verdict=False)
    ledger = _ledger_with(mini_bundle, tmp_path, HUMAN_CLASS, no_verdict=True)
    incremental = _copy(mini_corpus, tmp_path)
    capfd.readouterr()
    _build(incremental, mini_bundle, ledger_path=ledger, jobs=1)
    cached, total = _cached(capfd)
    assert total - cached == flipping
    for fragment in _class_fragments(incremental, HUMAN_CLASS):
        assert fragment["no_verdict"] is True and "batch" not in fragment
        assert not any(key in fragment for key in SLIM_OMITTED_KEYS), fragment["id"]
    scratch = tmp_path / "scratch"
    _build(scratch, mini_bundle, ledger_path=ledger, jobs=1)
    assert _form(incremental, tmp_path) == _form(scratch, tmp_path)


def _shard_paths(corpus: Path) -> list[Path]:
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    return [corpus / part for meta in manifest["classes"] for part in unit_index.class_shards(meta)]


def test_a_fragment_whose_stamp_moved_is_not_reused(mini_corpus, mini_bundle, tmp_path, capfd):
    """The cache fetches a prior fragment by id, and the id says nothing about the file's contents. So the store records the stamp each fragment was written with, and the build reuses a fragment only while the shard on disk still carries that stamp. A fragment edited underneath the store is recomputed, which restores the correct fragment: the class, which the store no longer describes, is written directly by the share rule, deflated (#618's change 35), so the corpus materializes to the files the unedited build wrote."""
    corpus = _copy(mini_corpus, tmp_path)
    path = next(path for path in _shard_paths(corpus) if json.loads(path.read_text(encoding="utf-8")))
    fragments = json.loads(path.read_text(encoding="utf-8"))
    fragments[0]["content_key"] = "0" * 64
    path.write_text(json.dumps(fragments), encoding="utf-8")
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    cached, total = _cached(capfd)
    assert cached == total - 1
    assert _live(corpus) == _live(mini_corpus)
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


def _environment(corpus: Path) -> str:
    return _stamps(corpus)["environment"]


def test_a_store_without_addresses_still_supplies_every_unit_through_the_walk(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    """A store whose records have no address names each unit's fragment by id and class only. The build still reuses every unit from it, placing the units by a walk over the previous corpus's shards, and writes the same bytes as a build reused from an addressed store, including the addresses in the rewritten store."""
    corpus = _copy(mini_corpus, tmp_path)
    _rewrite_store(corpus, lambda record: {key: value for key, value in record.items() if key != "address"})
    store = unit_cache.load_store(corpus, _environment(corpus))
    assert store is not None and all("address" not in record for record in store.values())
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    cached, total = _cached(capfd)
    assert cached == total
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


def test_a_shard_rewritten_underneath_the_store_is_walked_rather_than_trusted(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    """An address is valid only for the bytes it was taken from, and the manifest stamp says nothing about a shard's bytes. So the store records each part's size, and a part rewritten underneath it (here compactly, with every stamp intact) is placed by the walk again. Every unit is still reused, and the parts that did not change are trusted as before. The walked units are not copied as they are, so nothing predicts them unchanged, and the share rule writes their class directly, deflated (#618's change 35), with each record's fourth address element and each row's marker, while every other part stays as it was. The corpus materializes to the files the unedited build wrote (decision 48)."""
    corpus = _copy(mini_corpus, tmp_path)
    path = next(path for path in _shard_paths(corpus) if json.loads(path.read_text(encoding="utf-8")))
    fragments = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(fragments), encoding="utf-8")
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert cached == total
    assert "wrote 1 directly" in report and f"deflated {len(fragments):,} fragments" in report
    assert _held_deflated(corpus) == {fragment["id"] for fragment in fragments}
    assert {
        name: data
        for name, data in _tree(corpus).items()
        if name.startswith("units/") and name != f"units/{path.name}"
    } == {
        name: data
        for name, data in _tree(mini_corpus).items()
        if name.startswith("units/") and name != f"units/{path.name}"
    }
    assert _live(corpus) == _live(mini_corpus)
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


@pytest.mark.parametrize("write_jobs", [1, 3])
def test_an_edit_in_place_under_a_trusted_address_is_refused_at_the_write(
    mini_corpus, mini_bundle, tmp_path, write_jobs
):
    """The size check cannot see an edit that leaves a part the same length, so such a fragment is trusted into the plan. The reader catches it at the write, where every cached fragment is checked against its record, and the build fails instead of reusing bytes the store does not describe, at one write thread or several, and either way it leaves no staged part behind. A build that recomputes every unit recovers the corpus: it reuses no record, and the write's compare finds the edited slot unequal to the recomputed fragment and appends it, so the corpus holds the unedited build's fragments and materializes to its files."""
    corpus = _copy(mini_corpus, tmp_path)
    path = next(path for path in _shard_paths(corpus) if json.loads(path.read_text(encoding="utf-8")))
    fragments = json.loads(path.read_text(encoding="utf-8"))
    stamp = fragments[0]["content_key"]
    raw = path.read_bytes()
    edited = raw.replace(stamp.encode(), ("0" * len(stamp)).encode(), 1)
    assert len(edited) == len(raw)
    path.write_bytes(edited)
    with pytest.raises(SystemExit) as raised:
        _build(corpus, mini_bundle, jobs=1, write_jobs=write_jobs)
    assert "cannot be read back" in str(raised.value)
    assert not list(corpus.rglob("*.partial"))
    _build(corpus, mini_bundle, jobs=1, write_jobs=write_jobs, recompute_all_units=True)
    assert _overflow_parts(corpus)
    assert _live(corpus) == _live(mini_corpus)
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


def test_a_store_whose_ink_deltas_moved_fails_the_verification_sample(mini_corpus, mini_bundle, tmp_path):
    """The ink deltas are outside the content key (`unit_cache.CARRY_PRESENTATION_KEYS`), so the stamp does not cover them, and the verification sample compares them separately against a recomputation. A store whose deltas no longer match the fonts fails the build."""
    corpus = _copy(mini_corpus, tmp_path)
    _rewrite_store(
        corpus, lambda record: {**record, "ink_deltas": {**record["ink_deltas"], "bogus": "d-000000000000"}}
    )
    with pytest.raises(SystemExit) as raised:
        _build(corpus, mini_bundle, jobs=1)
    assert "do not match a recomputation" in str(raised.value)


def test_corrupt_store_degrades_to_a_full_build(mini_corpus, mini_bundle, tmp_path, capfd):
    """A store whose head does not read is unusable: the build recomputes every unit and writes the store whole again as generation 0, since no head names a generation to follow, and counts the rewrite (`output_sink.note_rewritten`). Nothing it holds predicts a unit unchanged, so the share rule writes every class directly, deflated (#618's change 35), and the corpus materializes to the files the mini build wrote (decision 48)."""
    corpus = _copy(mini_corpus, tmp_path)
    row_delta.head_path(corpus, unit_cache.STORE_NAME).write_bytes(b"not a head")
    _build(corpus, mini_bundle, jobs=1)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert cached == 0
    assert f"unit cache: {unit_cache.UNREADABLE_NOTE}" in report
    assert "(store: a new base (unusable)" in report
    assert len(_held_deflated(corpus)) == total
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


def test_a_store_that_fails_midway_degrades_to_a_full_build(mini_corpus, mini_bundle, tmp_path, capfd):
    """The plan loads each record as the stream returns it, so a store that stops reading partway has already put rows into the unit store. The build discards those rows with the store: every unit is recomputed, the unreadable note is printed, and the corpus is byte-identical to a from-scratch build over the same audit."""
    corpus = _copy(mini_corpus, tmp_path)
    _header, lines = _store_lines(corpus)
    assert len(lines) > 3
    broken = json.loads(lines[len(lines) // 2])
    del broken["proj"]
    _rewrite_store(corpus, lines=lines[: len(lines) // 2] + [(json.dumps(broken) + "\n").encode()])
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    report = capfd.readouterr().err
    cached, _total = _counts(report, CACHED)
    assert cached == 0
    assert f"unit cache: {unit_cache.UNREADABLE_NOTE}" in report
    assert "(store: a new base (unusable)" in report
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


@pytest.mark.parametrize("held", ["plain", "deflated"])
def test_recompute_all_units_reuses_no_record_and_keeps_every_fragment_in_place(
    mini_corpus, deflated_corpus, mini_bundle, tmp_path, capfd, held
):
    """`recompute_all_units` reuses no record and recomputes every unit, but the prior store still gives each fragment's address, so the shards compare every recomputed fragment with its prior, the units index compares every recomputed record with its prior and writes its head alone, and a corpus whose units all come out the same is left byte for byte as it was, its ink-signature store, which the pass reshapes, read without the two stamps that record which audit and family keys it was written over (`_signature_lines`). Over a corpus that holds fragments deflated (`deflated_corpus`, #618's change 35), the compare reads each deflated prior's recorded inflated length (decision 76) and inflates it, so a deflated prior whose inflated bytes equal the new fragment's is kept where it is, deflated, with its record's fourth address element and its row's marker."""
    if held == "plain":
        corpus, audit = _copy(mini_corpus, tmp_path), MINI_AUDIT
    else:
        corpus, audit = _copy(deflated_corpus[0], tmp_path), deflated_corpus[2]
        assert _held_deflated(corpus)
    before = _tree(corpus)
    _build(corpus, mini_bundle, audit_path=audit, jobs=1, recompute_all_units=True)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert cached == 0
    assert f"appended 0.0 MB (0.0 MB inflated), kept {total:,} by compare" in report
    assert "(index: head only)" in report
    after = _tree(corpus)
    for tree in (before, after):
        tree[unit_cache.SIGNATURE_STORE_NAME] = _signature_lines(tree[unit_cache.SIGNATURE_STORE_NAME])
    assert after == before


def test_serial_and_parallel_builds_are_byte_identical(mini_corpus, mini_bundle, tmp_path):
    """A build at two threads drafts the recomputed units on both, in whatever order the threads take their hand-outs, and `mini_corpus` drafts the same units on one. Both produce the same bytes because every order-sensitive whole-corpus pass runs over all the projections once the units phase has finished, so this comparison would show any effect of a thread's hand-out order on the output. A first build writes the units index as a head over generation 0's base and no single file (#618's change 32)."""
    parallel = tmp_path / "parallel"
    _build(parallel, mini_bundle, jobs=2)
    assert _tree(parallel) == _tree(mini_corpus)
    assert sorted(path.name for path in parallel.glob(f"{unit_index.INDEX_NAME}*")) == [
        row_delta.base_name(unit_index.INDEX_NAME, 0),
        row_delta.head_name(unit_index.INDEX_NAME),
    ]


def _sidecars(corpus: Path) -> dict[str, bytes]:
    """Return the sidecars' files by name: the units index's head and every part beside it, and the three app sidecars."""
    return {
        **{path.name: path.read_bytes() for path in sorted(corpus.glob(f"{unit_index.INDEX_NAME}*"))},
        **{
            name: (corpus / name).read_bytes()
            for name in (app_index.APP_INDEX_NAME, app_index.LOCATOR_NAME, app_index.LOCATOR_ROWS_NAME)
        },
    }


def _sidecars_held_whole(corpus: Path, scratch: Path) -> dict[str, bytes]:
    """Return the sidecars `unit_index.write_index` and `app_index.write_app_artifacts` write over a corpus's fragments held all at once, beside a copy of its manifest. Each class's spans come from rewriting its fragments through `_write_shard`, which must give back the corpus's own parts. These writers project the lines from the fragments and cut the locator's blocks in one pass over every machine unit, so they write the bytes a build's sidecars must have however the build holds its lines."""
    scratch.mkdir()
    shutil.copy2(corpus / "manifest.json", scratch / "manifest.json")
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    shards: dict[str, list[dict]] = {}
    spans: dict[str, list[tuple[int, int, int]]] = {}
    for meta in manifest["classes"]:
        parts = unit_index.class_shards(meta)
        shards[meta["id"]] = [
            fragment for part in parts for fragment in json.loads((corpus / part).read_text(encoding="utf-8"))
        ]
        rewritten, spans[meta["id"]] = _write_shard(scratch, meta["id"], shards[meta["id"]])
        assert [(scratch / part).read_bytes() for part in rewritten] == [
            (corpus / part).read_bytes() for part in parts
        ]
    unit_index.write_index(scratch, shards.items())
    app_index.write_app_artifacts(scratch, shards, spans)
    return _sidecars(scratch)


def test_a_write_on_several_threads_is_byte_identical_to_the_serial_one(mini_bundle, tmp_path, capfd):
    """The write cuts the classes into runs of shard order and, wider than one, writes the runs on threads, each with its own shard writer and compressed sidecar lines, narrowed to the runs the largest class allows. So a cold build and a cached rebuild whose write is given three threads match a serial build byte for byte, and the sidecars of all three are the bytes the writers that hold every fragment at once write over the same shards (`_sidecars_held_whole`), the units index's head and parts among them, since every writer of the index takes the same route (#618's change 32). A rebuild over an edited audit that appends changed fragments to a class's overflow parts lays them out the same at either width, overflow parts and store segment included, since a class's appends go in its id order on the one thread that writes it, and deflates each there (#618's change 35). So does a later rebuild that folds two classes (`_fold_due`), copying the deflated fragments one holds as they were stored and deflating every plain one. The cold build reads, patches and serializes every fragment on the write's threads. The cached rebuild copies every fragment and every sidecar row forward, each run's copy-forward reading past the lines of the runs before it."""
    serial = tmp_path / "serial"
    _build(serial, mini_bundle, jobs=1, write_jobs=1)
    held_whole = _sidecars_held_whole(serial, tmp_path / "held-whole")
    assert _sidecars(serial) == held_whole
    cold = tmp_path / "cold"
    capfd.readouterr()
    _build(cold, mini_bundle, jobs=1, write_jobs=3)
    assert "review.build manifest+check" in capfd.readouterr().err
    assert _tree(cold) == _tree(serial)
    assert _sidecars(cold) == held_whole
    cached = _copy(serial, tmp_path)
    _build(cached, mini_bundle, jobs=1, write_jobs=3)
    byte_copied, total, copied_forward = _counts(capfd.readouterr().err, BYTE_COPIED)
    assert byte_copied == total == copied_forward
    assert _tree(cached) == _tree(serial)
    assert _sidecars(cached) == held_whole
    appended = {}
    edited = _edited_audit(tmp_path, 2)
    for width in (1, 3):
        appended[width] = tmp_path / f"appended-{width}"
        shutil.copytree(serial, appended[width])
        _build(appended[width], mini_bundle, audit_path=edited, jobs=1, write_jobs=width)
    assert _overflow_parts(appended[3]) == [f"{RETAG_CLASS}.x000.json"]
    assert _held_deflated(appended[3])
    assert _tree(appended[3]) == _tree(appended[1])
    for width in (1, 3):
        _fold_due(appended[width], RETAG_CLASS, FOLDED_HUMAN_CLASS)
        capfd.readouterr()
        _build(appended[width], mini_bundle, audit_path=edited, jobs=1, write_jobs=width)
        assert "folded 2 classes" in capfd.readouterr().err
    assert _overflow_parts(appended[3]) == []
    assert _tree(appended[3]) == _tree(appended[1])


def test_a_narrowed_hand_out_is_byte_identical_to_the_serial_build(
    mini_corpus, mini_bundle, tmp_path, monkeypatch
):
    """The units phase hands the recomputed units out one batch at a time, so which thread drafts which unit depends on timing. Narrowing the hand-out (`build.PHASE1_HANDOUT_UNITS`, which the build input's vocabulary carries to the crate) splits the mini bundle's recomputed units into a few dozen batches over two threads, where per-thread state leaking into the output would show up as a changed byte against the serial build. At the checked-in width the hand-out splits them into only a handful of batches, which exercises little interleaving."""
    monkeypatch.setattr(review_build, "PHASE1_HANDOUT_UNITS", 37)
    parallel = tmp_path / "narrowed"
    _build(parallel, mini_bundle, jobs=2)
    assert _tree(parallel) == _tree(mini_corpus)


def test_a_cached_rebuild_on_two_threads_is_byte_identical_to_the_serial_one(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    """A cached rebuild still recomputes its verification sample, and at two threads it settles each thread's share of the sample and matches each report back to its unit. A report matched to its neighbor's unit would recompute that unit under another window, miss its content key, and fail the build. A cached rebuild at two threads runs that path, and its output matches the serial build byte for byte."""
    corpus = _copy(mini_corpus, tmp_path)
    _build(corpus, mini_bundle, jobs=2)
    cached, total = _cached(capfd)
    assert cached == total
    assert _tree(corpus) == _tree(mini_corpus)


SIGNATURES = r"signatures: (\d[\d,]*) cached, (\d[\d,]*) shaped"


def _signatures(capfd) -> tuple[int, int]:
    cached, shaped = _counts(capfd.readouterr().err, SIGNATURES)
    return cached, shaped


def test_a_signature_pass_on_two_threads_is_byte_identical_to_the_serial_one(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    """The signature phase shapes the store's misses in chunks on its own threads and puts every digest on the row that requested it, so a pass over a cold store at two threads builds the corpus, the ink-signature store included, byte for byte as the serial `mini_corpus` was built."""
    threaded = tmp_path / "threaded"
    capfd.readouterr()
    _build(threaded, mini_bundle, jobs=1, signature_jobs=2)
    _cached_signatures, shaped = _signatures(capfd)
    assert shaped > 0
    assert _tree(threaded) == _tree(mini_corpus)


def test_no_change_rebuild_reuses_every_signature(mini_corpus, mini_bundle, tmp_path, capfd):
    corpus = _copy(mini_corpus, tmp_path)
    _build(corpus, mini_bundle, jobs=1)
    cached, shaped = _signatures(capfd)
    assert cached > 0
    assert shaped == 0


def test_corrupt_signature_store_reshapes_and_degrades_to_the_same_bytes(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    corpus = _copy(mini_corpus, tmp_path)
    unit_cache.signature_store_path(corpus).write_bytes(b"not a gzip stream")
    _build(corpus, mini_bundle, jobs=1)
    report = capfd.readouterr().err
    cached, shaped = _counts(report, SIGNATURES)
    assert cached == 0
    assert shaped > 0
    assert f"ink-signature store: {unit_cache.UNREADABLE_NOTE}" in report
    assert _tree(corpus) == _tree(mini_corpus)


def test_a_bundled_build_stamps_the_explain_prose_of_its_own_spec_and_the_checkouts_draft_inputs(
    mini_corpus, mini_bundle
):
    """The manifest's `explain_prose` takes the rune and ledger prose from the bundle's spec, not the checkout's, and the change groups' prose from the checkout, since the materialized spec has no `rebuild/review/`. The rationales the mini corpus quotes come from the spec root the fixture materializes, and hashing the live runes instead would misdescribe the corpus and add every live rune to the contracts closure of every module that uses conftest's `mini_corpus`. `draft_inputs` also comes from the checkout's pages and harness, as the change groups' prose does, through the lines the build took in its plan step."""
    from rebuild.pipeline import fingerprint

    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs_fingerprint"]["explain_prose"] == fingerprint.explain_prose_value(
        REPO_ROOT, mini_bundle.spec_root
    )
    assert manifest["inputs_fingerprint"]["draft_inputs"] == fingerprint.draft_inputs_value(
        fingerprint.draft_input_lines(REPO_ROOT)
    )


def test_unit_store_environment_tracks_each_kernel_settlement_mode(monkeypatch):
    """The store's stamp must change when either kernel settlement mode flag changes, or a store written under one mode would reuse units the other mode never produced. The subset directory is only hashed, never read, so the frozen bundle stands in for the live one."""
    spec = fixtures.mini_spec()

    def stamp():
        return unit_cache.environment_stamp(
            REPO_ROOT,
            spec,
            MINI,
            SITE_BEFORE_FONT,
            SITE_JUNIOR_FONT,
            "after-helpers",
            identical_classes=(),
            units_engine=CRATE,
        )

    prospect = kernel_exec.SIMULATED_PROSPECT_DEFAULT
    follower_prefers = kernel_exec.FOLLOWER_PREFER_SLOTS_DEFAULT
    base = stamp()
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", not prospect)
    assert stamp() != base
    monkeypatch.setattr(kernel_exec, "SIMULATED_PROSPECT_DEFAULT", prospect)
    monkeypatch.setattr(kernel_exec, "FOLLOWER_PREFER_SLOTS_DEFAULT", not follower_prefers)
    assert stamp() != base


def test_unit_store_environment_tracks_the_ledgers_identical_classes():
    """The ledger's `ink_identical` ids decide which units re-route to a change group, and the ledger is otherwise outside the stamp, so the stamp carries the sorted id set: flipping a class's flag drops the store, and the order the ids arrive in does not."""
    spec = fixtures.mini_spec()

    def stamp(identical):
        return unit_cache.environment_stamp(
            REPO_ROOT,
            spec,
            MINI,
            SITE_BEFORE_FONT,
            SITE_JUNIOR_FONT,
            "after-helpers",
            identical_classes=identical,
            units_engine=CRATE,
        )

    base = stamp(("identical-zoo-crown", "identical-variant-name"))
    assert stamp(("identical-variant-name", "identical-zoo-crown")) == base
    assert stamp(("identical-variant-name",)) != base


def test_unit_store_environment_ignores_the_contact_allow_list(tmp_path):
    """Dropping the unit store rebuilds every unit cold, and an edit to the contact allow-list should not cause that: the defect gate is its only reader, and nothing in the review build opens it. The `data` line hashes `fingerprint.data_paths`, which does not include the allow-list. The test uses a hand-built root so the edit is real and does not touch the repository's own file."""
    spec = fixtures.mini_spec()
    root = tmp_path / "repo"
    (root / "rebuild").mkdir(parents=True)
    registry = root / "rebuild" / "script.yaml"
    registry.write_text("alphabet: []\n", encoding="utf-8")
    allow = root / "rebuild" / "m1-contact-allow.yaml"

    def stamp():
        return unit_cache.environment_stamp(
            root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=(), units_engine=CRATE
        )

    base = stamp()
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y1'}\n", encoding="utf-8")
    assert stamp() == base
    allow.write_text("- {signature: 'contact:qsPea.full.ex-y0:qsTea.full.en-y0:y2'}\n", encoding="utf-8")
    assert stamp() == base
    registry.write_text("alphabet: [edited]\n", encoding="utf-8")
    assert stamp() != base


def test_unit_store_environment_ignores_a_schema_hover_and_a_registry_comment(tmp_path):
    """Rewording a schema's `description` or a comment in the registry leaves the store valid, because the `data` line hashes both through `fingerprint.data_path_lines`. A changed schema keyword still drops it."""
    spec = fixtures.mini_spec()
    root = tmp_path / "repo"
    (root / "rebuild" / "schema").mkdir(parents=True)
    registry = root / "rebuild" / "script.yaml"
    schema = root / "rebuild" / "schema" / "rune.schema.json"
    registry.write_text("alphabet: []\n", encoding="utf-8")
    schema.write_text('{"type": "object", "description": "One rune per file."}\n', encoding="utf-8")

    def stamp():
        return unit_cache.environment_stamp(
            root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=(), units_engine=CRATE
        )

    base = stamp()
    registry.write_text("# Registries only.\nalphabet: []  # none yet\n", encoding="utf-8")
    schema.write_text('{"type": "object", "description": "One rune to a file."}\n', encoding="utf-8")
    assert stamp() == base
    schema.write_text('{"type": "array", "description": "One rune to a file."}\n', encoding="utf-8")
    assert stamp() != base


CORPUS_UNREAD_CODE = (
    "rebuild/pipeline/run_m1.py",
    "rebuild/pipeline/oracle.py",
    "rebuild/pipeline/oracle_positions.py",
    "rebuild/pipeline/defects.py",
    "rebuild/pipeline/compile_font.py",
    "rebuild/pipeline/conform.py",
    "rebuild/pipeline/oracle_cache.py",
    "rebuild/pipeline/emit_gsub.py",
    "rebuild/pipeline/explain.py",
    "rebuild/pipeline/fixtures.py",
    "rebuild/pipeline/geometry.py",
    "rebuild/validation/pins.py",
    "rebuild/kernel-rs/src/fixpoint.rs",
    "rebuild/kernel-rs/src/fold.rs",
    "rebuild/kernel-rs/src/fanout.rs",
)
CORPUS_ONLY_CODE = (
    "rebuild/pipeline/kernel_exec.py",
    "rebuild/pipeline/labels.py",
    "rebuild/review/build.py",
    "rebuild/review/unit_cache.py",
    "rebuild/review/enrich.py",
    "rebuild/kernel-rs/src/main.rs",
    "rebuild/kernel-rs/src/engine.rs",
    "rebuild/kernel-rs/Cargo.lock",
)
COMPARATOR_CODE = (
    "rebuild/review/ink.py",
    "rebuild/review/units_exec.py",
    "rebuild/validation/shaping.py",
    "rebuild/validation/classify.py",
    "rebuild/validation/rowmodel.py",
)


def _stamped_root(tmp_path: Path) -> Path:
    """Build a root holding every file the three code rosters name, each written as a Python statement so that the code line's prose-insensitive digest sees a real edit. The stamp tests below edit it through `_edit`, so their assertions are about the rosters, not this checkout."""
    root = tmp_path / "repo"
    for relative in CORPUS_UNREAD_CODE + CORPUS_ONLY_CODE + COMPARATOR_CODE:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(f"RELATIVE = {relative!r}\n", encoding="utf-8")
    return root


def _edit(root: Path, relative: str) -> None:
    (root / relative).write_text(f"RELATIVE = {relative!r}\nEDITED = 1\n", encoding="utf-8")


def test_both_store_stamps_survive_a_pipeline_or_crate_edit_the_corpus_never_reads(tmp_path):
    """An edit to code the corpus build never runs (`CORPUS_UNREAD_CODE`) changes neither stamp, so it does not cost the next build a cold units phase (`unit_cache.corpus_code_paths`). An edit to a module the build runs and no signature reads (`CORPUS_ONLY_CODE`) changes the unit store's stamp and leaves the signature store's alone (`unit_cache.signature_code_paths`). An edit to the code that reads the outlines into the font facts the crate takes signatures from, or a module it imports (`COMPARATOR_CODE`), changes both. The root is hand-built so the edits are to real files and the assertions are about the rosters, not this checkout; rebuild/test_review_code_closure.py checks the rosters against the walked import closure. Each file is a Python statement before and after its edit, because the code line's digest is prose-insensitive (`fingerprint.code_file_digest`) and falls back to raw bytes only for a file that does not parse, so a file of bare prose would test the fallback instead."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)

    def stamps() -> tuple[str, str]:
        unit_stamp, signature_stamp = _both_stamps(root, spec)
        return unit_stamp.value, signature_stamp.value

    base = stamps()
    for relative in CORPUS_UNREAD_CODE:
        _edit(root, relative)
        assert stamps() == base, relative
    previous = base
    for relative in CORPUS_ONLY_CODE:
        _edit(root, relative)
        current = stamps()
        assert current[0] != previous[0], relative
        assert current[1] == base[1], relative
        previous = current
    for relative in COMPARATOR_CODE:
        _edit(root, relative)
        current = stamps()
        assert current[0] != previous[0], relative
        assert current[1] != previous[1], relative
        previous = current


def test_the_unit_store_survives_an_edit_to_a_tools_script_the_drafts_never_import(tmp_path):
    """The `draft_harness` line hashes test/test_shaping.py and the tools/ modules it imports (`fingerprint.font_compile_tool_paths`, whose closure rebuild/test_build_code_closure.py checks), not the whole tools/ directory. So an edit to an old-font script no draft imports leaves the unit store's stamp as it was, and an edit to a font-compile module moves it at `draft_harness` alone, the label the crate's miss note names. The root is hand-built so the edits are to real files and the assertions are about the line, not this checkout."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)
    for relative in ("tools/serve.py", "tools/glyph_compiler.py"):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(f"RELATIVE = {relative!r}\n", encoding="utf-8")

    def stamp() -> fingerprint.EnvironmentStamp:
        return _both_stamps(root, spec)[0]

    base = stamp()
    _edit(root, "tools/serve.py")
    assert stamp().value == base.value
    _edit(root, "tools/glyph_compiler.py")
    edited = stamp()
    assert edited.value != base.value
    assert fingerprint.moved_note(base.labels, edited.labels) == "draft_harness (changed)"


def _both_stamps(root: Path, spec) -> tuple[fingerprint.EnvironmentStamp, fingerprint.EnvironmentStamp]:
    return (
        unit_cache.environment_stamp(
            root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=(), units_engine=CRATE
        ),
        unit_cache.signature_environment(root, MINI_FONT, "after-helpers", CRATE),
    )


def test_the_store_header_records_the_stamp_lines_beside_the_hex_it_compares(tmp_path):
    """A store's header (`unit_cache._stamp_header`, which the build input carries to the crate for both stores) records the hex a load compares as `environment`, and it equals sha256 over the stamp's lines joined by newlines, which a reader reconstructing a stamp from the recorded lines depends on. `environment_lines` and `environment_detail` record the lines themselves beside it, the code label's per-file lines among them, which the crate's miss notes read. A caller holding only the recorded hex still loads the store, as `test_a_landed_corpus_still_answers_for_itself` in rebuild/test_artifact_cycle.py does after landing a corpus. A store written under a bare hex records no lines."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)
    out = tmp_path / "corpus"
    out.mkdir()
    unit_stamp, signature_stamp = _both_stamps(root, spec)
    unit_cache.write_store(out, unit_stamp, [], parts=())
    for stamp, header, code_label in (
        (unit_stamp, unit_cache.store_stamps(out), "corpus_code"),
        (signature_stamp, unit_cache._stamp_header(signature_stamp), "comparator_code"),
    ):
        assert header is not None
        assert header["environment"] == stamp.value
        assert stamp.value == hashlib.sha256("\n".join(stamp.lines).encode()).hexdigest()
        assert header["environment_lines"] == list(stamp.lines)
        assert header["environment_detail"] == {code_label: list(dict(stamp.detail)[code_label])}
        assert (
            stamp.labels[code_label]
            == hashlib.sha256("\n".join(dict(stamp.detail)[code_label]).encode()).hexdigest()
        )
    assert unit_cache.load_store(out, unit_stamp.value) == {}

    bare = tmp_path / "bare"
    bare.mkdir()
    unit_cache.write_store(bare, unit_stamp.value, [], parts=())
    header = unit_cache.store_stamps(bare)
    assert header is not None
    assert "environment_lines" not in header and "environment_detail" not in header
    assert unit_cache.load_store(bare, unit_stamp) == {}


REFUSE_RUNE = """\
rune: qsPea
policy:
  refuse:
  - {exit: baseline, why: two verticals render thick}
"""


def test_a_refuse_why_edit_moves_only_that_family_key(tmp_path):
    """A refusal's `why` is quoted into the explain text a unit carries, so rewording one must re-enrich the windows that show it and no others. The family keys that change are the reworded rune's and those of families that reach it through `resolve.against`, and the whole-store stamp does not change. The test uses a hand-built root so the edit is real, with the fixture spec supplying the closure."""
    spec = fixtures.mini_spec()
    runes = tmp_path / "glyph_data" / "runes"
    runes.mkdir(parents=True)
    (runes / "qsTea.yaml").write_text("rune: qsTea\n", encoding="utf-8")
    (runes / "qsPea.yaml").write_text(REFUSE_RUNE, encoding="utf-8")

    def stamp():
        return unit_cache.environment_stamp(
            tmp_path,
            spec,
            MINI,
            MINI_FONT,
            MINI_FONT,
            "after-helpers",
            identical_classes=(),
            units_engine=CRATE,
        )

    before, _ = unit_cache.family_content_keys(tmp_path, spec, MINI_FONT)
    before_stamp = stamp()
    (runes / "qsPea.yaml").write_text(REFUSE_RUNE.replace("render thick", "render thin"), encoding="utf-8")
    after, _ = unit_cache.family_content_keys(tmp_path, spec, MINI_FONT)
    assert set(after) == set(before)
    moved = {name for name in before if after[name] != before[name]}
    assert "qsPea" in moved
    closure = spec_load.rune_closure(spec)
    assert all(name == "qsPea" or "qsPea" in closure.get(name, frozenset()) for name in moved)
    assert stamp() == before_stamp


# --- the ink-signature store and the audit's reads -------------------------------------------


def _write_signature_store(out_dir: Path, environment: str, entries: Mapping[str, str]) -> None:
    """Write an ink-signature store the way the crate writes one: a JSON header line naming the format and the stamp, then one `key\\tdigest` line an entry in key order, as one gzip stream."""
    header = json.dumps({"format": unit_cache.SIGNATURE_STORE_FORMAT, "environment": environment})
    body = "".join(f"{key}\t{entries[key]}\n" for key in sorted(entries))
    unit_cache.signature_store_path(out_dir).write_bytes(gzip.compress(f"{header}\n{body}".encode(), mtime=0))


def test_signature_store_round_trip_and_invalidation(tmp_path):
    """`load_signature_store` gives a store's entries under its own stamp and None under another stamp, with no store, or for a store that does not read."""
    entries = {"k2": "d2", "k1": "d1"}
    _write_signature_store(tmp_path, "env-a", entries)
    assert unit_cache.load_signature_store(tmp_path, "env-a") == entries
    assert unit_cache.load_signature_store(tmp_path, "env-b") is None
    assert unit_cache.load_signature_store(tmp_path / "missing", "env-a") is None
    unit_cache.signature_store_path(tmp_path).write_bytes(b"not a gzip stream")
    assert unit_cache.load_signature_store(tmp_path, "env-a") is None


def _identity(path: Path) -> list[int]:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        return unit_cache.audit_identity(descriptor)
    finally:
        os.close(descriptor)


def _signature_stat(corpus: Path) -> tuple[int, int, int]:
    stat = unit_cache.signature_store_path(corpus).stat()
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def _mini_audit_lines() -> tuple[str, list[str]]:
    header, *rows = MINI_AUDIT.read_text(encoding="utf-8").splitlines()
    return header, rows


def _write_audit(path: Path, header: str, rows: Iterable[str]) -> Path:
    """Write an audit with the mini audit's modification time, which the manifest's `generated_at` reads, so a corpus built over it compares with one built over the mini audit."""
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    stat = MINI_AUDIT.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    return path


def _same_units(corpus: Path, expected: Path) -> bool:
    """Whether two corpora built over audits at different paths hold the same fragments by id (`_live`) and the same manifest but for its `source` block, which names the audit's path."""
    manifests = [
        json.loads((path / "manifest.json").read_text(encoding="utf-8")) for path in (corpus, expected)
    ]
    for manifest in manifests:
        manifest.pop("source")
    return _live(corpus) == _live(expected) and manifests[0] == manifests[1]


def _reordered(rows: list[str], shape: str) -> list[str]:
    """The mini audit's rows in an order run_m1 never writes: its `ss03` section ahead of `default`, or two rows of `default` swapped, so the stream meets a window that does not increase partway through."""
    if shape == "sections out of rank order":
        return [row for row in rows if row.startswith("ss03\t")] + [
            row for row in rows if not row.startswith("ss03\t")
        ]
    swapped = list(rows)
    swapped[100], swapped[101] = swapped[101], swapped[100]
    return swapped


def test_the_build_refuses_an_audit_not_in_sorted_sections(mini_bundle, tmp_path):
    """The build reads an audit only as a stream, so an audit run_m1 never writes, here with its sections out of rank order, is refused with a message that says why, rather than built in a form the oracle does not write."""
    header, rows = _mini_audit_lines()
    audit = _write_audit(tmp_path / "audit.tsv", header, _reordered(rows, "sections out of rank order"))
    with pytest.raises(SystemExit, match=r"reads an audit only as a stream, and .* is not one, since "):
        _build(tmp_path / "corpus", mini_bundle, audit_path=audit, jobs=1)


def test_an_audit_replaced_by_rename_after_the_build_opened_it_is_not_read(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """The build holds the descriptors it opened the audit with (`audit.AuditParts`) until it returns, and the build process reads every byte of the audit through them, never by the path, since a `--gates-only` pass can replace the audit by rename during a build. An audit replaced by a shorter one just after the build opened it is read as it was when opened, by the key-only read and the stream."""
    header, rows = _mini_audit_lines()
    audit = _write_audit(tmp_path / "audit.tsv", header, rows)
    replacement = _write_audit(tmp_path / "replacement.tsv", header, rows[:10])
    held = review_build.audit.AuditParts

    def held_then_replaced(path):
        parts = held(path)
        os.replace(replacement, path)
        return parts

    monkeypatch.setattr(review_build.audit, "AuditParts", held_then_replaced)
    corpus = tmp_path / "corpus"
    capfd.readouterr()
    _build(corpus, mini_bundle, audit_path=audit, jobs=1)
    report = capfd.readouterr().err
    assert audit.read_text(encoding="utf-8").count("\n") == 11
    assert "key-only read ran " in report and "; read as a stream;" in report
    assert _same_units(corpus, mini_corpus)


@pytest.mark.parametrize("damage", ["a flipped bit", "rows that break the walk first"])
def test_a_member_that_does_not_inflate_fails_the_build_naming_its_configuration(
    damage, mini_bundle, tmp_path
):
    """An audit whose base a fold wrote as gzip members (#618's change 36) is read by member, and a reader that seeks checks only the parts' sizes, so a member whose bytes are damaged at its recorded size is found as it is inflated, by the key-only read or the stream, whichever reaches it first. Either fails the build with a `MemberError` naming the section's configuration. That holds for damage zlib catches as it inflates, and for damage that inflates to rows that break the walk before the member's end is checked, as damage inside a large member usually does: here a whole gzip member of a row repeated at a window past every other, so only the last window range reaches it, then padding to the member's recorded length."""
    from rebuild.pipeline import oracle

    header, rows = _mini_audit_lines()
    out = tmp_path / "m1"
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(row_delta, "AUDIT_SEGMENT_CAP", 1)
        for lines in (rows[:-1], rows[1:], rows):
            with oracle.AuditWriter(out) as writer:
                writer.feed(("\n".join(lines) + "\n").encode("utf-8"))
    head = row_delta.read_head(out, oracle.AUDIT_NAME)
    assert head is not None and not head.segments and head.base.members is not None
    largest = max(head.base.members, key=lambda member: member.inflated)
    victim = next(member for member in head.base.members[1:] if member != largest)
    offset = sum(member.length for member in head.base.members[: head.base.members.index(victim)])
    base = out / head.base.name
    damaged = bytearray(base.read_bytes())
    if damage == "a flipped bit":
        damaged[offset + victim.length // 2] ^= 0x01
    else:
        fields = next(row for row in rows if row.startswith(f"{victim.section}\t")).split("\t")
        reach = max(row.split("\t")[1].count(":") for row in rows) + 2
        row = "\t".join([victim.section, ":".join(["E650"] * reach), *fields[2:]])
        member = gzip.compress(f"{row}\n{row}\n".encode("utf-8"), compresslevel=1, mtime=0)
        assert len(member) < victim.length
        damaged[offset : offset + victim.length] = member.ljust(victim.length, b"\0")
    base.write_bytes(bytes(damaged))
    with pytest.raises(
        corpus_exec.BuildRunError, match=re.escape(f"the {victim.section!r} section in a member that")
    ):
        _build(tmp_path / "corpus", mini_bundle, audit_path=out / oracle.AUDIT_NAME, jobs=1)


def test_a_pass_that_hits_every_prior_signature_leaves_the_store_alone(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    """When every prior entry was hit and nothing missed, the store a pass would write is the one on disk, so the pass leaves it, its inode and modification time with it, and says so on its units line. That holds on a pass whose key-only read ran, here over a copy of the audit, whose identity differs from the one the store was written over, and the store keeps the stamps it had."""
    corpus = _copy(mini_corpus, tmp_path)
    before = _signature_stat(corpus)
    stamps = unit_cache.read_header(unit_cache.signature_store_path(corpus))
    audit = tmp_path / "audit.tsv"
    shutil.copyfile(MINI_AUDIT, audit)
    capfd.readouterr()
    _build(corpus, mini_bundle, audit_path=audit, jobs=1)
    report = capfd.readouterr().err
    assert "key-only read ran " in report
    assert "ink-signature store left as it is" in report
    assert _signature_stat(corpus) == before
    assert unit_cache.read_header(unit_cache.signature_store_path(corpus)) == stamps


def test_the_store_stamps_skip_the_key_only_read_while_both_hold(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """The store the build writes records the audit's identity and the family keys' digest (`unit_cache.audit_identity`, `unit_cache.family_keys_digest`). A pass over the same audit and family keys skips the key-only read, since no key can then miss; a pass over another audit file, or with a family key moved, runs it."""
    corpus = _copy(mini_corpus, tmp_path)
    header = unit_cache.read_header(unit_cache.signature_store_path(corpus))
    assert header is not None and header["audit_identity"] == _identity(MINI_AUDIT)
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    assert "key-only read skipped, the store's stamps held" in capfd.readouterr().err
    audit = tmp_path / "audit.tsv"
    shutil.copyfile(MINI_AUDIT, audit)
    _build(corpus, mini_bundle, audit_path=audit, jobs=1)
    assert "key-only read ran " in capfd.readouterr().err
    keys = unit_cache.family_content_keys

    def moved(repo_root, spec, after_font):
        found, helpers = keys(repo_root, spec, after_font)
        return {**found, "qsRoe": "0" * 64}, helpers

    monkeypatch.setattr(unit_cache, "family_content_keys", moved)
    _build(corpus, mini_bundle, jobs=1)
    assert "key-only read ran " in capfd.readouterr().err


def test_a_wrong_skip_shapes_the_miss_in_the_stream_and_writes_the_store(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    """A store whose stamps hold but which lacks an entry, which the stamps say cannot happen, costs time and never a byte: the pass skips the key-only read, the stream meets the missing key and shapes it there, and the store is written with it again, so the corpus and the store's entries come out as they were."""
    corpus = _copy(mini_corpus, tmp_path)
    path = unit_cache.signature_store_path(corpus)
    header, _, body = gzip.decompress(path.read_bytes()).partition(b"\n")
    lines = body.splitlines(keepends=True)
    path.write_bytes(gzip.compress(header + b"\n" + b"".join(lines[1:]), mtime=0))
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    report = capfd.readouterr().err
    assert "key-only read skipped" in report
    assert re.search(r"signatures: [\d,]+ cached, 1 shaped serially\)", report), report
    assert "ink-signature store written" in report
    assert _signature_lines(path.read_bytes()) == _signature_lines(
        unit_cache.signature_store_path(mini_corpus).read_bytes()
    )
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


def test_the_cluster_a_recomputed_unit_carries_keys_on_its_final_class(mini_corpus):
    """The build computes a unit's cluster where it assigns the change group, so the cluster of an UNMATCHED or re-routed unit must be keyed on that group (the class its fragment is sharded under) and a ledger-classed unit's on its ledger class. The diffs behind the id are not kept, so the test checks the store instead: every unit's record, machine-approved ones included, carries a cluster, and a full fragment's cluster matches its record. A cached unit's cluster is taken from the record, which is sound only if the recomputation keyed on the same class the store records for the unit."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    store = unit_cache.load_store(mini_corpus, _stamps(mini_corpus)["environment"])
    assert store
    by_id = {record["id"]: record for record in store.values()}
    seen_group = False
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            for fragment in json.loads((mini_corpus / part).read_text(encoding="utf-8")):
                record = by_id[fragment["id"]]
                assert record["cluster"].startswith("c-")
                if not slim_fragment(fragment):
                    assert fragment["cluster"] == record["cluster"]
                assert record["class"] == meta["id"]
                if record["change_group"]:
                    seen_group = True
                    assert meta["id"] == record["change_group"]
    assert seen_group


def test_an_absent_manifest_hashes_to_a_sentinel_rather_than_raising(tmp_path):
    """The store's stamp is the identity of the manifest beside it, and the corpus may not have a manifest yet (a first build, or a crash between the two writes). The sentinel turns that into a stamp mismatch and a full rebuild instead of an exception out of `load_store`. The test checks both kinds of unreadable path, a missing file and a directory, so the behavior does not depend on which exception a particular read raises."""
    assert unit_cache._manifest_stamp(tmp_path) == "missing"
    assert unit_cache._sha256_file(tmp_path / "manifest.json") == "missing"
    assert unit_cache._sha256_file(tmp_path) == "missing"
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert unit_cache._manifest_stamp(tmp_path) != "missing"
    assert unit_cache._sha256_file(tmp_path / "manifest.json") != "missing"


# --- the store's records -------------------------------------------------------------------------


def _record(key: str, *, address: list | None = None, **fields) -> dict:
    """A unit store record in the field order the crate writes one, `key` first and `address` last, where `unit_index.record_address` slices it. `unit_index.record_id` slices the id at the offset a 64-hex input key puts it, so a test that reads ids back passes such a key."""
    return {
        "key": key,
        "id": unit_cache.unit_id_for(hashlib.sha256(key.encode()).hexdigest()),
        "class": "split-by-space-or-zwnj",
        "content_key": "f" * 64,
        "slim": False,
        **fields,
        "address": address,
    }


def _record_line(record: dict) -> bytes:
    return (json.dumps(record) + "\n").encode()


def test_store_round_trip_and_invalidation(tmp_path):
    """A store written whole (`write_store`) loads under its own stamp as its records keyed by input key, parsed whole, and as None under another stamp or once the manifest beside it moves, since the store names the manifest it describes."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    first, slim = _record("k1"), _record("k2", slim=True)
    unit_cache.write_store(tmp_path, "env-a", [_record_line(first)], parts=())
    assert unit_cache.load_store(tmp_path, "env-a") == {"k1": first}
    unit_cache.write_store(tmp_path, "env-a", [_record_line(first), _record_line(slim)], parts=())
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k2"]["slim"] is True and loaded["k1"]["slim"] is False
    assert unit_cache.load_store(tmp_path, "env-b") is None
    (tmp_path / "manifest.json").write_text('{"changed": true}', encoding="utf-8")
    assert unit_cache.load_store(tmp_path, "env-a") is None


def test_a_store_reads_back_its_lines_and_each_records_address(tmp_path):
    """The store's lines, read back in its materialized order (`materialized_store`), are the lines written for its records, in order, and each loads as the record it was written from. A deflated fragment's record (#618's change 35) writes its inflated length as its address's fourth element (decision 76), and only it does: a plain fragment's address keeps three elements (decision 77). The line's slicer (`unit_index.record_address`) reads both, the inflated length 0 for a plain fragment, and `unit_index.record_id` the id, without parsing the line."""
    held = tmp_path / "held"
    (held / "units").mkdir(parents=True)
    (held / "units" / "small.json").write_bytes(b"[" + b" " * 30 + b"]")
    (held / "manifest.json").write_text("{}", encoding="utf-8")
    plain_key, deflated_key = (hashlib.sha256(name.encode()).hexdigest() for name in ("plain", "deflated"))
    plain = _record(plain_key, address=["units/small.json", 1, 5])
    deflated = _record(deflated_key, address=["units/small.json", 7, 5, 12])
    lines = [_record_line(record) for record in (plain, deflated)]
    assert lines[0].endswith(b', "address": ["units/small.json", 1, 5]}\n')
    assert lines[1].endswith(b', "address": ["units/small.json", 7, 5, 12]}\n')
    assert [unit_index.record_address(line) for line in lines] == [
        ("units/small.json", 1, 5, 0),
        ("units/small.json", 7, 5, 12),
    ]
    assert [unit_index.record_id(line) for line in lines] == [plain["id"], deflated["id"]]
    unit_cache.write_store(held, "env-a", lines, parts=["units/small.json"])
    assert _store_lines(held)[1] == lines
    assert _stamps(held)["parts"] == {"units/small.json": 32}
    assert unit_cache.load_store(held, "env-a") == {plain_key: plain, deflated_key: deflated}


def test_an_assets_refresh_leaves_the_store_loadable(tmp_path):
    """The store is stamped with the manifest's identity, not its bytes, so rewriting `inputs_fingerprint.static` over a served corpus (all an assets refresh does) leaves the store valid for the shards beside it. Any other change to the manifest, such as a new `generated_at`, invalidates the store and costs the next build a full pass."""
    manifest = {"generated_at": "2026-01-01T00:00:00Z", "inputs_fingerprint": {"data": "d", "static": "s"}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    unit_cache.write_store(tmp_path, "env-a", [_record_line(_record("k1"))], parts=())
    refreshed = {**manifest, "inputs_fingerprint": {"data": "d", "static": "refreshed"}}
    (tmp_path / "manifest.json").write_text(json.dumps(refreshed, indent=2), encoding="utf-8")
    assert unit_cache.load_store(tmp_path, "env-a") is not None
    (tmp_path / "manifest.json").write_text(
        json.dumps({**refreshed, "generated_at": "2099-01-01T00:00:00Z"}), encoding="utf-8"
    )
    assert unit_cache.load_store(tmp_path, "env-a") is None


# --- the content-addressed ids --------------------------------------------------------------------


def test_base58_64_spells_the_first_64_bits_in_eleven_fixed_symbols():
    """The id alphabet and width: Bitcoin's base58 (no 0, O, I, or l), eleven symbols for 64 bits, most significant first, zero-padded with the alphabet's first symbol, with the bits taken from the front of the digest, so the id depends only on the digest's first 16 hex digits."""
    assert len(unit_cache.BASE58_ALPHABET) == 58
    assert not set("0OIl") & set(unit_cache.BASE58_ALPHABET)
    assert 58**10 < 2**64 <= 58**11
    assert unit_cache.base58_64("0" * 64) == "1" * 11
    assert unit_cache.base58_64("0000000000000001" + "f" * 48) == "1" * 10 + "2"
    assert unit_cache.base58_64("0000000000000039" + "0" * 48) == "1" * 10 + "z"
    assert unit_cache.base58_64("000000000000003a" + "0" * 48) == "1" * 9 + "21"
    assert unit_cache.base58_64("f" * 64) == "jpXCZedGfVQ"
    digest = hashlib.sha256(b"a window").hexdigest()
    assert unit_cache.base58_64(digest) == unit_cache.base58_64(digest[:16] + "0" * 48)
    assert unit_cache.base58_64(digest) != unit_cache.base58_64("0" * 16 + digest[16:])


def test_unit_and_duplicate_group_ids_carry_the_prefix_and_the_shape():
    """A unit's id is `u-` and the first 64 bits of its content key in base58, and a duplicate group's `e-` and the same shape, so both read as content ids. `id_word` gives a unit id's 64-bit word back, and nothing for a string of another shape."""
    key = hashlib.sha256(b"a window").hexdigest()
    unit_id = unit_cache.unit_id_for(key)
    assert unit_id == "u-" + unit_cache.base58_64(key)
    assert unit_cache.is_content_id(unit_id)
    assert unit_cache.id_word(unit_id) == int(key[:16], 16)
    duplicate_group = "e-" + unit_cache.base58_64(hashlib.sha256(b"a group").hexdigest())
    assert unit_cache.is_content_id(duplicate_group) and unit_cache.id_word(duplicate_group) is None
    for bad in ("u-0000", "u-3mJ7kPq2Xw", "u-3mJ7kPq2Xw9Z", "u-0O0O0O0O0O0", "3mJ7kPq2Xw9", None, 7):
        assert not unit_cache.is_content_id(bad), bad


def test_a_units_id_is_its_stamps_and_moves_only_with_its_content(mini_corpus):
    """Over the whole mini corpus, every fragment's id is `unit_id_for` of its `content_key`, the key is the hash of the fragment's carry projection, no fragment carries a batch, and no two fragments share an id. So a verdict recorded against an id follows the unit's content across corpora, whatever the unit's position."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    seen: set[str] = set()
    slim_ids: set[str] = set()
    for meta in manifest["classes"]:
        for fragment in _class_fragments(mini_corpus, meta["id"]):
            assert fragment["content_key"] == unit_cache.carry_content_hash(fragment)
            assert fragment["id"] == unit_cache.unit_id_for(fragment["content_key"])
            assert "batch" not in fragment
            assert fragment["id"] not in seen
            seen.add(fragment["id"])
            if slim_fragment(fragment):
                slim_ids.add(fragment["id"])
    assert seen == set(manifest["human_unit_ids"]) | slim_ids
    assert not slim_ids & set(manifest["human_unit_ids"])


def _rewritten(counts: Path) -> list[str]:
    return [line for line in counts.read_text().splitlines() if line.startswith(output_sink.REWRITTEN)]


def test_the_units_index_takes_each_route_from_its_head_and_materializes_to_the_file_a_whole_write_gives(
    tmp_path, monkeypatch
):
    """The units index takes the segment format's routes, each decided from its prior head before anything of it is written (#618's change 32, decisions 72 to 75), and every write leaves a materialized index that is the file a whole write of the same records gives, byte for byte. The single file a corpus from before the segment format holds is linked as generation 0's base, and unlinked once the write's head over the link is in place. A write that changes some records writes one segment of them, and one that changes none writes the head alone. With the cap lowered to two segments, a write whose fold is due and that holds the pass's one fold writes this pass's records whole as the next generation's base, moving the head's content generation only when a record moved; a write whose fold another output holds this pass writes its segment and waits, until it has waited `row_delta.FOLD_OVERRUN` segments past its cap, where it folds anyway; and with the cap at 16, a segment as large as its base makes the next write fold. A head that does not read, or a part whose sha256 is not the one its head records, is an `unusable` base, counted as rewritten whole."""
    from rebuild.tools import cycle_paths

    directory = tmp_path / "index"
    directory.mkdir()
    counts = tmp_path / "counts.tsv"
    counts.write_text("")
    monkeypatch.setenv(output_sink.OUTPUT_COUNTS_ENV, str(counts))
    monkeypatch.setattr(row_delta, "SEGMENT_CAP", 2)
    units = {
        f"u-{index:02d}": {"id": f"u-{index:02d}", "class": "c-a" if index % 3 else "c-b"}
        for index in range(12)
    }
    passes: list[unit_index.IndexWrite] = []

    def write(
        changed: Iterable[str] = (), *, notation: str = "", held_by: str | None = None
    ) -> row_delta.Head:
        number = len(passes) + 1
        monkeypatch.setattr(cycle_paths, "CYCLE_VAR", tmp_path / f"pass-{number}")
        if held_by is not None:
            assert row_delta.claim_fold(held_by)
        for unit_id in changed:
            units[unit_id] = {**units[unit_id], "notation": f"{notation or 'pass'} {number}"}
        shards: dict[str, list[dict]] = {}
        for unit in units.values():
            shards.setdefault(unit["class"], []).append(unit)
        manifest = {"batch_size": 4, "human_unit_ids": [unit_id for unit_id in units if int(unit_id[2:]) % 2]}
        for target in (directory, tmp_path / f"whole-{number}"):
            target.mkdir(exist_ok=True)
            (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        passes.append(unit_index.write_index(directory, shards.items()))
        unit_index.write_index(tmp_path / f"whole-{number}", shards.items())
        materialized = unit_index.materialized_index(directory, sha256=True)
        assert materialized is not None
        form = tmp_path / f"form-{number}"
        form.mkdir()
        assert (
            unit_index.write_materialized_index(form, *materialized).read_bytes()
            == (tmp_path / f"whole-{number}" / row_delta.base_name(unit_index.INDEX_NAME, 0)).read_bytes()
        )
        head = row_delta.read_head(directory, unit_index.INDEX_NAME)
        assert head is not None
        return head

    write()
    single = unit_index.index_path(directory)
    materialized = unit_index.materialized_index(directory)
    assert materialized is not None
    unit_index.write_materialized_index(directory, materialized[0], list(materialized[1]))
    row_delta.head_path(directory, unit_index.INDEX_NAME).unlink()
    row_delta.remove_unlisted(directory, unit_index.INDEX_NAME, None)
    inode = single.stat().st_ino
    head = write()
    assert passes[-1] == unit_index.IndexWrite("head") and not single.exists()
    assert (head.generation, head.content, head.segments) == (0, 0, ())
    assert (directory / head.base.name).stat().st_ino == inode
    head = write(["u-03"])
    assert (passes[-1].outcome, passes[-1].rows) == ("segment", 1) and (head.content, len(head.segments)) == (
        1,
        1,
    )
    head = write()
    assert passes[-1] == unit_index.IndexWrite("head") and head.content == 1
    head = write(["u-05", "u-06"])
    assert (passes[-1].rows, len(head.segments)) == (2, 2) and row_delta.fold_due(head)
    while not row_delta.fold_forced(head):
        head = write(["u-07"], held_by="windows-default.tsv.gz")
        assert passes[-1].outcome == "segment"
    assert len(head.segments) == row_delta.SEGMENT_CAP + row_delta.FOLD_OVERRUN
    content = head.content
    head = write(["u-08"], held_by="windows-default.tsv.gz")
    assert (passes[-1].outcome, passes[-1].reason, passes[-1].rows) == ("base", "fold", len(units))
    assert (head.generation, head.content, head.segments) == (1, content + 1, ())
    head = write(["u-09"])
    head = write(["u-10"])
    content = head.content
    head = write()
    assert (passes[-1].outcome, passes[-1].reason) == ("base", "fold")
    assert (head.generation, head.content, head.segments) == (2, content, ())
    monkeypatch.setattr(row_delta, "SEGMENT_CAP", 16)
    head = write(
        list(units), notation="a notation long enough to make every record outweigh its line in the base"
    )
    assert passes[-1].rows == len(units) and row_delta.fold_due(head) and len(head.segments) == 1
    head = write()
    assert (passes[-1].outcome, passes[-1].reason, head.generation) == ("base", "fold", 3)
    assert not _rewritten(counts)
    base = directory / head.base.name
    damaged = bytearray(base.read_bytes())
    damaged[-9] ^= 1
    base.write_bytes(bytes(damaged))
    head = write()
    assert (passes[-1].outcome, passes[-1].reason, head.generation) == ("base", "unusable", 4)
    row_delta.head_path(directory, unit_index.INDEX_NAME).write_text("{ not a head")
    head = write()
    assert (passes[-1].outcome, passes[-1].reason, head.generation) == ("base", "unusable", 0)
    assert _rewritten(counts) == [f"{output_sink.REWRITTEN}\t{unit_index.INDEX_NAME}"] * 2
    assert sorted(path.name for path in directory.glob(f"{unit_index.INDEX_NAME}*")) == [
        row_delta.base_name(unit_index.INDEX_NAME, 0),
        row_delta.head_name(unit_index.INDEX_NAME),
    ]


def test_a_build_over_a_single_file_store_puts_it_under_a_head(
    mini_corpus, mini_bundle, edited_scratch, tmp_path
):
    """A store written before the segment format, the single file `unit-cache.ndjson.gz` with no head, is read as a base with no head and put under a head before the write appends to any shard part, so a build over it that appends completes the corpus into the from-scratch build's materialized form and removes the single file."""
    audits, scratches = edited_scratch
    corpus = _copy(mini_corpus, tmp_path)
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    assert head is not None and not head.segments
    single = unit_cache.store_path(corpus)
    shutil.copyfile(corpus / head.base.name, single)
    row_delta.head_path(corpus, unit_cache.STORE_NAME).unlink()
    row_delta.remove_unlisted(corpus, unit_cache.STORE_NAME, None)
    assert row_delta.read_head(corpus, unit_cache.STORE_NAME) is None
    _build(corpus, mini_bundle, audit_path=audits[0], jobs=1)
    assert _overflow_parts(corpus) == [f"{RETAG_CLASS}.x000.json"]
    assert row_delta.read_head(corpus, unit_cache.STORE_NAME) is not None
    assert not single.exists()
    assert _live(corpus) == _live(scratches[0])
    assert _form(corpus, tmp_path) == _form(scratches[0], tmp_path)


def test_a_stamp_move_that_moves_no_fragment_writes_no_shard_byte(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """A stamp line that moves without re-deriving every record, as the corpus code line moves when the build's code changes and its outputs do not, recomputes every unit, and each comes out byte for byte its prior (#618's decisions 72 and 73): the write finds each unit's prior fragment by id through the prior store's records, compares it with the new one and keeps it where it is. So every shard part keeps its inode and modification time, no overflow part is written, and the store, whose records all come out the same, writes its head alone, under the new stamp."""
    corpus = _copy(mini_corpus, tmp_path)
    before = {path.name: (path.stat().st_ino, path.stat().st_mtime_ns) for path in _shard_paths(corpus)}
    real = unit_cache.environment_stamp

    def moved(*args, **kwargs):
        stamp = real(*args, **kwargs)
        return fingerprint.EnvironmentStamp((*stamp.lines, "moved\tthis pass"), stamp.detail)

    monkeypatch.setattr(unit_cache, "environment_stamp", moved)
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert cached == 0
    assert (
        f"appended 0.0 MB (0.0 MB inflated), kept {total:,} by compare, folded 0 classes, wrote 0 directly, "
        "deflated 0 fragments, dead 0.0 MB"
    ) in report
    assert {
        path.name: (path.stat().st_ino, path.stat().st_mtime_ns) for path in _shard_paths(corpus)
    } == before
    assert _overflow_parts(corpus) == []
    assert "(store: head only)" in report
    assert "moved\tthis pass" in _stamps(corpus)["environment_lines"]
    head = row_delta.head_name(unit_cache.STORE_NAME)
    assert {name: data for name, data in _tree(corpus).items() if name != head} == {
        name: data for name, data in _tree(mini_corpus).items() if name != head
    }


def test_a_class_whose_dead_bytes_reach_its_live_bytes_is_folded_deflated_into_a_from_scratch_layout(
    mini_corpus, mini_bundle, edited_scratch, tmp_path, capfd, monkeypatch
):
    """A class whose dead bytes reach its live bytes on disk as the pass opens is folded: its live fragments rewritten in id order into base parts cut and named by the rule a from-scratch build follows (`unit_index.PartLayout`, `unit_index.base_part_names`), over their bytes on disk, its overflow parts left unlisted and pruned. A fold writes deflated (#618's change 35): a fragment the class held deflated is copied in as it was stored, never deflated twice, and a plain one is deflated (`_deflated`). Here `RETAG_CLASS` holds two retagged units appended deflated beside its plain base part, and copies of that part as overflow parts no record names, so its dead bytes reach its live bytes (`_fold_due`); the pass that folds it moves one retagged unit back out. The cap (`unit_index.SHARD_PART_BYTES`, which the build input's vocabulary carries to the crate) is lowered below the class's deflated bytes, so the fold crosses from the bare name to numbered parts. The files on disk differ from a from-scratch build's (decision 48), but the corpus holds that build's fragments by id and materializes to its files byte for byte."""
    audits, scratches = edited_scratch
    corpus = _copy(mini_corpus, tmp_path)
    _build(corpus, mini_bundle, audit_path=audits[1], jobs=1)
    with unit_index.LiveCorpus(corpus) as live:
        before = {
            slot.unit_id: (slot, live.read(slot)) for slot in live.slots(_class_meta(corpus, RETAG_CLASS))
        }
    assert sum(1 for slot, _plain in before.values() if slot.inflated) == 2
    deflated = {
        unit_id: (
            (corpus / slot.part).read_bytes()[slot.start : slot.start + slot.length]
            if slot.inflated
            else _deflated(plain)
        )
        for unit_id, (slot, plain) in before.items()
    }
    cap = max(max(map(len, deflated.values())) + 8, sum(len(body) + 1 for body in deflated.values()) * 2 // 3)
    _fold_due(corpus, RETAG_CLASS)
    monkeypatch.setattr(unit_index, "SHARD_PART_BYTES", cap)
    capfd.readouterr()
    _build(corpus, mini_bundle, audit_path=audits[0], jobs=1)
    report = capfd.readouterr().err
    assert "folded 1 classes" in report
    folded = _class_meta(corpus, RETAG_CLASS)["shards"]
    layout = unit_index.PartLayout(cap)
    with unit_index.LiveCorpus(corpus) as live:
        slots = live.slots(_class_meta(corpus, RETAG_CLASS))
    for slot in slots:
        assert slot.inflated == len(before[slot.unit_id][1])
        assert (corpus / slot.part).read_bytes()[slot.start : slot.start + slot.length] == deflated[
            slot.unit_id
        ]
        index, start, _first = layout.place(slot.length)
        assert (slot.part, slot.start) == (folded[index], start)
    sizes = layout.sizes()
    assert folded == unit_index.base_part_names(RETAG_CLASS, len(sizes)) and len(folded) > 1
    assert [(corpus / part).stat().st_size for part in folded] == sizes
    assert len(set(before) - {slot.unit_id for slot in slots}) == 1 and len(slots) == len(before) - 1
    assert not list((corpus / "units").glob(f"{RETAG_CLASS}.x*.json"))
    assert {slot.unit_id for slot in slots} <= _held_deflated(corpus)
    assert _live(corpus) == _live(scratches[0])
    assert _form(corpus, tmp_path) == _form(scratches[0], tmp_path)


def test_a_class_one_of_whose_parts_is_gone_is_written_plain_as_a_first_base(
    deflated_corpus, mini_bundle, tmp_path, capfd
):
    """A class one of whose parts is gone from disk is written directly as a first base, plain throughout, as a from-scratch build writes it (#618's change 35): a cached fragment a surviving overflow part holds deflated is read, its inflated bytes checked, and copied in plain, and the units whose part is gone are recomputed. Its part is the from-scratch build's byte for byte, its records and rows carry no marker, and every other class keeps the form it had."""
    corpus = _copy(deflated_corpus[0], tmp_path)
    scratch = deflated_corpus[1]
    base, overflow = unit_index.class_shards(_class_meta(corpus, DEFLATED_FROM))
    with unit_index.LiveCorpus(corpus) as live:
        moved = {
            slot.unit_id for slot in live.slots(_class_meta(corpus, DEFLATED_FROM)) if slot.part == overflow
        }
    held = _held_deflated(corpus)
    assert moved and moved <= held
    (corpus / base).unlink()
    capfd.readouterr()
    _build(corpus, mini_bundle, audit_path=deflated_corpus[2], jobs=1)
    assert "folded 0 classes, wrote 1 directly, deflated 0 fragments" in capfd.readouterr().err
    assert unit_index.class_shards(_class_meta(corpus, DEFLATED_FROM)) == [base]
    assert unit_index.class_shards(_class_meta(scratch, DEFLATED_FROM)) == [base]
    assert (corpus / base).read_bytes() == (scratch / base).read_bytes()
    assert not (corpus / overflow).exists()
    assert _held_deflated(corpus) == held - moved
    assert _live(corpus) == _live(scratch)
    assert _form(corpus, tmp_path) == _form(scratch, tmp_path)
