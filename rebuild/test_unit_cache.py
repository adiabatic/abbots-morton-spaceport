"""Tests for the persisted per-unit corpus cache, whose contract the docstring of rebuild/review/unit_cache.py states. The tests check that an incremental rebuild over an edited audit writes the same bytes as a from-scratch build of the same inputs, the store included. They also check that a no-change rebuild reuses every unit, that a corrupt or bypassed store falls back to a full build instead of reusing stale bytes, that a cold build ships each unit's per-config class map in the order the audit lists its configs, that neither a cold nor a cached build writes through a pooled map, and that the serial and parallel paths agree.

None of these properties depends on a glyph, so the workload is the frozen mini-M1 bundle under rebuild/review/fixtures/mini/ (real windows, their subset-table slices, and the after font they were extracted with) instead of the live build. That keeps the module in the contracts lane, with each build taking seconds. `fixtures/mini/regenerate.py` refreshes the bundle. The content-key and cluster-id tests further down use synthetic inputs.
"""

import filecmp
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import threading
from array import array
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import fingerprint, fixtures, kernel_exec, row_delta, spec_load
from rebuild.review import app_index, audit_stream, landing, unit_cache, unit_index
from rebuild.review import build as review_build
from rebuild.review import enrich as review_enrich
from rebuild.review.audit import (
    SLIM_OMITTED_KEYS,
    AuditRow,
    RowColumns,
    UnitTable,
    _config_index,
    load_table,
    merge_ink_duplicate_units,
    slim_fragment,
)
from rebuild.review.build import (
    SITE_BEFORE_FONT,
    SITE_JUNIOR_FONT,
    _cluster_id,
    _cluster_id_from_repr,
    _write_shard,
    build_m1,
)
from rebuild.review.unit_store import UnitStore

REPO_ROOT = Path(__file__).resolve().parent.parent
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"
MINI_AUDIT = MINI / "audit.tsv"
MINI_FONT = MINI / "M1.otf"


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
    """An ink-signature store's bytes decompressed, with its header line re-serialized without the two stamps that say which audit and family keys it was written over (`unit_cache.AUDIT_IDENTITY_KEY`, `unit_cache.FAMILY_KEYS_KEY`), as `perf_fingerprint` projects them: a build that leaves the store as it is keeps the stamps of the build that wrote it, so they record the store's history and not its entries."""
    header, _, lines = gzip.decompress(data).partition(b"\n")
    fields = json.loads(header)
    for key in (unit_cache.AUDIT_IDENTITY_KEY, unit_cache.FAMILY_KEYS_KEY):
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
    """Give each class in `class_ids` enough dead bytes that the next build folds it (`build._route`, #618's decision 53): copies of its first part as overflow parts the manifest lists after its own and no store record names, until its dead bytes reach its live bytes. The manifest and the store are rewritten over the new parts."""
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
                name = unit_index.overflow_part_name(meta["id"], number)
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
    """A rebuild over unchanged inputs reuses every unit, keeps every fragment at its address without rewriting any shard part (the parts' mtimes do not change) or appending any, projects every sidecar row from the previous sidecars without parsing a fragment, and writes the store's head alone, since no record changed. The corpus it leaves is byte for byte the one it found. That holds over the mini corpus, all plain, and over one that holds folded classes and an append deflated (`deflated_corpus`, #618's change 35), whose deflated fragments keep their bytes and whose records and copied-forward rows keep their marker."""
    if held == "plain":
        corpus, audit = _copy(mini_corpus, tmp_path), MINI_AUDIT
    else:
        corpus, audit = _copy(deflated_corpus[0], tmp_path), deflated_corpus[2]
        assert _held_deflated(corpus)
    before = _tree(corpus)
    mtimes = _part_mtimes(corpus)
    capfd.readouterr()
    _build(corpus, mini_bundle, audit_path=audit, jobs=1)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert cached == total
    assert MISS_NOTE.search(report) is None, report
    byte_copied, written, copied_forward = _counts(report, BYTE_COPIED)
    assert (byte_copied, written, copied_forward) == (total, total, total)
    assert "(store: head only)" in report
    assert (
        "appended 0.0 MB (0.0 MB inflated), kept 0 by compare, folded 0 classes, wrote 0 directly, "
        "deflated 0 fragments, dead 0.0 MB"
    ) in report
    assert _part_mtimes(corpus) == mtimes
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


def _out_of_order_audit(tmp_path: Path) -> tuple[Path, dict[str, list[str]]]:
    """Return the mini audit with two windows split per config. The ss03 row of each is retagged to a ledger class, so each window is blessed under ss03 and novel under its other configs. The second window's ss03 row is moved to the top of the file, so its class map lists ss03 first while the first window's lists its configs in config order. Returns the edited file and, for the in-order window and then the out-of-order one, the configs in the order the edited file lists them.

    The in-order window is the first in the file whose rows all have one (codepoints, baseline, new) triple and are all UNMATCHED, under configs that include default and ss03. The out-of-order window is the next such window with the same configs. A single triple means the ink-duplicate merge does not reach them. The rows left UNMATCHED keep each unit's class UNMATCHED, and since default is among them, neither the set group the review facts record nor the group phase 1 assigns changes. The mini audit is written one config block at a time, so every class map in it is in config order; the moved row is what gives the build a map in another order.
    """
    lines = MINI_AUDIT.read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], [line.split("\t") for line in lines[1:]]
    windows: dict[str, tuple[set[tuple[str, str]], set[str], list[str]]] = {}
    for fields in rows:
        triples, entries, configs = windows.setdefault(fields[1], (set(), set(), []))
        triples.add((fields[4], fields[5]))
        entries.add(fields[3])
        configs.append(fields[0])
    candidates = [
        window
        for window, (triples, entries, configs) in windows.items()
        if len(triples) == 1 and entries == {"UNMATCHED"} and {"default", "ss03"} <= set(configs)
    ]
    assert candidates, "the mini audit states no one-triple, all-UNMATCHED window under default and ss03"
    in_order = candidates[0]
    out_of_order = next(
        (window for window in candidates[1:] if sorted(windows[window][2]) == sorted(windows[in_order][2])),
        None,
    )
    assert out_of_order is not None, f"no second one-triple, all-UNMATCHED window states {in_order}'s configs"
    for fields in rows:
        if fields[1] in (in_order, out_of_order) and fields[0] == "ss03":
            fields[3] = RETAG_CLASS
    moved = next(fields for fields in rows if fields[1] == out_of_order and fields[0] == "ss03")
    edited = [moved] + [fields for fields in rows if fields is not moved]
    path = tmp_path / "audit-out-of-order.tsv"
    path.write_text("\n".join([header] + ["\t".join(fields) for fields in edited]) + "\n", encoding="utf-8")
    return path, {
        window: [fields[0] for fields in edited if fields[1] == window] for window in (in_order, out_of_order)
    }


@pytest.fixture(scope="module")
def edited_scratch(build_once, mini_bundle) -> tuple[list[Path], list[Path]]:
    """Return two edited mini audits (`_edited_audit`, with one window retagged and then two) and a from-scratch build of each, built once a run (`build_once`) for every case of the incremental-rebuild test, which only read them."""

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


@pytest.mark.parametrize(("seed", "cloning"), [("copy", True), ("clone", True), ("copy", False)])
def test_incremental_rebuild_matches_a_from_scratch_build_after_an_edit(
    mini_corpus, mini_bundle, edited_scratch, tmp_path, capfd, monkeypatch, seed, cloning
):
    """After an audit edit that drops one window and retags another, an incremental rebuild reuses all but at most two units, leaves every shard part it found in place, appends the retagged unit's fragment to its new class's first overflow part, deflated (#618's change 35), with its store record's fourth address element and its row's marker (`_held_deflated`), leaves its old slot dead, and writes the store as a segment. The files on disk differ from a from-scratch build's (decision 48), but read through the live-fragment iterator, which inflates, its fragments are that build's by id, and materialized (`build.materialize_corpus`) the two corpora are byte for byte the same files, store included. A second edit that retags one more window into the same class appends to that overflow part, beside the deflated fragment it holds, which the shard writer clones to its staging name (`build._clone`), or copies where the volume cannot clone, and cuts before its closing bytes, to the same bytes. The same holds for a build seeded as the artifact cycle seeds it beside the served corpus, a copy-on-write clone (`landing.clone_tree`), and that build leaves the tree it was cloned from byte for byte as it was. Every case compares against the from-scratch builds `edited_scratch` makes."""
    audits, scratches = edited_scratch
    clones: list[bool] = []
    clone = review_build._clone

    def spy(source: Path, target: Path) -> bool:
        made = cloning and clone(source, target)
        clones.append(made)
        return made

    monkeypatch.setattr(review_build, "_clone", spy)
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
    assert _overflow_parts(incremental) == [f"{RETAG_CLASS}.x000.json"] and clones == []
    assert "(store: a segment of " in report
    appended = _held_deflated(incremental)
    with unit_index.LiveCorpus(incremental) as live:
        overflow = [
            slot.unit_id for slot in live.slots(_class_meta(incremental, RETAG_CLASS)) if "x000" in slot.part
        ]
    assert len(appended) == 1 and appended == set(overflow)
    assert _live(incremental) == _live(scratches[0])
    assert _form(incremental, tmp_path) == _form(scratches[0], tmp_path)
    _build(incremental, mini_bundle, audit_path=audits[1], jobs=1)
    assert _overflow_parts(incremental) == [f"{RETAG_CLASS}.x000.json"] and clones == [cloning]
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


def _fragment_of(corpus: Path, codepoints: str) -> dict:
    """Return the one fragment a corpus ships for a window, searching every class the manifest lists, because the build shards an UNMATCHED or re-routed unit under the change group it promotes the unit to."""
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    (fragment,) = [
        fragment
        for meta in manifest["classes"]
        for fragment in _class_fragments(corpus, meta["id"])
        if fragment["codepoints"] == codepoints
    ]
    return fragment


PoolSnapshot = list[tuple[Mapping[str, str], tuple[tuple[str, str], ...]]]


def _pooled_maps(monkeypatch) -> list[tuple[UnitTable, PoolSnapshot]]:
    """Record each workload table a build loads, with the instance and the items of every class map its pool holds at load, so a test can check after the build that the pool still holds what the loader wrote."""
    captured: list[tuple[UnitTable, PoolSnapshot]] = []
    real = review_build.load_workload

    def spy(*args, **kwargs):
        workload = real(*args, **kwargs)
        pool = workload.table.mappings
        snapshot = [(pool[index], tuple(pool[index].items())) for index in range(len(pool) + 1)]
        captured.append((workload.table, snapshot))
        return workload

    monkeypatch.setattr(review_build, "load_workload", spy)
    return captured


def _assert_no_pooled_map_was_written(table: UnitTable, snapshot: PoolSnapshot) -> None:
    """Check that every class map pooled at load is still the instance at its id and still has the items it was pooled with, and that every map the pool holds now, including those the merge added, is filed under its own items."""
    pool = table.mappings
    for index, (instance, items) in enumerate(snapshot):
        assert pool[index] is instance, index
        assert tuple(instance.items()) == items, index
    assert [pool.id(pool[index]) for index in range(len(pool) + 1)] == list(range(len(pool) + 1))


def test_a_cold_build_ships_a_class_map_in_the_order_its_audit_states_it_and_no_build_writes_through_a_pooled_one(
    mini_bundle, tmp_path, monkeypatch, capfd
):
    """A cold build ships a per-config class map in the order its audit lists the configs. That order is part of the shard bytes, beside a `configs` list that is always in config order, while the content key and the id hash the map without regard to order, so a pool keyed on sorted items would ship two maps with equal contents in whichever order it saw first. Neither the cold build nor a cached rebuild over the same audit writes through a pooled map. Both run at one job, so phase 1 runs in this process, and a write through a pooled map would change the instance the test holds, not a worker's pickled copy. The cached rebuild reuses every unit and is byte-identical to the cold build. Because it copies every fragment as it is, it checks that the pool is unwritten and the bytes are stable; it does not check the order a rebuild ships after an audit edit that lists a window's configs in another order."""
    audit, stated = _out_of_order_audit(tmp_path)
    in_order, out_of_order = stated
    captured = _pooled_maps(monkeypatch)
    cold = tmp_path / "cold"
    _build(cold, mini_bundle, audit_path=audit, jobs=1)
    assert len(captured) == 1
    _assert_no_pooled_map_was_written(*captured[0])
    first = _fragment_of(cold, in_order)
    second = _fragment_of(cold, out_of_order)
    assert list(first["config_classes"]) == stated[in_order]
    assert list(second["config_classes"]) == stated[out_of_order]
    assert first["config_classes"] == second["config_classes"]
    assert stated[in_order] != stated[out_of_order]
    assert first["configs"] == second["configs"] == sorted(stated[in_order], key=_config_index)
    note = f"blessed as {RETAG_CLASS} under ss03; novel under " + ", ".join(
        sorted(set(stated[in_order]) - {"ss03"}, key=_config_index)
    )
    assert first["config_class_note"] == second["config_class_note"] == note
    cached = _copy(cold, tmp_path)
    capfd.readouterr()
    _build(cached, mini_bundle, audit_path=audit, jobs=1)
    report = capfd.readouterr().err
    cached_count, total = _counts(report, CACHED)
    byte_copied, written, _ = _counts(report, BYTE_COPIED)
    assert cached_count == byte_copied == written == total
    assert len(captured) == 2
    _assert_no_pooled_map_was_written(*captured[1])
    assert _tree(cached) == _tree(cold)


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
    slim_by_id = {record.prior_id: record.slim for record in store.values()}
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


def _walked(monkeypatch) -> list[dict[str, set[int]]]:
    """Record the records the build asks `locate_prior_fragments` to place, as each class's id words, so a test can tell whether the shards were parsed to place a unit."""
    calls: list[dict[str, set[int]]] = []
    real = unit_cache.locate_prior_fragments

    def spy(out_dir, wanted):
        grouped: dict[str, set[int]] = {}
        for class_index, word in zip(wanted.class_of, wanted.words):
            grouped.setdefault(wanted.classes[class_index], set()).add(word)
        calls.append(grouped)
        return real(out_dir, wanted)

    monkeypatch.setattr(unit_cache, "locate_prior_fragments", spy)
    return calls


def test_a_recomputed_fragment_the_parent_would_patch_under_another_scaffold_fails_the_build(
    mini_bundle, tmp_path, monkeypatch
):
    """End-to-end check of the write's guard: a cold build whose parent holds a different value for a stamped scaffold key than the worker drafted under fails with `check_scaffold`'s `SystemExit` naming the key, instead of shipping a fragment whose id names other content. A recomputed unit's id is empty until `unit_to_json` stamps it, so a scaffold taken over a unit that already has an id is the write's, and changing its group there makes the parent disagree with the worker. `jobs=1` drafts in this process, so the patched `unit_scaffold` reaches both calls."""
    unit_scaffold = review_build.unit_scaffold

    def moved_at_the_write(unit, *args, **kwargs):
        scaffold = unit_scaffold(unit, *args, **kwargs)
        if unit.unit_id:
            scaffold["group"] = f"{scaffold['group']}-moved"
        return scaffold

    monkeypatch.setattr(review_build, "unit_scaffold", moved_at_the_write)
    with pytest.raises(SystemExit, match=r"other values of group than the write patches"):
        _build(tmp_path / "corpus", mini_bundle, jobs=1)


def test_a_cached_build_places_every_unit_from_the_store_without_walking_the_shards(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """Each store record carries the address the shard writer returned for its fragment, so a no-change rebuild plans by looking units up in the store. It never parses the previous corpus's shards to find a unit, and at the write the reader checks each cached fragment against the record's id and stamp."""
    corpus = _copy(mini_corpus, tmp_path)
    store = unit_cache.load_store(corpus, _environment(corpus))
    assert store is not None and all(cached.address is not None for cached in store.values())
    calls = _walked(monkeypatch)
    _build(corpus, mini_bundle, jobs=1)
    cached, total = _cached(capfd)
    assert cached == total
    assert calls == []
    assert _tree(corpus) == _tree(mini_corpus)


def _environment(corpus: Path) -> str:
    return _stamps(corpus)["environment"]


def _settled(monkeypatch) -> list[int]:
    """Record the size of every settlement batch the build's enrichers request, so a test can count the kernel passes a build's enrichment took."""
    widths: list[int] = []
    real = review_enrich.Enricher.explain_units

    def spy(self, units):
        widths.append(len(units))
        return real(self, units)

    monkeypatch.setattr(review_enrich.Enricher, "explain_units", spy)
    return widths


def test_a_cached_build_settles_its_verification_sample_in_one_pass(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """A build that reuses every unit still recomputes its verification sample, and it settles the sample in one `explain_units` call per chunk instead of one per unit. A one-window settlement spawns a kernel process per position, so a per-unit sample would cost hundreds of spawns. The sample is smaller than a chunk, so it is one call. `jobs=1` keeps the enricher in this process, where the spy can see it."""
    corpus = _copy(mini_corpus, tmp_path)
    widths = _settled(monkeypatch)
    _build(corpus, mini_bundle, jobs=1)
    cached, total = _cached(capfd)
    assert cached == total
    assert widths == [min(review_build.VERIFICATION_SAMPLE, total)]
    assert _tree(corpus) == _tree(mini_corpus)


def test_a_store_without_addresses_still_supplies_every_unit_through_the_walk(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """A store whose records have no address names each unit's fragment by id and class only. The build still reuses every unit from it, placing the units by a walk over the previous corpus's shards, and writes the same bytes as a build reused from an addressed store, including the addresses in the rewritten store."""
    corpus = _copy(mini_corpus, tmp_path)
    _rewrite_store(corpus, lambda record: {key: value for key, value in record.items() if key != "address"})
    store = unit_cache.load_store(corpus, _environment(corpus))
    assert store is not None and all(cached.address is None for cached in store.values())
    calls = _walked(monkeypatch)
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    cached, total = _cached(capfd)
    assert cached == total
    assert sum(len(ids) for call in calls for ids in call.values()) == total
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


def test_a_shard_rewritten_underneath_the_store_is_walked_rather_than_trusted(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """An address is valid only for the bytes it was taken from, and the manifest stamp says nothing about a shard's bytes. So the store records each part's size, and a part rewritten underneath it (here compactly, with every stamp intact) is placed by the walk again. Every unit is still reused, and the parts that did not change are trusted as before. The walked units are not copied as they are, so nothing predicts them unchanged, and the share rule writes their class directly, deflated (#618's change 35), with each record's fourth address element and each row's marker, while every other part stays as it was. The corpus materializes to the files the unedited build wrote (decision 48)."""
    corpus = _copy(mini_corpus, tmp_path)
    path = next(path for path in _shard_paths(corpus) if json.loads(path.read_text(encoding="utf-8")))
    fragments = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(fragments), encoding="utf-8")
    calls = _walked(monkeypatch)
    capfd.readouterr()
    _build(corpus, mini_bundle, jobs=1)
    report = capfd.readouterr().err
    cached, total = _counts(report, CACHED)
    assert cached == total
    assert sum(len(ids) for call in calls for ids in call.values()) == len(fragments)
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
    mini_corpus, mini_bundle, tmp_path, monkeypatch, write_jobs
):
    """The size check cannot see an edit that leaves a part the same length, so such a fragment is trusted into the plan. The reader catches it at the write, where every cached fragment is checked against its record, and the build fails instead of reusing bytes the store does not describe. The write fails the same way in this process and in a write worker, which hands the parent its message, and either way it leaves no staged part behind. A build that recomputes every unit recovers the corpus: it reuses no record, and the write's compare finds the edited slot unequal to the recomputed fragment and appends it, so the corpus holds the unedited build's fragments and materializes to its files. The width is set because the mini bundle's largest class would bound it below three (`_write_width`)."""
    monkeypatch.setattr("rebuild.review.build._write_width", lambda classes, fit: fit)
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


def test_two_units_spelling_one_input_key_are_refused_at_the_plan(mini_bundle, tmp_path, monkeypatch):
    """Two units cannot have the same input key: every row belongs to one triple and every key line includes the row's window and names, so a repeat means a sha256 collision. The plan fails on it as it builds its index over the input keys (`UnitStore.input_key_index`), naming both units, before the store is opened, instead of supplying both units from one record and failing later on the store's duplicate-id check. The stream computes the keys (`audit_stream.input_key`), so the test makes every one the same there."""
    monkeypatch.setattr(audit_stream, "input_key", lambda lines, suffix: bytes(32))
    with pytest.raises(SystemExit) as raised:
        _build(tmp_path / "corpus", mini_bundle, jobs=1)
    match = re.search(r"units (\S+) and (\S+) spell one input key", str(raised.value))
    assert match and match.group(1) != match.group(2)


def test_a_load_that_raises_is_not_swallowed_by_the_store_parse(
    mini_corpus, mini_bundle, tmp_path, monkeypatch
):
    """The load runs in the plan's frame, not the stream's, so an error the load itself raises (a row loaded twice, an id that does not match its content key, a coding error) propagates unchanged instead of being read as an unreadable store and falling back to a full build."""
    corpus = _copy(mini_corpus, tmp_path)

    def refuse(self, ordinal, cached, *, codepoints, found=None):
        raise ValueError("the load's own error")

    monkeypatch.setattr(UnitStore, "load_cached", refuse)
    with pytest.raises(ValueError, match="the load's own error"):
        _build(corpus, mini_bundle, jobs=1)


@pytest.mark.parametrize("held", ["plain", "deflated"])
def test_recompute_all_units_reuses_no_record_and_keeps_every_fragment_in_place(
    mini_corpus, deflated_corpus, mini_bundle, tmp_path, capfd, held
):
    """`recompute_all_units` reuses no record and recomputes every unit, but the prior store still gives each fragment's address, so the shards compare every recomputed fragment with its prior, and a corpus whose units all come out the same is left byte for byte as it was, its ink-signature store, which the pass reshapes, read without the two stamps that record which audit and family keys it was written over (`_signature_lines`). Over a corpus that holds fragments deflated (`deflated_corpus`, #618's change 35), the compare reads each deflated prior's recorded inflated length (decision 76) and inflates it, so a deflated prior whose inflated bytes equal the new fragment's is kept where it is, deflated, with its record's fourth address element and its row's marker."""
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
    after = _tree(corpus)
    for tree in (before, after):
        tree[unit_cache.SIGNATURE_STORE_NAME] = _signature_lines(tree[unit_cache.SIGNATURE_STORE_NAME])
    assert after == before


def test_serial_and_parallel_builds_are_byte_identical(mini_corpus, mini_bundle, tmp_path):
    """A pooled build drafts the recomputed units in configuration order (`_configuration_order`) across two workers, and the serial `mini_corpus` drafts the same units in load order in one process. Both produce the same bytes because every order-sensitive whole-corpus pass runs in the parent over all the projections, so this comparison would show any effect of a worker's hand-out order on the output."""
    parallel = tmp_path / "parallel"
    _build(parallel, mini_bundle, jobs=2)
    assert _tree(parallel) == _tree(mini_corpus)


def _sidecars(corpus: Path) -> dict[str, bytes]:
    return {
        name: (corpus / name).read_bytes()
        for name in (
            unit_index.INDEX_NAME,
            app_index.APP_INDEX_NAME,
            app_index.LOCATOR_NAME,
            app_index.LOCATOR_ROWS_NAME,
        )
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


def test_a_pooled_write_is_byte_identical_to_the_serial_one(mini_bundle, tmp_path, capfd, monkeypatch):
    """The write cuts the classes into runs of shard order (`_write_runs`) and, wider than one, writes the runs in worker processes, each fed its run a chunk at a time from the parent's columns (`_write_chunks`), with its own shard writer, compressed sidecar lines and copy-forward cursors, while the parent writes each chunk's results into the store and folds its checker's facts as they arrive. Once the manifest is written the parent pulls each run's lines from the worker that wrote it and writes the sidecars. So a cold build and a cached rebuild whose write runs three wide match a serial build byte for byte, and the sidecars of all three are the bytes the writers that hold every fragment at once write over the same shards (`_sidecars_held_whole`). A rebuild over an edited audit that appends changed fragments to a class's overflow parts lays them out the same at either width, overflow parts and store segment included, since a class's appends go in its id order in the one process that writes it, and deflates each in that process (#618's change 35). So does a later rebuild that folds two classes (`_fold_due`), copying the deflated fragments one holds as they were stored and deflating every plain one. The chunk width is lowered so the larger classes span many chunks, some ending on a chunk's last unit. The cold build reads, patches and serializes every fragment in the workers. The cached rebuild copies every fragment and every sidecar row forward, each run's cursors reading past the lines of the runs before it. Each pooled pass files one `corpus-write` pool record in the journal the autouse fixture redirects. The width is set because the mini bundle's largest class would bound it below three (`_write_width`).

    The one value the write reads at another unit is the id of a secondary junction's primary unit, which a chunk carries as an id word (`ChunkStore.primary_units`). The mini bundle's junctions find no primary unit under the real matcher, since their two-letter windows are not units, so every build here accepts any containing window (`enrich._junction_outcomes_match`). That names a visible primary unit for some junctions and an ink-identical one, suppressing the junction, for others, and the corpus checks still pass. A missing word then changes the cold build's bytes, and keeps the cached rebuild from copying those units as they are.
    """
    from rebuild.tools import cycle_timings

    monkeypatch.setattr("rebuild.review.build._write_width", lambda classes, fit: fit)
    monkeypatch.setattr("rebuild.review.build.WRITE_CHUNK_UNITS", 11)
    monkeypatch.setattr("rebuild.review.enrich._junction_outcomes_match", lambda *_args: True)
    serial = tmp_path / "serial"
    _build(serial, mini_bundle, jobs=1, write_jobs=1)
    counts = json.loads((serial / "manifest.json").read_text(encoding="utf-8"))["secondary_junctions"]
    assert counts["junctions_with_primary_unit"] and counts["junctions_suppressed_invisible"], counts
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
    err = capfd.readouterr().err
    assert re.search(r"jobs=3 \(byte-copied", err), err
    byte_copied, total, copied_forward = _counts(err, BYTE_COPIED)
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
    records = [json.loads(line) for line in cycle_timings.JOURNAL.read_text(encoding="utf-8").splitlines()]
    assert [(record["unit"], record["width"]) for record in records] == [("corpus-write", 3)] * 4


def test_the_write_cuts_shard_order_into_contiguous_runs_no_wider_than_its_largest_class_bounds():
    """A class is written by one process, so the largest class bounds the write, and the width stops where that class alone is the even share: with the unit total over the largest class, rounded up. Runs are contiguous stretches of shard order, each closed before the class that would take it past the even share, and a class larger than the share is a run of its own. At a width of one the whole corpus is one run."""
    from array import array

    def classes(*sizes):
        return [
            review_build._ClassToWrite(
                id=f"c{index}",
                title="",
                summary="",
                note="",
                section="",
                status="",
                ink_identical=False,
                no_verdict=False,
                why="",
                ordinals=array("I", range(size)),
                prior_parts=(),
            )
            for index, size in enumerate(sizes)
        ]

    def cut(sizes, width):
        return [[entry.id for entry in run] for run in review_build._write_runs(classes(*sizes), width)]

    shaped = (686, 217, 401, 1, 23, 12, 110, 109)
    assert review_build._write_width(classes(*shaped), 17) == 3
    assert review_build._write_width(classes(*shaped), 2) == 2
    assert cut(shaped, 3) == [["c0"], ["c1"], ["c2", "c3", "c4", "c5"], ["c6", "c7"]]
    assert cut(shaped, 1) == [[f"c{index}" for index in range(len(shaped))]]
    assert review_build._write_width(classes(), 8) == 1 and cut((), 8) == [[]]


def test_a_narrowed_hand_out_pool_is_byte_identical_to_the_serial_build(
    mini_corpus, mini_bundle, tmp_path, monkeypatch
):
    """The pool hands the recomputed units out one batch at a time, so which worker drafts which unit depends on timing. Narrowing the hand-out splits the mini bundle's recomputed units into a few dozen batches over two workers, where per-worker state leaking into the output would show up as a changed byte against the serial build. At the checked-in `PHASE1_HANDOUT_UNITS`, `_handout_width` splits them into only a handful of batches, which exercises little interleaving."""
    monkeypatch.setattr("rebuild.review.build.PHASE1_HANDOUT_UNITS", 37)
    parallel = tmp_path / "narrowed"
    _build(parallel, mini_bundle, jobs=2)
    assert _tree(parallel) == _tree(mini_corpus)


def test_a_pool_handed_the_recomputed_units_in_any_configuration_order_is_byte_identical_to_the_serial_build(
    mini_corpus, mini_bundle, tmp_path, monkeypatch
):
    """The configuration sort exists to save memory, not for correctness, so the byte identity above must hold under any hand-out order. With the sort reversed (the last acceptance configuration first, load order within each), a two-worker pool over the narrowed hand-out still builds the serial corpus byte for byte, which shows that the sort changes no bytes."""
    from rebuild.review.audit import _config_index

    monkeypatch.setattr("rebuild.review.build.PHASE1_HANDOUT_UNITS", 37)
    monkeypatch.setattr(
        "rebuild.review.build._configuration_order",
        lambda recomputed, table: sorted(recomputed, key=lambda ordinal: -table.config_rank(ordinal)),
    )
    parallel = tmp_path / "reversed"
    _build(parallel, mini_bundle, jobs=2)
    assert _tree(parallel) == _tree(mini_corpus)


def test_a_pooled_cached_rebuild_is_byte_identical_to_the_serial_one(
    mini_corpus, mini_bundle, tmp_path, capfd
):
    """The pooled verify branch settles each worker's share of the sample and zips the reports back to `(unit, injection)` pairs by position. A report zipped onto its neighbor's unit would recompute that unit under another window, miss its content key, and fail the build. A cached rebuild at two jobs runs that branch, and its output matches the serial build byte for byte."""
    corpus = _copy(mini_corpus, tmp_path)
    _build(corpus, mini_bundle, jobs=2)
    cached, total = _cached(capfd)
    assert cached == total
    assert _tree(corpus) == _tree(mini_corpus)


def test_both_pooled_hand_outs_go_through_the_configuration_sort_and_cover_their_units(
    mini_bundle, tmp_path, capfd, monkeypatch
):
    """The parent applies the configuration sort at two call sites: the recomputed units in `_drive_phase1` and the verification sample in `verify`. The byte-identity tests above cannot detect a fault at either, because the corpus is the same bytes under any hand-out order, and a sampled unit that no worker receives goes unverified without changing a byte. So this test spies on the helper instead of replacing it. A cold two-worker build passes it every recomputed unit once. A cached rebuild over that corpus passes it no recomputed units and then the whole sample (every cached unit, up to `VERIFICATION_SAMPLE`). The units phase's `verified=` count equals the sample size, which checks that the contiguous shares `verify` cuts cover the whole sample and drop no tail past the last worker."""
    handed: list[list] = []
    original = review_build._configuration_order

    def spy(recomputed, table):
        handed.append(list(recomputed))
        return original(recomputed, table)

    monkeypatch.setattr("rebuild.review.build._configuration_order", spy)
    corpus = tmp_path / "corpus"
    _build(corpus, mini_bundle, jobs=2)
    cached, total = _cached(capfd)
    assert cached == 0 and total > 0
    assert [len(units) for units in handed] == [total]
    _build(corpus, mini_bundle, jobs=2)
    err = capfd.readouterr().err
    cached, total = _counts(err, CACHED)
    (verified,) = _counts(err, r"verified=(\d[\d,]*) cached")
    assert cached == total
    assert [len(units) for units in handed] == [total, 0, min(review_build.VERIFICATION_SAMPLE, total)]
    assert verified == len(handed[2]) == len(set(handed[2]))


SIGNATURES = r"signatures: (\d[\d,]*) cached, (\d[\d,]*) shaped"


def _signatures(capfd) -> tuple[int, int]:
    cached, shaped = _counts(capfd.readouterr().err, SIGNATURES)
    return cached, shaped


def test_a_pooled_signature_pass_is_byte_identical_to_the_serial_one(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """The signature phase's pool maps chunks of the signature misses, each reply carrying its worker's peak memory beside the chunk's digests, and `pool.map` keeps the chunks in miss order. So a pooled pass over a cold store puts every digest on the row that requested it, and the corpus it builds, including the ink-signature store, is byte-identical to the serial one. The pass also writes one `signature` pool record to the journal (redirected by the autouse fixture), with the peaks recorded under the worker labels; the serial build before it wrote nothing. The mini bundle's signature misses are few enough that one worker can finish them before the other has spawned, so the test checks the record's worker names and width, not that two workers replied. The threshold is lowered because the mini bundle's signature misses fall far below the checked-in one, and the units runner runs at one job so that the signature pool is the only pool in this build."""
    from rebuild.tools import cycle_timings

    monkeypatch.setattr("rebuild.review.build._SIGNATURE_POOL_THRESHOLD", 1)
    serial = tmp_path / "serial"
    _build(serial, mini_bundle, jobs=1)
    _cached, serial_shaped = _signatures(capfd)
    assert serial_shaped > 0
    pooled = tmp_path / "pooled"
    _build(pooled, mini_bundle, jobs=1, signature_jobs=2)
    err = capfd.readouterr().err
    match = re.search(r"signatures: (\d[\d,]*) cached, (\d[\d,]*) shaped across 2 workers", err)
    assert match, err
    assert int(match.group(2).replace(",", "")) == serial_shaped
    assert _tree(pooled) == _tree(mini_corpus)
    lines = cycle_timings.JOURNAL.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert (record["kind"], record["unit"], record["width"]) == ("pool", "signature", 2)
    peaks = record["worker_peak_rss_bytes"]
    assert 1 <= len(peaks) <= 2 and all(label.startswith("SpawnPoolWorker-") for label in peaks)
    assert all(isinstance(peak, int) and peak > 0 for peak in peaks.values())


def test_signature_misses_under_the_threshold_stay_serial(mini_corpus, mini_bundle, tmp_path, capfd):
    """Giving the signature phase its own width must not push a small set of signature misses off the serial path. Below `_SIGNATURE_POOL_THRESHOLD` the parent shapes through its shared shapers at any width, starts no pool, writes no pool record, and builds the same bytes. The autouse fixture redirects the journal, so the test checks that nothing was written to it."""
    from rebuild.tools import cycle_timings

    corpus = tmp_path / "shallow"
    _build(corpus, mini_bundle, jobs=1, signature_jobs=4)
    assert " shaped serially)" in capfd.readouterr().err
    assert not cycle_timings.JOURNAL.exists()
    assert _tree(corpus) == _tree(mini_corpus)


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
            root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=()
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
            root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=()
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
    "rebuild/pipeline/geometry.py",
    "rebuild/kernel-rs/src/fixpoint.rs",
    "rebuild/kernel-rs/src/fold.rs",
    "rebuild/kernel-rs/src/fanout.rs",
)
CORPUS_ONLY_CODE = (
    "rebuild/pipeline/kernel_exec.py",
    "rebuild/pipeline/labels.py",
    "rebuild/validation/pins.py",
    "rebuild/review/build.py",
    "rebuild/review/unit_cache.py",
    "rebuild/review/enrich.py",
    "rebuild/kernel-rs/src/main.rs",
    "rebuild/kernel-rs/src/engine.rs",
    "rebuild/kernel-rs/Cargo.lock",
)
COMPARATOR_CODE = (
    "rebuild/review/ink.py",
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
    """An edit to code the corpus build never runs (`CORPUS_UNREAD_CODE`) changes neither stamp, so it does not cost the next build a cold units phase (`unit_cache.corpus_code_paths`). An edit to a module the build runs and no signature does (`CORPUS_ONLY_CODE`) changes the unit store's stamp and leaves the signature store's alone (`unit_cache.signature_code_paths`). An edit to the comparator or a module it imports (`COMPARATOR_CODE`) changes both. The root is hand-built so the edits are to real files and the assertions are about the rosters, not this checkout; rebuild/test_review_code_closure.py checks the rosters against the walked import closure. Each file is a Python statement before and after its edit, because the code line's digest is prose-insensitive (`fingerprint.code_file_digest`) and falls back to raw bytes only for a file that does not parse, so a file of bare prose would test the fallback instead."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)

    def stamps() -> tuple[str, str]:
        return (
            unit_cache.environment_stamp(
                root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=()
            ).value,
            unit_cache.signature_environment(root, MINI_FONT, "after-helpers").value,
        )

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
    """The `draft_harness` line hashes test/test_shaping.py and the tools/ modules it imports (`fingerprint.font_compile_tool_paths`, whose closure rebuild/test_build_code_closure.py checks), not the whole tools/ directory. So an edit to an old-font script no draft imports leaves the unit store valid, and an edit to a font-compile module drops it with a miss note that names `draft_harness`. The root is hand-built so the edits are to real files and the assertions are about the line, not this checkout."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)
    for relative in ("tools/serve.py", "tools/glyph_compiler.py"):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(f"RELATIVE = {relative!r}\n", encoding="utf-8")
    out = tmp_path / "corpus"
    out.mkdir()

    def stamp() -> fingerprint.EnvironmentStamp:
        return unit_cache.environment_stamp(
            root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=()
        )

    base = stamp()
    unit_cache.write_store(out, base, [], parts=())
    _edit(root, "tools/serve.py")
    assert stamp().value == base.value
    assert unit_cache.store_miss_note(out, stamp()) is None
    _edit(root, "tools/glyph_compiler.py")
    edited = stamp()
    assert edited.value != base.value
    assert unit_cache.store_miss_note(out, edited) == "the stamp moved at draft_harness (changed)"


def _both_stamps(root: Path, spec) -> tuple[fingerprint.EnvironmentStamp, fingerprint.EnvironmentStamp]:
    return (
        unit_cache.environment_stamp(
            root, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=()
        ),
        unit_cache.signature_environment(root, MINI_FONT, "after-helpers"),
    )


def test_a_miss_note_names_the_closure_file_that_moved(tmp_path):
    """When a store's code line changes, its miss note names the changed file inside the closure, not the closure digest: under `corpus_code` for the unit store and under `comparator_code` for the ink-signature store. An edit to a module only the build runs is named for the unit store and gives the signature store no miss note, which checks the narrowing `signature_code_paths` makes through the stores themselves."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)
    out = tmp_path / "corpus"
    out.mkdir()
    unit_stamp, signature_stamp = _both_stamps(root, spec)
    unit_cache.write_store(out, unit_stamp, [], parts=())
    unit_cache.write_signature_store(out, signature_stamp, {})
    assert unit_cache.store_miss_note(out, unit_stamp) is None
    assert unit_cache.signature_miss_note(out, signature_stamp) is None

    _edit(root, "rebuild/review/ink.py")
    unit_stamp, signature_stamp = _both_stamps(root, spec)
    assert unit_cache.load_store(out, unit_stamp) is None
    assert unit_cache.load_signature_store(out, signature_stamp) is None
    assert (
        unit_cache.store_miss_note(out, unit_stamp)
        == "the stamp moved at corpus_code: rebuild/review/ink.py (changed)"
    )
    assert (
        unit_cache.signature_miss_note(out, signature_stamp)
        == "the stamp moved at comparator_code: rebuild/review/ink.py (changed)"
    )

    unit_cache.write_store(out, unit_stamp, [], parts=())
    unit_cache.write_signature_store(out, signature_stamp, {})
    _edit(root, "rebuild/review/build.py")
    unit_stamp, signature_stamp = _both_stamps(root, spec)
    assert (
        unit_cache.store_miss_note(out, unit_stamp)
        == "the stamp moved at corpus_code: rebuild/review/build.py (changed)"
    )
    assert unit_cache.signature_miss_note(out, signature_stamp) is None
    assert unit_cache.load_signature_store(out, signature_stamp) == {}


def test_the_other_ways_to_miss_each_read_differently(tmp_path):
    """No store, an unreadable store, and a store stamped for another manifest need different fixes, so each gets its own note, and a store whose stamp still matches gets none. Only the unit store has the manifest case, because the signature store describes no shard."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)
    out = tmp_path / "corpus"
    out.mkdir()
    unit_stamp, signature_stamp = _both_stamps(root, spec)
    assert unit_cache.store_miss_note(out, unit_stamp) == unit_cache.NO_STORE_NOTE
    assert unit_cache.signature_miss_note(out, signature_stamp) == unit_cache.NO_STORE_NOTE

    unit_cache.store_path(out).write_bytes(b"not a gzip stream")
    unit_cache.signature_store_path(out).write_bytes(b"not a gzip stream")
    assert unit_cache.store_miss_note(out, unit_stamp) == unit_cache.UNREADABLE_NOTE
    assert unit_cache.signature_miss_note(out, signature_stamp) == unit_cache.UNREADABLE_NOTE

    (out / "manifest.json").write_text(json.dumps({"classes": []}), encoding="utf-8")
    unit_cache.write_store(out, unit_stamp, [], parts=())
    assert unit_cache.store_miss_note(out, unit_stamp) is None
    (out / "manifest.json").write_text(json.dumps({"classes": [{"id": "moved"}]}), encoding="utf-8")
    assert unit_cache.load_store(out, unit_stamp) is None
    assert unit_cache.store_miss_note(out, unit_stamp) == unit_cache.MANIFEST_MOVED_NOTE


def test_a_broad_closure_move_names_a_handful_of_files_and_a_count(tmp_path):
    """The expanded code label uses the same cap `moved_note` puts on its label list, so a change across the whole closure (a formatter pass, a rename that touches every module) prints `CODE_FILES_SHOWN` files and a count instead of every file in the closure."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)
    out = tmp_path / "corpus"
    out.mkdir()
    unit_stamp, _ = _both_stamps(root, spec)
    unit_cache.write_store(out, unit_stamp, [], parts=())
    moved = sorted(CORPUS_ONLY_CODE + COMPARATOR_CODE)
    assert len(moved) > unit_cache.CODE_FILES_SHOWN
    for relative in moved:
        _edit(root, relative)
    unit_stamp, _ = _both_stamps(root, spec)
    note = unit_cache.store_miss_note(out, unit_stamp)
    shown = ", ".join(f"{relative} (changed)" for relative in moved[: unit_cache.CODE_FILES_SHOWN])
    remainder = len(moved) - unit_cache.CODE_FILES_SHOWN
    assert note == f"the stamp moved at corpus_code: {shown} and {remainder} more"


def test_the_store_header_records_the_stamp_lines_beside_the_hex_it_compares(tmp_path):
    """The header's `environment` is the hex a load compares, and it equals sha256 over the stamp's lines joined by newlines, which a reader reconstructing a stamp from the recorded lines depends on. `environment_lines` and `environment_detail` record the lines themselves beside it. A caller holding only the recorded hex still loads either store, as `test_a_promoted_corpus_still_answers_for_itself` in rebuild/test_artifact_cycle.py does after promoting a corpus. A store written under a bare hex records no lines, and a miss against it says the stamp moved without naming a label, instead of reporting every label as gone."""
    spec = fixtures.mini_spec()
    root = _stamped_root(tmp_path)
    out = tmp_path / "corpus"
    out.mkdir()
    unit_stamp, signature_stamp = _both_stamps(root, spec)
    unit_cache.write_store(out, unit_stamp, [], parts=())
    unit_cache.write_signature_store(out, signature_stamp, {"k1": "d1"})
    for stamp, header, code_label in (
        (unit_stamp, unit_cache.store_stamps(out), "corpus_code"),
        (signature_stamp, unit_cache.read_header(unit_cache.signature_store_path(out)), "comparator_code"),
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
    assert unit_cache.load_signature_store(out, signature_stamp.value) == {"k1": "d1"}

    bare = tmp_path / "bare"
    bare.mkdir()
    unit_cache.write_store(bare, unit_stamp.value, [], parts=())
    unit_cache.write_signature_store(bare, signature_stamp.value, {})
    for header in (
        unit_cache.store_stamps(bare),
        unit_cache.read_header(unit_cache.signature_store_path(bare)),
    ):
        assert header is not None
        assert "environment_lines" not in header and "environment_detail" not in header
    assert unit_cache.load_store(bare, unit_stamp) == {}
    _edit(root, "rebuild/review/ink.py")
    unit_stamp, signature_stamp = _both_stamps(root, spec)
    assert unit_cache.store_miss_note(bare, unit_stamp) == "the stamp moved"
    assert unit_cache.signature_miss_note(bare, signature_stamp) == "the stamp moved"


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
            tmp_path, spec, MINI, MINI_FONT, MINI_FONT, "after-helpers", identical_classes=()
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


# --- the key and cluster byte-contracts ------------------------------------------------


def _unit(codepoints: str, matched: str = "join-dropped") -> tuple[UnitTable, RowColumns, int]:
    """Return one unit's table, row columns, and ordinal, the three things the keyer reads."""
    row = AuditRow(
        config="default",
        codepoints=codepoints,
        kinds=("junction",),
        matched_entry=matched,
        baseline=("a", "b"),
        new=("c", "d"),
    )
    table, rows = load_table([row], [], _FAMILY_OF)
    assert table.n == 1
    return table, rows, 0


_FAMILY_OF = {0xE650: "qsPea", 0xE652: "qsTea", 0xE668: "qsRoe"}


def _keyer(**overrides) -> unit_cache.UnitKeyer:
    family_keys = {"qsPea": "p0", "qsTea": "t0", "qsRoe": "r0", "qsPea_qsTea": "pt0", **overrides}
    return unit_cache.UnitKeyer(family_keys, _FAMILY_OF)


def test_unit_key_moves_only_with_window_families():
    pair = _unit("E650:E652")
    base = _keyer().key(*pair)
    assert _keyer(qsRoe="r1").key(*pair) == base
    assert _keyer(qsTea="t1").key(*pair) != base
    assert _keyer(qsPea_qsTea="pt1").key(*pair) != base
    solo = _unit("0020:E650")
    assert _keyer().key(*solo) != _keyer(qsPea="p1").key(*solo)
    assert _keyer().key(*solo) == _keyer(qsPea_qsTea="pt1", qsTea="t1", qsRoe="r1").key(*solo)


def test_unit_key_moves_with_row_content():
    assert _keyer().key(*_unit("E650:E652")) != _keyer().key(*_unit("E650:E652", matched="UNMATCHED"))


def _key_over_rows(keyer: unit_cache.UnitKeyer, codepoints: tuple[int, ...], rows: list[AuditRow]) -> str:
    """Compute the unit key directly over row records: each row as the audit's own line, then the window families' keys. This is the form `UnitKeyer.key` reconstructs from the row columns, written here over `AuditRow`s so the two can be compared."""
    families = frozenset(_FAMILY_OF[value] for value in codepoints if value in _FAMILY_OF)
    lines = [
        "\t".join(
            (
                row.config,
                row.codepoints,
                ",".join(row.kinds),
                row.matched_entry,
                "|".join(row.baseline),
                "|".join(row.new),
            )
        )
        for row in rows
    ]
    lines += [f"{name}\t{keyer._family_keys[name]}" for name in keyer._relevant_families(families)]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def test_a_merged_survivors_key_is_the_key_over_its_absorbed_rows_own_names():
    """After the ink-duplicate merge, a survivor's run spans two name-tuple pairs, its own and the absorbed sibling's, merged by config rank with the survivor's row first at a tie. That is the stable sort of the two units' rows concatenated survivor first. The content key is over that run with each row's own names, so it equals a hash over the row records in that order, and it changes when an absorbed row's rendered names change. A key over the survivor's names alone would reuse a stale fragment to a window whose absorbed rows had changed."""

    def rows_for(new_a: tuple[str, ...], new_b: tuple[str, ...]) -> list[AuditRow]:
        return [
            AuditRow("default", "E650:E652", ("cell",), "UNMATCHED", ("a", "b"), new_a),
            AuditRow("ss04", "E650:E652", ("cell",), "UNMATCHED", ("a", "b"), new_a),
            AuditRow("ss03", "E650:E652", ("junction",), "UNMATCHED", ("a.ss03", "b"), new_b),
            AuditRow("ss04", "E650:E652", ("junction",), "UNMATCHED", ("a.ss03", "b"), new_b),
        ]

    def merged(records: list[AuditRow]) -> tuple[UnitTable, RowColumns, list[AuditRow]]:
        table, columns = load_table(records, [], _FAMILY_OF)
        assert table.n == 2
        survivor = next(ordinal for ordinal in range(2) if table.configs(ordinal)[0] == "default")
        absorbed = 1 - survivor
        survivor_rows = [row for row in records if row.baseline == table.baseline(survivor)]
        absorbed_rows = [row for row in records if row.baseline == table.baseline(absorbed)]
        stats = merge_ink_duplicate_units(table, columns, lambda window, config: window)
        assert stats["units_merged"] == 1 and table.survivor(absorbed) == survivor
        assert table.compact().survivor[absorbed] == 0 and table.n == 1
        ordered = sorted(survivor_rows + absorbed_rows, key=lambda row: _config_index(row.config))
        return table, columns, ordered

    table, columns, ordered = merged(rows_for(("c", "d"), ("c", "d.ss03")))
    survivor = table.unit(0)
    assert survivor.configs == ("default", "ss03", "ss04", "ss04")
    assert [row.config for row in ordered] == list(survivor.configs)
    assert [row.baseline for row in ordered] == [("a", "b"), ("a.ss03", "b"), ("a", "b"), ("a.ss03", "b")]
    base = _keyer().key(table, columns, 0)
    assert base == _key_over_rows(_keyer(), survivor.codepoint_values, ordered)

    moved_table, moved_columns, moved_ordered = merged(rows_for(("c", "d"), ("c", "d.moved")))
    moved = moved_table.unit(0)
    assert moved.baseline == survivor.baseline and moved.new == survivor.new
    assert (
        _keyer().key(moved_table, moved_columns, 0)
        == _key_over_rows(_keyer(), moved.codepoint_values, moved_ordered)
        != base
    )


_SIGNATURE_ROW = AuditRow(
    config="default",
    codepoints="E650:E652",
    kinds=("junction",),
    matched_entry="join-dropped",
    baseline=("a", "b"),
    new=("c", "d"),
)


def test_signature_key_moves_with_render_identity_not_classification():
    """The signature store depends on this split: everything that can change the placed ink (the window, the config, either font's rendered names, a window family's rune or compiled glyphs) changes the signature key, while the row's classification fields (`kinds`, `matched_entry`) do not, so a ledger edit never re-shapes a window."""
    row = _SIGNATURE_ROW
    base = _keyer().signature_key(row)
    assert _keyer().signature_key(replace(row, kinds=("cell",))) == base
    assert _keyer().signature_key(replace(row, matched_entry="UNMATCHED")) == base
    assert _keyer().signature_key(replace(row, config="ss03")) != base
    assert _keyer().signature_key(replace(row, codepoints="E650:E650")) != base
    assert _keyer().signature_key(replace(row, baseline=("a", "x"))) != base
    assert _keyer().signature_key(replace(row, new=("c", "x"))) != base
    assert _keyer(qsTea="t1").signature_key(row) != base
    assert _keyer(qsPea_qsTea="pt1").signature_key(row) != base
    assert _keyer(qsRoe="r1").signature_key(row) == base


def test_the_one_walk_signature_keys_match_the_per_row_key():
    """`signature_keys` computes a window's family lines once per run of rows sharing the window, and the build keys the ink-signature store with it, so its keys must be byte-identical to `signature_key`'s for the store to keep serving. The windows here cite different families (a ligature pair, ·Roe alone, none) and one recurs after another window, each with several configs, so a family suffix carried across a window boundary would give a wrong key."""
    windows = ("E650:E652", "0020:E668", "0020", "E650:E652", "E668:E650")
    rows = [
        replace(_SIGNATURE_ROW, config=config, codepoints=window, new=(window, config))
        for window in windows
        for config in ("default", "ss02", "ss03")
    ]
    keyer = _keyer()
    walked = list(keyer.signature_keys(iter(rows)))
    assert all(walked_row is row for (walked_row, _key), row in zip(walked, rows))
    assert [key for _row, key in walked] == [keyer.signature_key(row) for row in rows]


def test_signature_store_round_trip_and_invalidation(tmp_path):
    entries = {"k2": "d2", "k1": "d1"}
    unit_cache.write_signature_store(tmp_path, "env-a", entries)
    assert unit_cache.load_signature_store(tmp_path, "env-a") == entries
    assert unit_cache.load_signature_store(tmp_path, "env-b") is None
    assert unit_cache.load_signature_store(tmp_path / "missing", "env-a") is None
    unit_cache.signature_store_path(tmp_path).write_bytes(b"not a gzip stream")
    assert unit_cache.load_signature_store(tmp_path, "env-a") is None


def test_a_failed_signature_write_leaves_the_previous_store_whole(tmp_path):
    """The write is staged under a sibling name and renamed last, so a write that fails partway (standing in for a build killed during the write) leaves the previous store readable, with no truncated gzip at the final path and no staging file left behind."""
    entries = {"k1": "d1", "k2": "d2"}
    unit_cache.write_signature_store(tmp_path, "env-a", entries)

    class Failing(dict):
        def __getitem__(self, key):
            if key == "k2":
                raise RuntimeError("the write stops here")
            return super().__getitem__(key)

    with pytest.raises(RuntimeError, match="stops here"):
        unit_cache.write_signature_store(tmp_path, "env-a", Failing(k1="x1", k2="x2"))
    assert unit_cache.load_signature_store(tmp_path, "env-a") == entries
    path = unit_cache.signature_store_path(tmp_path)
    assert not path.with_name(path.name + ".partial").exists()


def test_the_staged_signature_store_is_byte_identical_to_one_written_at_the_final_path(tmp_path):
    """`GzipFile` writes the handle's basename into the gzip header when it is given no filename, so writing through the staging handle would put `.partial` into every store's bytes. The writer names the final path in the header instead, so the bytes match a write straight to that path."""
    entries = {"k1": "d1", "k2": "d2"}
    (tmp_path / "staged").mkdir()
    unit_cache.write_signature_store(tmp_path / "staged", "env-a", entries)
    direct = tmp_path / "direct" / unit_cache.signature_store_path(tmp_path).name
    direct.parent.mkdir()
    with open(direct, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
            stream.write(
                (
                    json.dumps({"format": unit_cache.SIGNATURE_STORE_FORMAT, "environment": "env-a"}) + "\n"
                ).encode()
            )
            for key in sorted(entries):
                stream.write(f"{key}\t{entries[key]}\n".encode())
    staged = unit_cache.signature_store_path(tmp_path / "staged").read_bytes()
    assert staged == direct.read_bytes()
    assert b".partial" not in staged


def _spy_signature_write(monkeypatch) -> list[tuple[bool, str]]:
    """Record, for each signature-store write, whether it ran on the main thread and the environment it was stamped with, then call the real writer. The build calls the writer through the module attribute, so one patch covers the threaded and the inline paths."""
    calls: list[tuple[bool, str]] = []
    real = unit_cache.write_signature_arrays

    def spy(out_dir, environment, signatures, stamps=None):
        calls.append((threading.current_thread() is threading.main_thread(), environment))
        real(out_dir, environment, signatures, stamps)

    monkeypatch.setattr(unit_cache, "write_signature_arrays", spy)
    return calls


def test_a_pooled_build_writes_its_signature_store_off_the_main_thread_and_joins_it(
    mini_bundle, tmp_path, monkeypatch
):
    """At `jobs=2` the units phase is pooled and the parent waits in `multiprocessing.connection.wait`, so the signature store is written on a thread during that phase, and the build joins the thread in the cache phase before it returns. The mini bundle's store is small enough that the thread would finish before the build returns whether or not the build joined it, so the test holds the write until the build calls `join`: a build that returned without joining would return with the store unwritten. `test_serial_and_parallel_builds_are_byte_identical` and `test_a_narrowed_hand_out_pool_is_byte_identical_to_the_serial_build` compare the bytes the thread writes against the inline path's, since they compare the whole tree, including the store and any staging file. The autouse fixture redirects the journal, so the pool record this build writes does not reach the real one."""
    calls: list[tuple[bool, str]] = []
    release, finished = threading.Event(), threading.Event()
    writers: list[threading.Thread] = []
    real = unit_cache.write_signature_arrays
    join = review_build._SignatureWrite.join

    def spy(out_dir, environment, signatures, stamps=None):
        writers.append(threading.current_thread())
        calls.append((threading.current_thread() is threading.main_thread(), environment))
        release.wait(120)
        real(out_dir, environment, signatures, stamps)
        finished.set()

    def joining(self):
        release.set()
        join(self)

    monkeypatch.setattr(unit_cache, "write_signature_arrays", spy)
    monkeypatch.setattr(review_build._SignatureWrite, "join", joining)
    corpus = tmp_path / "pooled"
    try:
        _build(corpus, mini_bundle, jobs=2)
        written_at_return = finished.is_set()
    finally:
        release.set()
        for writer in writers:
            writer.join(120)
    assert written_at_return
    assert len(calls) == 1
    on_main_thread, environment = calls[0]
    assert not on_main_thread
    assert unit_cache.load_signature_store(corpus, environment) is not None


def test_a_serial_build_writes_its_signature_store_inline_before_the_units_phase_drafts_anything(
    mini_bundle, tmp_path, monkeypatch
):
    """The serial build has no idle parent to overlap the write with, and a CPU-bound parent would compete with a thread instead. So at `jobs=1` the store is written inline on the main thread at the units-phase boundary, where the pooled build starts its thread. The store is on disk, stamped for this build's environment, before `_RecomputeRunner.phase1` drafts a unit, so the columns are freed before the units phase instead of after the cache phase."""
    calls = _spy_signature_write(monkeypatch)
    corpus = tmp_path / "serial"
    phase1 = review_build._RecomputeRunner.phase1
    stored_at_drafting: list[bool] = []

    def drafting(self, *args):
        stored_at_drafting.append(
            len(calls) == 1 and unit_cache.load_signature_store(corpus, calls[0][1]) is not None
        )
        return phase1(self, *args)

    monkeypatch.setattr(review_build._RecomputeRunner, "phase1", drafting)
    _build(corpus, mini_bundle, jobs=1)
    assert len(calls) == 1
    on_main_thread, _environment = calls[0]
    assert on_main_thread
    assert stored_at_drafting == [True]


def _signature_columns(
    prior: dict[int, str], hits: Iterable[int], misses: dict[int, str]
) -> unit_cache.SignatureArrays:
    """A store's columns over `prior` (key to digest), with the prior entries at `hits` hit and `misses` shaped."""
    keys = sorted(prior)
    signatures = unit_cache.SignatureArrays(
        array("Q", keys), bytearray().join(bytes.fromhex(prior[key]) for key in keys), {}
    )
    for key in hits:
        signatures.mark_hit(signatures.prior_index(key))
    signatures.set_misses(array("Q", misses), list(misses.values()))
    return signatures


def _digest(seed: int) -> str:
    return hashlib.sha256(str(seed).encode()).hexdigest()


def test_the_signature_write_holds_no_columns_once_the_store_is_written(tmp_path):
    """The thread holds the store's columns only until the write finishes: `_write` drops them when `write_signature_arrays` returns, so after `join` the write holds nothing and the store is on disk. The failing case checks the attribute, the re-raise at `join`, and that the previous store survives the failed write. It does not check that the columns are freed, because the captured error's traceback keeps the write's frames, and the columns with them, until `join` raises."""
    write = review_build._SignatureWrite(tmp_path, "env-a", _signature_columns({}, (), {7: _digest(7)}))
    write.join()
    assert write._signatures is None
    assert unit_cache.load_signature_store(tmp_path, "env-a") == {f"{7:016x}": _digest(7)}

    class Failing(unit_cache.SignatureArrays):
        def lines(self):
            yield from list(super().lines())[:1]
            raise RuntimeError("the write stops here")

    failing_columns = Failing()
    failing_columns.set_misses(array("Q", [1, 2]), [_digest(1), _digest(2)])
    failing = review_build._SignatureWrite(tmp_path, "env-a", failing_columns)
    with pytest.raises(RuntimeError, match="stops here"):
        failing.join()
    assert failing._signatures is None
    assert unit_cache.load_signature_store(tmp_path, "env-a") == {f"{7:016x}": _digest(7)}


def test_a_failing_build_abandons_the_signature_write_rather_than_waiting_on_it(
    mini_bundle, tmp_path, monkeypatch
):
    """The thread is joined only on the success path, so a build that raises after starting it exits through its `finally` without waiting for the write. The spy holds the write open until the test releases it, so a build that joined would hang here instead of raising. The test then releases the write and joins its thread so nothing outlives the test."""
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    writers: list[threading.Thread] = []
    real = unit_cache.write_signature_arrays

    def spy(out_dir, environment, signatures, stamps=None):
        writers.append(threading.current_thread())
        started.set()
        release.wait(120)
        real(out_dir, environment, signatures, stamps)
        finished.set()

    def fail(*_args, **_kwargs):
        raise SystemExit("the corpus write stops here")

    monkeypatch.setattr(unit_cache, "write_signature_arrays", spy)
    monkeypatch.setattr(review_build, "_write_corpus", fail)
    try:
        with pytest.raises(SystemExit, match="stops here"):
            _build(tmp_path / "failing", mini_bundle, jobs=2)
        assert started.is_set()
        assert not finished.is_set()
    finally:
        release.set()
        for writer in writers:
            writer.join(120)


def test_the_store_written_from_its_columns_is_the_dict_writers_file(tmp_path):
    """The build writes the store from its columns (`write_signature_arrays`): the prior entries it hit, merged in key order with the misses it shaped, among them the late ones the stream shaped itself, each line `key\\tdigest`. Over the same entries the file is byte for byte the one `write_signature_store` writes from a dict, since a 16-digit lowercase hex key sorts as its integer does. Two misses with one key, shaped or late, keep the later one, as a dict does, and a prior entry no row hit is dropped."""
    prior = {key: _digest(key) for key in (3, 1 << 60, 5 << 56, 0xFFFF_FFFF_FFFF_FFFE, 42)}
    misses = array("Q", [9, 1 << 61, 9, 7 << 56])
    shaped = [_digest(100), _digest(101), _digest(102), _digest(103)]
    signatures = _signature_columns(prior, (3, 1 << 60, 0xFFFF_FFFF_FFFF_FFFE), {})
    signatures.set_misses(misses, shaped)
    signatures.add_late(1 << 59, bytes.fromhex(_digest(104)))
    signatures.add_late(1 << 58, bytes.fromhex(_digest(105)))
    signatures.add_late(1 << 58, bytes.fromhex(_digest(106)))
    assert (signatures.hits(), signatures.unhit(), signatures.misses()) == (3, 2, 6)
    columns = tmp_path / "columns"
    columns.mkdir()
    unit_cache.write_signature_arrays(columns, "env-a", signatures)
    entries = {f"{key:016x}": prior[key] for key in (3, 1 << 60, 0xFFFF_FFFF_FFFF_FFFE)}
    for key, digest in zip(misses, shaped):
        entries[f"{key:016x}"] = digest
    entries[f"{1 << 59:016x}"] = _digest(104)
    entries[f"{1 << 58:016x}"] = _digest(106)
    unit_cache.write_signature_store(tmp_path, "env-a", entries)
    assert filecmp.cmp(
        unit_cache.signature_store_path(columns), unit_cache.signature_store_path(tmp_path), shallow=False
    )
    loaded = unit_cache.SignatureArrays.load(columns, "env-a")
    assert loaded is not None and list(loaded.keys) == sorted(int(key, 16) for key in entries)
    stamped = tmp_path / "stamped"
    stamped.mkdir()
    unit_cache.write_signature_arrays(stamped, "env-a", signatures, {unit_cache.FAMILY_KEYS_KEY: "f"})
    header = unit_cache.read_header(unit_cache.signature_store_path(stamped))
    assert header is not None and header[unit_cache.FAMILY_KEYS_KEY] == "f"
    assert gzip.decompress(unit_cache.signature_store_path(stamped).read_bytes()).partition(b"\n")[2] == (
        gzip.decompress(unit_cache.signature_store_path(columns).read_bytes()).partition(b"\n")[2]
    )


def test_a_store_with_a_line_this_code_never_writes_does_not_load_as_columns(tmp_path):
    """The columns hold a key as an integer and a digest as bytes, so they reload only lines in the form the writer gives: a store with a key or a digest in another form, or keys out of order, reads as no store, and the build reshapes and rewrites it."""
    unit_cache.write_signature_store(tmp_path, "env-a", {f"{1:016x}": _digest(1), f"{2:016x}": _digest(2)})
    loaded = unit_cache.SignatureArrays.load(tmp_path, "env-a")
    assert loaded is not None and len(loaded) == 2
    for entries in ({"k1": "d1"}, {f"{1:016x}": _digest(1).upper()}, {f"{0xABC:016X}": _digest(1)}):
        unit_cache.write_signature_store(tmp_path, "env-a", entries)
        assert unit_cache.SignatureArrays.load(tmp_path, "env-a") is None
    header = json.dumps({"format": unit_cache.SIGNATURE_STORE_FORMAT, "environment": "env-a"})
    unsorted = f"{header}\n{2:016x}\t{_digest(2)}\n{1:016x}\t{_digest(1)}\n"
    unit_cache.signature_store_path(tmp_path).write_bytes(gzip.compress(unsorted.encode(), mtime=0))
    assert unit_cache.SignatureArrays.load(tmp_path, "env-a") is None


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


@pytest.mark.parametrize("shape", ["sections out of rank order", "an order break partway"])
def test_an_audit_not_in_sorted_sections_builds_through_the_in_memory_source(
    shape, mini_corpus, mini_bundle, tmp_path, capfd
):
    """An audit whose rows run_m1 never orders this way is no error: the build reads it through the in-memory source and says why on its load line. A break partway, two rows of one section swapped, leaves every unit's configs in their order, so the corpus is the sorted audit's. With the sections out of rank order a unit's per-config classes come in the file's order, which reaches its fragment, so that corpus holds the sorted audit's units and rows under other bytes."""
    header, rows = _mini_audit_lines()
    audit = _write_audit(tmp_path / "audit.tsv", header, _reordered(rows, shape))
    corpus = tmp_path / "corpus"
    capfd.readouterr()
    manifest = _build(corpus, mini_bundle, audit_path=audit, jobs=1)
    report = capfd.readouterr().err
    assert re.search(r"\(key-only read [^;]+; read in memory, since .+; prior store", report), report
    if shape == "an order break partway":
        assert "key-only read ran " in report
        assert _same_units(corpus, mini_corpus)
    else:
        expected = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["totals"] == expected["totals"]


@pytest.mark.parametrize("shape", ["sorted", "sections out of rank order"])
def test_an_audit_replaced_by_rename_after_the_load_opened_it_is_not_read(
    shape, mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """The build holds the descriptor it opened the audit with until its load returns and reads every byte of the audit through it, never by the path again, since a `--gates-only` pass can replace the audit by rename during a build. An audit replaced by a shorter one just after the build opened it is read as it was when opened: by the key-only read and the stream when it is in sorted sections, by the in-memory source otherwise."""
    header, rows = _mini_audit_lines()
    audit = _write_audit(
        tmp_path / "audit.tsv", header, rows if shape == "sorted" else _reordered(rows, shape)
    )
    expected = mini_corpus
    if shape != "sorted":
        expected = tmp_path / "expected"
        _build(
            expected,
            mini_bundle,
            audit_path=_write_audit(tmp_path / "unreplaced.tsv", header, _reordered(rows, shape)),
            jobs=1,
        )
    replacement = _write_audit(tmp_path / "replacement.tsv", header, rows[:10])
    layout = review_build.audit_layout

    def replaced(descriptor, path, members=None):
        found = layout(descriptor, path, members)
        os.replace(replacement, path)
        return found

    monkeypatch.setattr(review_build, "audit_layout", replaced)
    corpus = tmp_path / "corpus"
    capfd.readouterr()
    _build(corpus, mini_bundle, audit_path=audit, jobs=1)
    report = capfd.readouterr().err
    assert audit.read_text(encoding="utf-8").count("\n") == 11
    if shape == "sorted":
        assert "key-only read ran " in report and "; read as a stream;" in report
    else:
        assert "; read in memory, since " in report
    assert _same_units(corpus, expected)


@pytest.mark.parametrize("damage", ["a flipped bit", "rows that break the walk first"])
@pytest.mark.parametrize("reader", ["the key-only read", "the stream"])
def test_a_member_that_does_not_inflate_fails_the_build_naming_its_configuration(
    reader, damage, mini_bundle, tmp_path, monkeypatch
):
    """An audit whose base a fold wrote as gzip members (#618's change 36) is read by member, and a reader that seeks checks only the parts' sizes, so a member whose bytes are damaged at its recorded size is found as it is inflated: by a key-only read process, which replies with the failure the build raises, or by the stream when the store's stamps skip that read. Either fails the build with `audit_stream.MemberError` naming the section's configuration, and the build never reads the audit through its in-memory source. That holds for damage zlib catches as it inflates, and for damage that inflates to rows that break the walk before the member's end is checked, as damage inside a large member usually does: here a whole gzip member of a row repeated at a window past every other, so only the last window range reaches it, then padding to the member's recorded length."""
    from rebuild.pipeline import oracle

    header, rows = _mini_audit_lines()
    out = tmp_path / "m1"
    with monkeypatch.context() as patched:
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
    if reader == "the stream":
        monkeypatch.setattr(review_build, "_find_misses", lambda *_args: review_build._KeyOnly(False))
    monkeypatch.setattr(
        review_build,
        "load_workload",
        lambda *_args, **_kwargs: pytest.fail("the build read the audit through its in-memory source"),
    )
    with pytest.raises(
        audit_stream.MemberError, match=re.escape(f"the {victim.section!r} section in a member that")
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
    """The store this code writes records the audit's identity and the family keys' digest. A pass over the same audit and family keys skips the key-only read, since no key can then miss; a pass over another audit file, or with a family key moved, runs it."""
    corpus = _copy(mini_corpus, tmp_path)
    header = unit_cache.read_header(unit_cache.signature_store_path(corpus))
    assert header is not None and header[unit_cache.AUDIT_IDENTITY_KEY] == _identity(MINI_AUDIT)
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


def test_a_wrong_skip_shapes_the_miss_in_the_parent_and_writes_the_store(
    mini_corpus, mini_bundle, tmp_path, capfd, monkeypatch
):
    """A store whose stamps hold but which lacks an entry, which the stamps say cannot happen, costs time and never a byte: the pass skips the key-only read, the stream meets the missing key, shapes it in the parent as the serial path does, releasing the shape memo after it, so a wrong skip that misses every row holds one signature's shapes at a time, and the store is written with it again, so the corpus and the store's entries come out as they were."""
    late = review_build._LateShaper.__call__
    memo_after_shape: list[int] = []

    def watching(self, *args):
        shaped = self.shaped
        digest = late(self, *args)
        if self.shaped > shaped:
            memo_after_shape.append(review_build.shape_memo_sizes().entries)
        return digest

    monkeypatch.setattr(review_build._LateShaper, "__call__", watching)
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
    assert memo_after_shape == [0]
    assert "ink-signature store written" in report
    assert _signature_lines(path.read_bytes()) == _signature_lines(
        unit_cache.signature_store_path(mini_corpus).read_bytes()
    )
    assert _form(corpus, tmp_path) == _form(mini_corpus, tmp_path)


def test_cluster_id_from_repr_matches_the_tuple_recipe():
    """The c- ids recorded in rebuild/standing-approvals.yaml and in prior verdicts are hashes of `repr((tuple(configs), class_id, diffs))`. The piecewise hashing over the diffs' repr bytes must reproduce that byte stream exactly, including empty diffs and one-element config tuples."""
    piece = ((("moveTo", ((0, 0),)), ("lineTo", ((5, 0),))),)
    for configs, class_id, diffs in (
        (("default",), "join-dropped", ((), (), 0)),
        (("default", "ss03"), "split-by-space-or-zwnj", ((piece, (), 3), ((), piece, -2))),
        (("ss10",), "ss10-isolation-completed", ((piece, piece, 0),)),
    ):
        expected = "c-" + hashlib.sha1(repr((tuple(configs), class_id, diffs)).encode()).hexdigest()[:8]
        assert _cluster_id(configs, class_id, diffs) == expected
        assert _cluster_id_from_repr(configs, class_id, repr(diffs).encode()) == expected


def test_the_cluster_a_recomputed_unit_carries_keys_on_its_final_class(mini_corpus):
    """The runner computes a unit's cluster where it assigns the change group, so the cluster of an UNMATCHED or re-routed unit must be keyed on that group (the class its fragment is sharded under) and a ledger-classed unit's on its ledger class. The diffs behind the id are discarded with the worker, so the test checks the store instead: every unit's record, machine-approved ones included, carries a cluster, and a full fragment's cluster matches its record. A cached unit's cluster is taken from the record, which is sound only if the recomputation keyed on the same class the store records for the unit."""
    manifest = json.loads((mini_corpus / "manifest.json").read_text(encoding="utf-8"))
    store = unit_cache.load_store(mini_corpus, _stamps(mini_corpus)["environment"])
    assert store
    by_id = {cached.prior_id: cached for cached in store.values()}
    seen_group = False
    for meta in manifest["classes"]:
        for part in unit_index.class_shards(meta):
            for fragment in json.loads((mini_corpus / part).read_text(encoding="utf-8")):
                cached = by_id[fragment["id"]]
                assert cached.cluster.startswith("c-")
                if not slim_fragment(fragment):
                    assert fragment["cluster"] == cached.cluster
                assert cached.prior_class == meta["id"]
                if cached.change_group:
                    seen_group = True
                    assert meta["id"] == cached.change_group
    assert seen_group


def test_the_store_parse_interns_and_pools_what_repeats_across_records(tmp_path):
    """Two records that name the same class, cluster, change group, ledger class, config, delta, cell name, or junction token load as the same string objects, and their span and name tuples as the same tuple objects, so a store with a million records holds one instance per distinct name and per distinct tuple. The per-record keys, which never repeat, are not interned."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    unit_cache.write_store(tmp_path, "env-a", [_round_trip_unit(), replace(_round_trip_unit(), key="k2")])
    pool: dict = {}
    loaded = unit_cache.load_store(tmp_path, "env-a", pool=pool)
    assert loaded is not None
    first, second = loaded["k1"], loaded["k2"]
    assert first.prior_class is second.prior_class is sys.intern("split-by-space-or-zwnj")
    assert first.cluster is second.cluster
    assert first.diffs_digest is second.diffs_digest
    assert first.change_group is second.change_group
    assert first.ledger_class is second.ledger_class is sys.intern("identical-variant-name")
    assert next(iter(first.ink_deltas)) is next(iter(second.ink_deltas)) is sys.intern("default")
    assert first.ink_deltas["default"] is second.ink_deltas["default"]
    for name in ("after_cells", "after_junctions", "before_glyphs", "before_junctions"):
        assert getattr(first, name) is getattr(second, name), name
        assert all(a is b for a, b in zip(getattr(first, name), getattr(second, name), strict=True)), name
    assert first.after_junctions[0] is sys.intern("y5")
    for name in ("pair", "after_spans", "before_spans", "junction_pairs"):
        assert getattr(first, name) is getattr(second, name), name
    assert first.after_spans[1] is first.before_spans[1] is second.junction_pairs[0] is pool[(1, 2)]
    assert first.after_spans[0] is first.pair is pool[(0, 1)]
    assert replace(first, key="k2") == second


def test_a_streamed_store_pools_only_the_records_it_hands_over_without_an_address(tmp_path):
    """Without a caller's pool, the stream shares one table across the records that have no address, which a streaming caller buffers together for the walk, and shares none across addressed records, each of which is loaded and released before the next is parsed. So two addressed records with the same spans get equal but distinct tuples, and two addressless ones get the same object. A caller that passes its own table holds every record together and gets the sharing `load_store` provides."""
    fragments = [{"id": "u-0001", "content_key": "f" * 64}, {"id": "u-0002", "content_key": "f" * 64}]
    parts, spans = _write_shard(tmp_path, "small", fragments)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    records = [
        replace(
            _round_trip_unit(), key="k1", prior_id="u-0001", address=(parts[0], spans[0][1], spans[0][2])
        ),
        replace(
            _round_trip_unit(), key="k2", prior_id="u-0002", address=(parts[0], spans[1][1], spans[1][2])
        ),
        replace(_round_trip_unit(), key="k3", prior_id="u-0003"),
        replace(_round_trip_unit(), key="k4", prior_id="u-0004"),
    ]
    unit_cache.write_store(tmp_path, "env-a", records)
    stream = unit_cache.stream_store(tmp_path, "env-a")
    assert stream is not None
    by_key = {cached.key: cached for cached in stream}
    assert by_key["k1"].address is not None and by_key["k2"].address is not None
    assert by_key["k3"].address is None and by_key["k4"].address is None
    for name in ("pair", "after_spans", "after_cells", "before_spans", "before_glyphs", "junction_pairs"):
        assert getattr(by_key["k1"], name) == getattr(by_key["k2"], name), name
        assert getattr(by_key["k1"], name) is not getattr(by_key["k2"], name), name
        assert getattr(by_key["k3"], name) is getattr(by_key["k4"], name), name
    pool: dict = {}
    stream = unit_cache.stream_store(tmp_path, "env-a", pool=pool)
    assert stream is not None
    held = list(stream)
    assert all(cached.after_spans is held[0].after_spans is pool[held[0].after_spans] for cached in held)
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert (
        loaded is not None
        and loaded["k1"].after_cells is loaded["k2"].after_cells is loaded["k4"].after_cells
    )


def test_a_store_supplies_only_the_keys_the_workload_names(tmp_path, monkeypatch):
    """The load parses only the lines whose key the caller names, reading the key off the front of the raw line, so a record the workload no longer names costs a prefix comparison and is never held. The test counts the record lines that reach `json.loads`, because a load that parsed every line and dropped the unnamed ones would return the same keys. Without `wanted`, every record loads."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    records = [replace(_round_trip_unit(), key=key) for key in ("k1", "k2", "k3")]
    unit_cache.write_store(tmp_path, "env-a", records)
    parsed: list[str] = []
    real = json.loads

    def counting(text, *args, **kwargs):
        record = real(text, *args, **kwargs)
        if isinstance(record, dict) and "key" in record:
            parsed.append(record["key"])
        return record

    monkeypatch.setattr(unit_cache.json, "loads", counting)
    loaded = unit_cache.load_store(tmp_path, "env-a", wanted={"k1", "k3", "k9"})
    assert loaded is not None and sorted(loaded) == ["k1", "k3"]
    assert sorted(parsed) == ["k1", "k3"]
    assert loaded["k3"] == replace(_round_trip_cached(), key="k3")
    parsed.clear()
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and sorted(loaded) == ["k1", "k2", "k3"]
    assert sorted(parsed) == ["k1", "k2", "k3"]
    parsed.clear()
    assert unit_cache.load_store(tmp_path, "env-a", wanted=set()) == {}
    assert parsed == []


def test_a_line_whose_key_cannot_be_sliced_reads_as_absent(tmp_path):
    """A line that does not start with the key field, which `to_record` always writes first, cannot be selected by its prefix and is treated as absent: its neighbors load and nothing raises. So a store this code did not write costs at most a recomputation of the units it cannot name."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    records = [replace(_round_trip_unit(), key=key) for key in ("k1", "k2", "k3")]
    unit_cache.write_store(tmp_path, "env-a", records)
    _header, lines = _store_lines(tmp_path)
    record = json.loads(lines[1])
    lines[1] = (
        json.dumps({**{name: value for name, value in record.items() if name != "key"}, "key": "k2"}) + "\n"
    ).encode()
    _rewrite_store(tmp_path, lines=lines)
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and sorted(loaded) == ["k1", "k3"]
    assert unit_cache.load_store(tmp_path, "env-a", wanted={"k2"}) == {}


def test_the_store_parse_reproduces_the_projection_it_was_written_from(tmp_path):
    """The projection a cached unit passes to the primary-unit resolution pass, loaded into the unit store and serialized again the way the store writer serializes a recomputed unit's, equals the record's `proj`. The bytes of a re-addressed cached record depend on this. The tuples the store returns have the recomputed path's shapes: two-tuples of ints for the spans and the junction pairs, beside the unit's own id and codepoint values."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    prior_id = unit_cache.unit_id_for("f" * 64)
    written = replace(_round_trip_unit(), key="ab" * 32, prior_id=prior_id)
    unit_cache.write_store(tmp_path, "env-a", [written])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None
    walked = unit_cache.PriorFragment(
        "units/split-by-space-or-zwnj.json", 1, 5, prior_id, written.content_key
    )
    store = UnitStore(1)
    store.load_cached(0, loaded[written.key], codepoints=(1, 2), found=walked)
    primary_unit = store.primary_unit_projection(0)
    assert store.primary_unit_projection_record(0) == written.proj
    assert json.dumps(store.primary_unit_projection_record(0)) == json.dumps(written.proj)
    assert (
        (primary_unit.unit_id, primary_unit.codepoint_values)
        == (prior_id, (1, 2))
        == (store.unit_id(0), store.codepoints(0))
    )
    assert primary_unit.junction_pairs == ((1, 2),)
    assert (primary_unit.ink_identical, primary_unit.picture_identical) == (False, False)


def test_an_absent_manifest_hashes_to_a_sentinel_rather_than_raising(tmp_path):
    """The store's stamp is the identity of the manifest beside it, and the corpus may not have a manifest yet (a first build, or a crash between the two writes). The sentinel turns that into a stamp mismatch and a full rebuild instead of an exception out of `load_store`. The test checks both kinds of unreadable path, a missing file and a directory, so the behavior does not depend on which exception a particular read raises."""
    assert unit_cache._manifest_stamp(tmp_path) == "missing"
    assert unit_cache._sha256_file(tmp_path / "manifest.json") == "missing"
    assert unit_cache._sha256_file(tmp_path) == "missing"
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    assert unit_cache._manifest_stamp(tmp_path) != "missing"
    assert unit_cache._sha256_file(tmp_path / "manifest.json") != "missing"


def _round_trip_unit() -> unit_cache.CachedUnit:
    return unit_cache.CachedUnit(
        key="k1",
        prior_id="u-0001",
        prior_class="split-by-space-or-zwnj",
        content_key="f" * 64,
        slim=False,
        address=None,
        ink_identical=False,
        picture_identical=False,
        junior_equivalent=False,
        ink_deltas={"default": "d-0123456789ab"},
        diffs_digest="deadbeef",
        cluster="c-12345678",
        change_group="same-joins-narrower",
        ledger_class="identical-variant-name",
        pair_codepoints=(1, 2),
        proj={
            "pair": [0, 1],
            "after_spans": [[0, 1], [1, 2]],
            "after_cells": ["c", "d"],
            "after_junctions": ["y5"],
            "before_spans": [[0, 1], [1, 2]],
            "before_glyphs": ["a", "b"],
            "before_junctions": ["break"],
        },
        junctions=[
            {
                "pair": [1, 2],
                "before": {"x_min": 0, "x_max": 5, "advance_total": 9},
                "after": {"x_min": 1, "x_max": 6, "advance_total": 9},
            }
        ],
        mismatches=[],
        duplicate_group="e-2WvdGAWe6bX",
        exemplar=False,
        no_verdict=False,
        primary_units=[["u-DdcTojn1hba", False]],
        policy_file="glyph_data/runes/qsTea.yaml",
    )


def _round_trip_cached() -> unit_cache.ParsedCachedUnit:
    """The `ParsedCachedUnit` that `_round_trip_unit()`'s store line parses to."""
    return unit_cache.ParsedCachedUnit(
        key="k1",
        prior_id="u-0001",
        prior_class="split-by-space-or-zwnj",
        content_key="f" * 64,
        slim=False,
        address=None,
        ink_identical=False,
        picture_identical=False,
        junior_equivalent=False,
        ink_deltas={"default": "d-0123456789ab"},
        diffs_digest="deadbeef",
        cluster="c-12345678",
        change_group="same-joins-narrower",
        ledger_class="identical-variant-name",
        pair_codepoints=(1, 2),
        duplicate_group="e-2WvdGAWe6bX",
        exemplar=False,
        no_verdict=False,
        primary_units=[["u-DdcTojn1hba", False]],
        policy_file="glyph_data/runes/qsTea.yaml",
        junction_rects=[
            {
                "pair": [1, 2],
                "before": {"x_min": 0, "x_max": 5, "advance_total": 9},
                "after": {"x_min": 1, "x_max": 6, "advance_total": 9},
            }
        ],
        mismatches=[],
        pair=(0, 1),
        after_spans=((0, 1), (1, 2)),
        after_cells=("c", "d"),
        after_junctions=("y5",),
        before_spans=((0, 1), (1, 2)),
        before_glyphs=("a", "b"),
        before_junctions=("break",),
        junction_pairs=((1, 2),),
    )


def test_store_round_trip_and_invalidation(tmp_path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    cached = _round_trip_unit()
    unit_cache.write_store(tmp_path, "env-a", [cached])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == _round_trip_cached()
    slim = replace(cached, key="k2", slim=True)
    unit_cache.write_store(tmp_path, "env-a", [cached, slim])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k2"].slim is True and loaded["k1"].slim is False
    assert unit_cache.load_store(tmp_path, "env-b") is None
    (tmp_path / "manifest.json").write_text('{"changed": true}', encoding="utf-8")
    assert unit_cache.load_store(tmp_path, "env-a") is None


def test_a_store_line_and_its_record_are_inverses_down_to_the_bytes(tmp_path):
    """Two properties let the build leave in place a previous store's line for a record it would write unchanged: the store's lines, read back in its materialized order, are the lines written for its records, in order; and a line written for a record loads as the cached form of that record. A deflated fragment's record (#618's change 35) writes its inflated length as its address's fourth element (decision 76), and only it does: a plain fragment's address keeps three elements, so its line is the one a store before the change wrote (decision 77). Every reader of an address reads both: the parse, the line's slicer and the prior record the plan keeps by id."""
    first = _round_trip_unit()
    second = replace(first, key="k2", prior_id="u-8nacGTcgMRS", address=("units/small.json", 1, 5))
    line = unit_cache.record_line(first)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    unit_cache.write_store(tmp_path, "env-a", [first, second])
    assert _store_lines(tmp_path)[1] == [line, unit_cache.record_line(second)]
    unit_cache.write_store(tmp_path, "env-a", [line, second], parts=["units/small.json"])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == _round_trip_cached()
    assert loaded["k2"] == replace(_round_trip_cached(), key="k2", prior_id="u-8nacGTcgMRS")
    held = tmp_path / "held"
    (held / "units").mkdir(parents=True)
    (held / "units" / "small.json").write_bytes(b"[" + b" " * 30 + b"]")
    (held / "manifest.json").write_text("{}", encoding="utf-8")
    plain = _keyed_unit("plain", address=("units/small.json", 1, 5))
    deflated = _keyed_unit("deflated", address=("units/small.json", 7, 5), inflated=12)
    lines = [unit_cache.record_line(record) for record in (plain, deflated)]
    assert lines[0].endswith(b', "address": ["units/small.json", 1, 5]}\n')
    assert lines[1].endswith(b', "address": ["units/small.json", 7, 5, 12]}\n')
    assert (
        lines[0] == (json.dumps({**plain.to_record(), "address": ["units/small.json", 1, 5]}) + "\n").encode()
    )
    assert [unit_index.record_address(line) for line in lines] == [
        ("units/small.json", 1, 5, 0),
        ("units/small.json", 7, 5, 12),
    ]
    assert [(record.address, record.inflated) for record in map(unit_cache.prior_record, lines)] == [
        (("units/small.json", 1, 5), 0),
        (("units/small.json", 7, 5), 12),
    ]
    unit_cache.write_store(held, "env-a", [plain, deflated])
    assert _store_lines(held)[1] == lines
    loaded = unit_cache.load_store(held, "env-a")
    assert loaded is not None
    assert loaded[plain.key] == replace(
        _round_trip_cached(), key=plain.key, prior_id=plain.prior_id, address=plain.address
    )
    assert loaded[deflated.key] == replace(
        _round_trip_cached(),
        key=deflated.key,
        prior_id=deflated.prior_id,
        address=deflated.address,
        inflated=12,
    )
    found = [loaded[record.key].located() for record in (plain, deflated)]
    assert [(place.length, place.inflated) for place in found if place is not None] == [(5, 0), (5, 12)]


def test_a_record_keeps_its_address_only_while_its_part_is_the_size_the_store_recorded(tmp_path):
    """The address round-trips beside the stamp, and `load_store` drops it, keeping the record, once the part it points into is no longer the size the header recorded, which sends a rewritten part to the walk. A record written without an address loads without one."""
    fragments = [{"id": "u-0000", "content_key": "k0"}, {"id": "u-0001", "content_key": "f" * 64}]
    parts, spans = _write_shard(tmp_path, "small", fragments)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    addressed = replace(_round_trip_unit(), address=(parts[0], spans[1][1], spans[1][2]))
    unaddressed = replace(_round_trip_unit(), key="k2", prior_id="u-0002")
    unit_cache.write_store(tmp_path, "env-a", [addressed, unaddressed])
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == replace(_round_trip_cached(), address=addressed.address)
    assert loaded["k2"].address is None
    located = loaded["k1"].located()
    assert located is not None and (located.unit_id, located.content_key) == ("u-0001", "f" * 64)
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        assert reader.read(located) == fragments[1]
    (tmp_path / parts[0]).write_text(json.dumps(fragments), encoding="utf-8")
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None and loaded["k1"] == _round_trip_cached()
    assert loaded["k1"].located() is None


def test_an_assets_refresh_leaves_the_store_loadable(tmp_path):
    """The store is stamped with the manifest's identity, not its bytes, so rewriting `inputs_fingerprint.static` over a served corpus (all an assets refresh does) leaves the store valid for the shards beside it. Any other change to the manifest, such as a new `generated_at`, invalidates the store and costs the next build a full pass."""
    manifest = {"generated_at": "2026-01-01T00:00:00Z", "inputs_fingerprint": {"data": "d", "static": "s"}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    unit_cache.write_store(tmp_path, "env-a", [_round_trip_unit()])
    refreshed = {**manifest, "inputs_fingerprint": {"data": "d", "static": "refreshed"}}
    (tmp_path / "manifest.json").write_text(json.dumps(refreshed, indent=2), encoding="utf-8")
    assert unit_cache.load_store(tmp_path, "env-a") is not None
    (tmp_path / "manifest.json").write_text(
        json.dumps({**refreshed, "generated_at": "2099-01-01T00:00:00Z"}), encoding="utf-8"
    )
    assert unit_cache.load_store(tmp_path, "env-a") is None


def _prior_corpus(root: Path, classes: list[dict], *, legacy: bool) -> None:
    key = "shard" if legacy else "shards"
    entries = [{"id": meta["id"], key: meta["shards"][0] if legacy else meta["shards"]} for meta in classes]
    (root / "manifest.json").write_text(json.dumps({"classes": entries}), encoding="utf-8")


def _sliced(root: Path, located: unit_cache.PriorFragment) -> dict:
    """Read the fragment at a located address the way the browser's Range request and `PriorFragmentReader` both do: slice the part's bytes and parse the slice alone."""
    raw = (root / located.part).read_bytes()
    return json.loads(raw[located.start : located.start + located.length])


def _fragment(index: int, **fields) -> dict:
    """A fragment with a content-derived id and stamp, the forms the walk records."""
    return {"id": unit_cache.unit_id_for(_digest(index)), "content_key": _digest(1000 + index), **fields}


def _wanted(**classes: Iterable[dict]) -> unit_cache.WantedFragments:
    wanted = unit_cache.WantedFragments()
    for class_id, fragments in classes.items():
        for fragment in fragments:
            word = unit_cache.id_word(fragment["id"])
            assert word is not None
            wanted.add(class_id, word)
    return wanted


def _found(located: unit_cache.LocatedFragments, class_id: str, fragment: dict) -> unit_cache.PriorFragment:
    found = located.find(class_id, fragment["id"])
    assert found is not None
    return found


def test_locate_prior_fragments_addresses_a_class_the_last_build_split(tmp_path, monkeypatch):
    """The cache reads the prior manifest to find which files a class was written as, because a class large enough to be split has no single file name to guess. For each wanted record, its class and id word, it keeps the address and the stamp found there as columns (`unit_cache.LocatedFragments`), never the fragment, so the plan can be made without holding the previous corpus. A class the manifest does not list contributes nothing, as a missing file does, and neither does an id the records do not want."""
    monkeypatch.setattr("rebuild.review.build.SHARD_PART_BYTES", 32)
    fragments = [_fragment(index) for index in range(6)]
    parts, _spans = _write_shard(tmp_path, "big", fragments)
    assert len(parts) > 1
    _prior_corpus(tmp_path, [{"id": "big", "shards": parts}], legacy=False)
    located = unit_cache.locate_prior_fragments(
        tmp_path, _wanted(big=(fragments[1], fragments[5]), gone=(fragments[0],))
    )
    assert located.found() == 2
    assert located.find("big", fragments[0]["id"]) is None
    assert located.find("gone", fragments[0]["id"]) is None
    assert located.find("small", fragments[1]["id"]) is None
    for index in (1, 5):
        found = located.find("big", fragments[index]["id"])
        assert found is not None and found.part in parts
        assert (found.unit_id, found.content_key) == (fragments[index]["id"], fragments[index]["content_key"])
        assert _sliced(tmp_path, found) == fragments[index]
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        assert [reader.read(_found(located, "big", fragments[index])) for index in (1, 5)] == [
            fragments[1],
            fragments[5],
        ]


def test_locate_prior_fragments_reads_a_format_1_prior_corpus(tmp_path):
    """A prior corpus may still use `ams-review-manifest/1`, with one `shard` string per class, and the cache must read from it, or the next build re-enriches every unit it could have reused."""
    fragments = [{"id": _fragment(0)["id"]}, _fragment(1)]
    parts, _spans = _write_shard(tmp_path, "small", fragments)
    assert parts == ["units/small.json"]
    _prior_corpus(tmp_path, [{"id": "small", "shards": parts}], legacy=True)
    located = unit_cache.locate_prior_fragments(tmp_path, _wanted(small=fragments[1:]))
    found = located.find("small", fragments[1]["id"])
    assert located.found() == 1 and found is not None
    assert found.content_key == fragments[1]["content_key"]
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        assert reader.read(found) == fragments[1]


def test_locate_prior_fragments_with_no_manifest_contributes_nothing(tmp_path):
    """A first build, or a prior corpus deleted by hand, has no manifest; its units fall back to a recomputation instead of raising."""
    located = unit_cache.locate_prior_fragments(tmp_path, _wanted(small=[_fragment(0)]))
    assert located.found() == 0 and located.find("small", _fragment(0)["id"]) is None


def test_locate_prior_fragments_walks_a_shard_rewritten_by_hand(tmp_path):
    """The address is taken from the part's own text, not assumed from the writer's framing, so a shard rewritten compactly (the form `test_a_fragment_whose_stamp_moved_is_not_reused` leaves on disk) still locates every fragment, and the recorded bytes still parse to it. A fragment whose stamp is not a content key is not recorded, since no record could match it."""
    fragments = [_fragment(0), _fragment(1), {**_fragment(2), "content_key": "k2"}]
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "small.json").write_text(json.dumps(fragments), encoding="utf-8")
    _prior_corpus(tmp_path, [{"id": "small", "shards": ["units/small.json"]}], legacy=False)
    located = unit_cache.locate_prior_fragments(tmp_path, _wanted(small=fragments))
    assert located.found() == 2 and located.find("small", fragments[2]["id"]) is None
    assert [_sliced(tmp_path, _found(located, "small", fragment)) for fragment in fragments[:2]] == fragments[
        :2
    ]


def test_a_part_that_is_not_ascii_is_not_addressable(tmp_path):
    """A character offset equals a byte offset only under `ensure_ascii`, which every plain part this build writes uses and a hand-edited part may not. Instead of recording an address that would read the wrong bytes, the walk the locate pass takes where the store does not drive the iterator refuses such a part (`unit_index.LayoutError`), as it refuses a part holding a deflated fragment, and its units fall back to a recomputation."""
    fragments = [_fragment(0, notation="·Tea")]
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "small.json").write_text(
        json.dumps(fragments, ensure_ascii=False), encoding="utf-8"
    )
    _prior_corpus(tmp_path, [{"id": "small", "shards": ["units/small.json"]}], legacy=False)
    assert unit_cache.locate_prior_fragments(tmp_path, _wanted(small=fragments)).found() == 0


def test_the_reader_refuses_a_fragment_that_moved_under_its_address(tmp_path):
    """An address is recorded at plan time and read at write time. The shard writer keeps the previous corpus intact between the two by deferring its renames, and the reader checks every read against the id and stamp the address was located with, so a fragment that changes anyway raises an error instead of supplying another unit's bytes under this one's id. A deflated fragment (#618's change 35) is checked on its inflated bytes: `read` inflates it, `read_bytes` returns it as stored once its inflated bytes pass the check, and `same_bytes` compares its recorded inflated length and then its inflated bytes with the new fragment's plain bytes. A deflated stream cut short, one with bytes after its end, and one that inflates to another unit are each refused by both reads and compare unequal."""
    fragments = [_fragment(0), _fragment(1)]
    parts, _spans = _write_shard(tmp_path, "small", fragments)
    _prior_corpus(tmp_path, [{"id": "small", "shards": parts}], legacy=False)
    found = unit_cache.locate_prior_fragments(tmp_path, _wanted(small=fragments[1:])).find(
        "small", fragments[1]["id"]
    )
    assert found is not None
    _write_shard(
        tmp_path, "small", [fragments[0], {**_fragment(9), "content_key": fragments[1]["content_key"]}]
    )
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        with pytest.raises(ValueError, match="changed underneath"):
            reader.read(found)
    bodies = [review_build._fragment_bytes(fragment) for fragment in fragments]
    stored = [unit_index.deflate_fragment(body) for body in bodies]
    (tmp_path / "units" / "held.json").write_bytes(
        b"[" + stored[1] + b"," + stored[0] + unit_index.PART_CLOSING
    )
    held = unit_cache.PriorFragment(
        "units/held.json",
        1,
        len(stored[1]),
        fragments[1]["id"],
        fragments[1]["content_key"],
        byte_copied=True,
        inflated=len(bodies[1]),
    )
    with unit_cache.PriorFragmentReader(tmp_path) as reader:
        assert reader.read(held) == fragments[1]
        assert reader.read_bytes(held) == stored[1]
        assert reader.same_bytes(held, bodies[1])
        assert not reader.same_bytes(held, bodies[1][:-1] + b" ")
        assert not reader.same_bytes(held, bodies[1] + b" ")
        for refused in (
            replace(held, length=len(stored[1]) - 1),
            replace(held, length=len(stored[1]) + 1),
            replace(held, start=len(stored[1]) + 2, length=len(stored[0]), inflated=len(bodies[0])),
        ):
            with pytest.raises(ValueError, match="changed underneath"):
                reader.read(refused)
            with pytest.raises(ValueError, match="changed underneath"):
                reader.read_bytes(refused)
            assert not reader.same_bytes(refused, bodies[1])


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
    key = hashlib.sha256(b"a window").hexdigest()
    unit_id = unit_cache.unit_id_for(key)
    assert unit_id == "u-" + unit_cache.base58_64(key)
    assert unit_cache.is_content_id(unit_id)
    duplicate_group = unit_cache.duplicate_group_id_for(
        repr((("default",), (0xE650, 0xE652), "a-class", "deadbeef"))
    )
    assert duplicate_group.startswith("e-") and unit_cache.is_content_id(duplicate_group)
    assert duplicate_group != unit_cache.duplicate_group_id_for(
        repr((("default",), (0xE650, 0xE652), "a-class", "deadbeee"))
    )
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


def _keyed_unit(name: str, **fields) -> unit_cache.CachedUnit:
    return replace(
        _round_trip_unit(), key=hashlib.sha256(name.encode()).hexdigest(), prior_id=f"u-{name}", **fields
    )


def _update(directory: Path, stamp: fingerprint.EnvironmentStamp, records: list) -> unit_cache.StoreWrite:
    """Write `records`, in their order, as this pass's store over the one in `directory`, as the build's cache write does: each matched to its prior line by input key, with a new base when `stamp` moves a line that says every record is re-derived."""
    header, prior_lines = _store_lines(directory)
    places = {line[9:73].decode(): index for index, line in enumerate(prior_lines)}
    head = row_delta.read_head(directory, unit_cache.STORE_NAME)
    assert head is not None
    plan = unit_cache.StorePlan(
        True,
        True,
        head,
        len(prior_lines),
        head.base.rows_bytes,
        unit_cache.rederiving_moved(header, stamp),
    )
    lines = [unit_cache.record_line(record) for record in records]
    return unit_cache.update_store(
        directory,
        stamp,
        plan,
        range(len(records)),
        prior_line=lambda ordinal: places.get(records[ordinal].key, unit_cache.NO_PRIOR_LINE),
        unchanged=lambda _ordinal: False,
        line_of=lines.__getitem__,
        parts=(),
    )


def test_the_stores_materialized_form_is_the_file_a_whole_write_gives(tmp_path, monkeypatch):
    """The store a cached pass writes as segments over its previous one materializes (`unit_cache.materialized_store`) to the file `write_store` writes whole over the same records in the same order, byte for byte (#618's decision 48), whatever the pass changed: a unit removed, a unit whose class changed and whose triage place moved with it (a tombstone at its old place and an upsert at its new one), a record changed in place, a unit new to the corpus at the front and inside the order, and a second segment over the first. With the cap lowered to two segments, the next pass folds them into a new base, which is the materialized form again and moves the store's content generation only when a record changed, and a pass that changes nothing writes the head alone. A pass that changes every record, whose segment's lines would pass half the base's, writes a new base by the share rule (`row_delta.writes_base`). A pass whose stamp moves only the subset tables' line, as an old-font edit does, writes a segment of the records that changed, and one whose stamp moves the spec's structure, as a letter batch does, writes a new base though no record changed (`unit_cache.REDERIVING_LABELS`, #618's decision 72)."""
    monkeypatch.setattr(row_delta, "SEGMENT_CAP", 2)
    stamp = fingerprint.EnvironmentStamp(("format\tx", "spec_structure\ts", "subsets\ta"))
    refiltered = fingerprint.EnvironmentStamp(("format\tx", "spec_structure\ts", "subsets\tb"))
    restructured = fingerprint.EnvironmentStamp(("format\tx", "spec_structure\tt", "subsets\tb"))
    units = {f"{index:02d}": _keyed_unit(f"{index:02d}") for index in range(12)}
    first = list(units.values())
    moved = replace(units["05"], prior_class="identical-variant-name")
    second = [units["00"], units["01"], _keyed_unit("n1")] + [
        replace(unit, cluster="c-87654321") if name == "07" else unit
        for name, unit in units.items()
        if name not in ("00", "01", "03", "05")
    ]
    second.append(moved)
    third = [_keyed_unit("n2")] + [
        replace(unit, cluster="c-55555555") if unit.key == units["09"].key else unit for unit in second
    ]
    fourth = [replace(unit, cluster="c-11111111") for unit in third]
    fifth = [replace(unit, cluster="c-22222222") if unit.key == units["02"].key else unit for unit in fourth]
    store = tmp_path / "store"
    store.mkdir()
    (store / "manifest.json").write_text("{}", encoding="utf-8")
    unit_cache.write_store(store, stamp, first, parts=())
    passes = [
        (stamp, second),
        (stamp, third),
        (stamp, third),
        (stamp, third),
        (stamp, fourth),
        (refiltered, fifth),
        (restructured, fifth),
    ]
    outcomes = []
    heads = []
    for number, (pass_stamp, records) in enumerate(passes, start=1):
        outcomes.append(_update(store, pass_stamp, records))
        head = row_delta.read_head(store, unit_cache.STORE_NAME)
        assert head is not None
        heads.append(head)
        whole = tmp_path / f"whole-{number}"
        whole.mkdir()
        (whole / "manifest.json").write_text("{}", encoding="utf-8")
        unit_cache.write_store(whole, pass_stamp, records, parts=())
        materialized = unit_cache.materialized_store(store, sha256=True)
        assert materialized is not None
        form = tmp_path / f"form-{number}"
        form.mkdir()
        unit_cache.write_materialized_store(form, *materialized)
        expected = (whole / row_delta.base_name(unit_cache.STORE_NAME, 0)).read_bytes()
        assert (form / unit_cache.STORE_NAME).read_bytes() == expected
        if outcomes[-1].outcome == "base":
            assert (store / head.base.name).read_bytes() == expected
    assert [write.outcome for write in outcomes] == [
        "segment",
        "segment",
        "base",
        "head",
        "base",
        "segment",
        "base",
    ]
    assert [write.reason for write in outcomes if write.outcome == "base"] == ["fold", "share", "rederived"]
    assert outcomes[0].rows == 5 and outcomes[5].rows == 1
    assert (heads[4].generation, heads[4].content, heads[4].segments) == (2, 3, ())
    assert (heads[5].generation, len(heads[5].segments)) == (2, 1)
    assert (heads[6].generation, heads[6].segments) == (3, ())


def test_a_store_write_that_fails_before_its_head_leaves_the_previous_store_readable(tmp_path, monkeypatch):
    """The head is the store's commit point: a write that fails after its segment is on disk and before its head is renamed into place leaves the previous head, and every part it lists, readable as they were, and the segment it left, which no head lists, is deleted by the next write before it writes its own."""
    stamp = fingerprint.EnvironmentStamp(("format\tx",))
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    first = [_keyed_unit(f"{index:02d}") for index in range(6)]
    unit_cache.write_store(tmp_path, stamp, first, parts=())
    before = _store_lines(tmp_path)
    second = first[:3] + [replace(first[3], cluster="c-00000000")] + first[4:]
    real = row_delta.commit

    def crash(directory, head):
        raise OSError("the pass was killed")

    monkeypatch.setattr(row_delta, "commit", crash)
    with pytest.raises(OSError, match="killed"):
        _update(tmp_path, stamp, second)
    orphan = tmp_path / row_delta.segment_name(unit_cache.STORE_NAME, 0, 1)
    assert orphan.is_file()
    assert _store_lines(tmp_path) == before
    assert unit_cache.load_store(tmp_path, stamp) is not None
    monkeypatch.setattr(row_delta, "commit", real)
    removed = []
    real_remove = row_delta.remove_unlisted

    def spy(directory, output, head):
        found = real_remove(directory, output, head)
        removed.extend(found)
        return found

    monkeypatch.setattr(row_delta, "remove_unlisted", spy)
    assert _update(tmp_path, stamp, second).outcome == "segment"
    assert removed[0] == orphan.name
    assert _store_lines(tmp_path)[1] == [unit_cache.record_line(record) for record in second]


def test_a_build_puts_a_single_file_store_under_a_head_before_it_appends(
    mini_corpus, mini_bundle, edited_scratch, tmp_path, monkeypatch
):
    """A store written before the segment format, the single file `unit-cache.ndjson.gz` with no head, is put under a head as generation 0's base, a link to the file, as soon as the plan has read it (`unit_cache.adopt_single_file`), before the write appends to any shard part. So a build that appends and then fails before its store write leaves a corpus with a head, which the iterator never walks part by part, so it never takes the dead slot the append left as live. It takes the corpus from the index, whose walk as JSON cannot read the append, held deflated (#618's change 35), so it refuses the corpus (`unit_index.LayoutError`) rather than read it wrong, and the next build completes it into the from-scratch build's materialized form and removes the single file."""
    audits, scratches = edited_scratch
    corpus = _copy(mini_corpus, tmp_path)
    head = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    assert head is not None and not head.segments
    single = unit_cache.store_path(corpus)
    shutil.copyfile(corpus / head.base.name, single)
    row_delta.head_path(corpus, unit_cache.STORE_NAME).unlink()
    row_delta.remove_unlisted(corpus, unit_cache.STORE_NAME, None)
    legacy = single.read_bytes()

    def killed(*_args, **_kwargs):
        raise OSError("the pass was killed")

    real = unit_cache.update_store
    monkeypatch.setattr(unit_cache, "update_store", killed)
    with pytest.raises(OSError, match="killed"):
        _build(corpus, mini_bundle, audit_path=audits[0], jobs=1)
    adopted = row_delta.read_head(corpus, unit_cache.STORE_NAME)
    assert adopted is not None and (adopted.generation, adopted.segments) == (0, ())
    assert (corpus / adopted.base.name).read_bytes() == legacy == single.read_bytes()
    assert _overflow_parts(corpus) == [f"{RETAG_CLASS}.x000.json"]
    with unit_index.LiveCorpus(corpus) as live:
        assert live.source == "index"
        with pytest.raises(unit_index.LayoutError, match="not ASCII"):
            list(live.fragments())
    monkeypatch.setattr(unit_cache, "update_store", real)
    _build(corpus, mini_bundle, audit_path=audits[0], jobs=1)
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
    """A class whose dead bytes reach its live bytes on disk as the pass opens is folded: its live fragments rewritten in id order into base parts cut and named by the rule a from-scratch build follows (`unit_index.PartLayout`, `unit_index.base_part_names`), over their bytes on disk, its overflow parts left unlisted and pruned. A fold writes deflated (#618's change 35): a fragment the class held deflated is copied in as it was stored, never deflated twice, and a plain one is deflated. Here `RETAG_CLASS` holds two retagged units appended deflated beside its plain base part, and copies of that part as overflow parts no record names, so its dead bytes reach its live bytes (`_fold_due`); the pass that folds it moves one retagged unit back out. The cap is lowered below the class's deflated bytes, so the fold crosses from the bare name to numbered parts. The files on disk differ from a from-scratch build's (decision 48), but the corpus holds that build's fragments by id and materializes to its files byte for byte."""
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
            else unit_index.deflate_fragment(plain)
        )
        for unit_id, (slot, plain) in before.items()
    }
    cap = max(max(map(len, deflated.values())) + 8, sum(len(body) + 1 for body in deflated.values()) * 2 // 3)
    _fold_due(corpus, RETAG_CLASS)
    monkeypatch.setattr(review_build, "SHARD_PART_BYTES", cap)
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
    """A class one of whose parts is gone from disk is written directly as a first base (`build.FIRST`), plain throughout, as a from-scratch build writes it (#618's change 35): a cached fragment a surviving overflow part holds deflated is read, its inflated bytes checked, and copied in plain, and the units whose part is gone are recomputed. Its part is the from-scratch build's byte for byte, its records and rows carry no marker, and every other class keeps the form it had."""
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
