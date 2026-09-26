"""Tests for the review-facts sidecar (`rebuild/review/facts.py`): projecting a corpus build's post-merge phase-1 products back onto the pre-merge grain the pins are defined over, the two in-memory functions that must match their shard- or font-reading counterparts, reading and writing the sidecar, and the CLI paths that read it.

The tests use hand-built audit rows and hand-set ink verdicts and unmatched groups, with no fonts, no shaping, and no live workload; the only live input is the checked-in divergence ledger. A failure here is a bug in the derivation, not a change in the corpus.
"""

import json
from pathlib import Path

import pytest

from rebuild.review import facts, unit_store
from rebuild.review.audit import (
    AuditRow,
    UnitTable,
    Workload,
    build_units,
    load_ledger,
    load_table,
    merge_ink_duplicate_units,
)
from rebuild.review.facts import (
    FACTS_FORMAT,
    REFERENCE_WINDOW_CODEPOINTS,
    PremergeFacts,
    build_facts,
    built_group,
    built_group_from_memory,
    capture_premerge,
    derive_premerge,
    ink_group_from_flags,
    ink_histogram,
    invariant_delta,
    invariant_diff,
    invariant_group,
    ledger_coverage,
    load_facts,
    workload_digest,
    write_facts,
)
from rebuild.review.audit import LedgerClass
from rebuild.review.enrich import LETTERS
from rebuild.review.unit_store import UnitStore

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"

MERGE_WINDOW = "E650:E665"
DEFERRED_WINDOW = "E651:E665"
MIXED_WINDOW = "E652:E665"
STANDALONE_UNMATCHED = "E653:E653"
STANDALONE_MATCHED = "E654:E654"


def _text(unit) -> str:
    return "".join(chr(value) for value in unit.codepoint_values)


def _row(config: str, codepoints: str, matched: str, baseline: tuple[str, ...]) -> AuditRow:
    return AuditRow(
        config=config,
        codepoints=codepoints,
        kinds=("cell",),
        matched_entry=matched,
        baseline=baseline,
        new=("after",),
    )


def _index_of(capture: facts.PremergeSnapshot, codepoints: str, config: str) -> int:
    return next(
        index
        for index, grain in enumerate(capture.grains())
        if grain.codepoints == codepoints and config in grain.configs
    )


def _merged_table(rows: list[AuditRow]) -> tuple[facts.PremergeSnapshot, UnitTable, UnitStore]:
    """Load `rows` against the live ledger, capture the pre-merge snapshot, merge with an ink signature that makes every config of a window identical, then compact the table and rebase the snapshot onto it. Returns the snapshot, the table, and an empty store sized to the compacted table."""
    ledger = load_ledger(LEDGER_PATH)
    table, columns = load_table(rows, ledger, dict(LETTERS))
    capture = capture_premerge(table)
    exempt = {entry.id for entry in ledger if entry.no_verdict}
    merge_ink_duplicate_units(table, columns, lambda text, config: text, exempt)
    capture.rebase(table.compact())
    return capture, table, UnitStore(table.n, strings=table.strings)


def _merged_fixture():
    """Five windows covering every case the projection handles: a default-reachable UNMATCHED survivor absorbing a relabeled ss04 sibling, two stylistic-set-only UNMATCHED siblings that defer to different buckets, a no-verdict matched unit absorbing an UNMATCHED sibling, and two standalone units that never merge. Returns the pre-merge snapshot, the compacted table, and a store over it, with phase 1's products set by hand on the survivors' rows."""
    rows = [
        _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss03", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss04", MERGE_WINDOW, "UNMATCHED", ("qsPea.ss04", "qsMay")),
        _row("ss03", DEFERRED_WINDOW, "UNMATCHED", ("qsBay", "qsMay")),
        _row("ss04", DEFERRED_WINDOW, "UNMATCHED", ("qsBay.ss04", "qsMay")),
        _row("default", MIXED_WINDOW, "boundary-window", ("qsTea", "qsMay")),
        _row("ss04", MIXED_WINDOW, "UNMATCHED", ("qsTea.ss04", "qsMay")),
        _row("default", STANDALONE_UNMATCHED, "UNMATCHED", ("qsDay", "qsDay")),
        _row("default", STANDALONE_MATCHED, "dangling-anchor-dropped", ("qsKey", "qsKey")),
    ]
    capture, table, store = _merged_table(rows)
    verdicts = {
        MERGE_WINDOW: (True, "no-chain-gains"),
        DEFERRED_WINDOW: (False, "deferred-ss04"),
        MIXED_WINDOW: (True, "unmatched-misc"),
        STANDALONE_UNMATCHED: (False, "junction-loss-unjoined"),
        STANDALONE_MATCHED: (True, ""),
    }
    assert table.n == 5
    for ordinal in range(table.n):
        ink_identical, group = verdicts[table.codepoints_text(ordinal)]
        if ink_identical:
            store._flags[ordinal] |= unit_store.INK_IDENTICAL
        table.set_unmatched_group(ordinal, group)
    return capture, table, store


def test_merged_siblings_take_their_survivors_ink_verdict():
    """A merge happens only when every config of every merged sibling renders identical ink, so the survivor's ink verdict applies to the whole window. Each captured sibling reports its survivor's flag, and the units that never merged report their own."""
    capture, table, store = _merged_fixture()
    premerge = derive_premerge(capture, table, store)
    flags = premerge.ink_flags
    assert premerge.units == len(capture) == len(flags) == 8
    assert premerge.workload_digest == workload_digest(capture.grains())
    assert flags[_index_of(capture, MERGE_WINDOW, "default")] == "1"
    assert flags[_index_of(capture, MERGE_WINDOW, "ss04")] == "1"
    assert flags[_index_of(capture, DEFERRED_WINDOW, "ss03")] == "0"
    assert flags[_index_of(capture, DEFERRED_WINDOW, "ss04")] == "0"
    assert flags[_index_of(capture, MIXED_WINDOW, "default")] == "1"
    assert flags[_index_of(capture, MIXED_WINDOW, "ss04")] == "1"
    assert flags[_index_of(capture, STANDALONE_UNMATCHED, "default")] == "0"
    assert flags[_index_of(capture, STANDALONE_MATCHED, "default")] == "1"


def test_unmatched_groups_read_deferral_from_the_premerge_config_classes():
    """A pre-merge UNMATCHED unit's unmatched group is its own deferred bucket when it has one, and otherwise its survivor's phase-1 group. The bucket is decided from the pre-merge config classes: the ss03-only survivor stays deferred-ss03, although the merged unit with its ss04 sibling would be deferred-ss04. A matched unit gets no unmatched group."""
    capture, table, store = _merged_fixture()
    premerge = derive_premerge(capture, table, store)
    assert dict(premerge.unmatched_groups) == {
        _index_of(capture, MERGE_WINDOW, "default"): "no-chain-gains",
        _index_of(capture, MERGE_WINDOW, "ss04"): "deferred-ss04",
        _index_of(capture, DEFERRED_WINDOW, "ss03"): "deferred-ss03",
        _index_of(capture, DEFERRED_WINDOW, "ss04"): "deferred-ss04",
        _index_of(capture, MIXED_WINDOW, "ss04"): "deferred-ss04",
        _index_of(capture, STANDALONE_UNMATCHED, "default"): "junction-loss-unjoined",
    }
    assert [index for index, _group in premerge.unmatched_groups] == sorted(
        index for index, _group in premerge.unmatched_groups
    )
    matched = {_index_of(capture, MIXED_WINDOW, "default"), _index_of(capture, STANDALONE_MATCHED, "default")}
    assert matched.isdisjoint(index for index, _group in premerge.unmatched_groups)


def test_derive_premerge_reads_each_merged_rows_survivor_off_the_compaction():
    """A captured row that the merge removed reads through the survivor the compaction recorded for it, which is the earliest-config sibling's post-merge row. `derive_premerge` raises on a snapshot that was never rebased, and `rebase` raises on a compaction of a different size."""
    rows = [
        _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss04", MERGE_WINDOW, "UNMATCHED", ("qsPea.ss04", "qsMay")),
    ]
    table, columns = load_table(rows, load_ledger(LEDGER_PATH), dict(LETTERS))
    capture = capture_premerge(table)
    merge_ink_duplicate_units(table, columns, lambda text, config: text)
    with pytest.raises(ValueError, match="rebased"):
        derive_premerge(capture, table, UnitStore(table.n))
    capture.rebase(table.compact())
    assert capture.survivor is not None and capture.removed is not None
    removed = _index_of(capture, MERGE_WINDOW, "ss04")
    assert list(capture.removed) == [1 if index == removed else 0 for index in range(2)]
    assert list(capture.survivor) == [0, 0] and table.n == 1
    with pytest.raises(ValueError, match="against 2 captured"):
        capture.rebase(table.compact())


def test_derive_premerge_refuses_an_unmatched_unit_with_no_group():
    """Every pre-merge UNMATCHED unit must have an unmatched group. `derive_premerge` raises on an undeferred one whose survivor has no phase-1 group instead of recording an empty group."""
    capture, table, store = _merged_table(
        [_row("default", STANDALONE_UNMATCHED, "UNMATCHED", ("qsDay", "qsDay"))]
    )
    with pytest.raises(ValueError, match=STANDALONE_UNMATCHED):
        derive_premerge(capture, table, store)


class _Comparator:
    """An ink comparator that returns a preset verdict per window, so the test checks the histogram's counting without fonts."""

    def __init__(self, verdicts: dict[str, bool]):
        self._verdicts = verdicts

    def ink_identical(self, text: str, configs) -> bool:
        return self._verdicts[text]


def test_ink_group_from_flags_mirrors_the_histogram():
    """`ink_group_from_flags` and `ink_histogram` must agree on keys, counts, and the insertion order of `by_class`. The first writes the pins, and the second is the only independent computation of them."""
    classes = ["boundary-window", "dangling-anchor-dropped", "UNMATCHED"]
    identical = [True, True, False, False, True, False, False, True, False, False]
    rows = [
        _row("default", f"E65{index:X}:E665", classes[index % 3], (f"q{index}",))
        for index in range(len(identical))
    ]
    ledger = load_ledger(LEDGER_PATH)
    table, _rows = load_table(rows, ledger, dict(LETTERS))
    units = table.units()
    verdicts = {_text(unit): flag for unit, flag in zip(units, identical, strict=True)}
    flags = "".join("1" if verdicts[_text(unit)] else "0" for unit in units)
    snapshot = capture_premerge(table)
    assert [(unit.class_id, unit.no_verdict) for unit in units] == list(snapshot.class_rows())

    workload = Workload(table=table, ledger=ledger, row_count=len(rows))
    assert ink_group_from_flags(snapshot.class_rows(), flags) == ink_histogram(
        workload, _Comparator(verdicts)
    )


def test_workload_digest_tracks_order_and_configs():
    """The digest shows that a flag string is indexed against the workload a reader loaded, so it must change when the unit order or a unit's config set changes. Either change would misalign every index after it."""
    units, _rows = build_units(
        [
            _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
            _row("default", STANDALONE_UNMATCHED, "UNMATCHED", ("qsDay", "qsDay")),
            _row("default", STANDALONE_MATCHED, "dangling-anchor-dropped", ("qsKey", "qsKey")),
        ],
        load_ledger(LEDGER_PATH),
        dict(LETTERS),
    )
    base = workload_digest(units)
    assert workload_digest([units[1], units[0], units[2]]) != base
    units[0].configs = (*units[0].configs, "ss10")
    assert workload_digest(units) != base


def test_the_snapshots_grains_digest_as_the_materialized_units_do():
    """The snapshot's grains carry the same window, class, no-verdict flag, and configs, in row order, as the units the table materializes, so both give the same digest. The sidecar carries this digest."""
    table, _rows = load_table(
        [
            _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
            _row("ss03", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
            _row("default", STANDALONE_MATCHED, "boundary-window", ("qsKey", "qsKey")),
        ],
        load_ledger(LEDGER_PATH),
        dict(LETTERS),
    )
    snapshot = capture_premerge(table)
    assert workload_digest(snapshot.grains()) == workload_digest(table.units())
    assert [grain.no_verdict for grain in snapshot.grains()] == [unit.no_verdict for unit in table.units()]
    assert any(grain.no_verdict for grain in snapshot.grains())


def _example_table() -> tuple[UnitTable, dict[int, str | None], list[dict]]:
    """A table of four windows with order, batch, and duplicate group set as the whole-corpus passes set them: three human units in two duplicate groups, the reference window among them, and one machine-approved unit outside the index. Returns the table, each unit's config note by ordinal, and the shard records the same units would be written as."""
    windows = {
        REFERENCE_WINDOW_CODEPOINTS: (0, "e-0000", None),
        "E670:E653:E652:E650": (0, "e-0000", None),
        "E670:E653:E652:E651": (1, "e-0001", "only under ss10"),
        "E650:E651": (None, None, "only when ss03 is on"),
    }
    table, _rows = load_table(
        [_row("default", codepoints, "boundary-window", ("q",)) for codepoints in windows],
        load_ledger(LEDGER_PATH),
        dict(LETTERS),
    )
    config_notes: dict[int, str | None] = {}
    records: list[dict] = []
    for ordinal in range(table.n):
        batch, duplicate_group, note = windows[table.codepoints_text(ordinal)]
        table.set_order_batch(ordinal, None if batch is None else ordinal, batch)
        table.set_duplicate_group(ordinal, duplicate_group)
        config_notes[ordinal] = note
        records.append(
            {
                "ink_identical": batch is None,
                "picture_identical": False,
                "junior_equivalent": False,
                "no_verdict": False,
                "duplicate_group": duplicate_group,
                "codepoints": table.codepoints_text(ordinal),
                "config_note": note,
            }
        )
    return table, config_notes, records


def _write_shard(root: Path, records: list[dict]) -> dict:
    """Write a minimal corpus with only what the review-facts reduction reads: the three classes `manifest_group` looks up by name (`CLASS_UNIT_COUNT_KEYS`) with the no-verdict flags and machine-approved histogram `invariant_group` reads, one shard holding `records`, and the manifest fields the sidecar copies into its stamp."""
    (root / "units").mkdir(parents=True, exist_ok=True)
    ids = ["boundary-window", "dangling-anchor-dropped", "bare-name-live-join"]
    for position, class_id in enumerate(ids):
        payload = records if position == 0 else []
        (root / "units" / f"{class_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0000000",
        "inputs_fingerprint": {"data": "x"},
        "totals": {"units": len(records), "rows": len(records), "batches": 1, "duplicate_groups": 1},
        "classes": [
            {
                "id": class_id,
                "shards": [f"units/{class_id}.json"],
                "unit_count": len(records) if position == 0 else 0,
                "no_verdict": class_id == "boundary-window",
            }
            for position, class_id in enumerate(ids)
        ],
        "machine_approved": {"units": 3, "by_class": {"bare-name-live-join": 2, "boundary-window": 1}},
        "secondary_junctions": {"units_with_markers": 0, "junctions_with_primary_unit": 0},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_built_group_from_memory_mirrors_the_shard_walk(tmp_path):
    """`built_group_from_memory` over the table and config notes must equal `built_group` over the shards written from them: the same human unit count, duplicate-sibling count for the reference window, and encoded config-note histogram."""
    table, config_notes, records = _example_table()
    manifest = _write_shard(tmp_path, records)
    assert built_group_from_memory(table, config_notes) == built_group(tmp_path, manifest)
    assert built_group_from_memory(table, config_notes)["reference_window_duplicate_siblings"] == 2


def test_built_group_reports_a_missing_reference_window_as_none(tmp_path):
    """When the reference window is not a human unit, as on every mini corpus a test builds, both functions report its duplicate-sibling count as None instead of failing. On the live corpus, the pins diff shows the loss as an accepted count replaced by null."""
    table, config_notes, records = _example_table()
    reference = next(
        ordinal for ordinal in range(table.n) if table.codepoints_text(ordinal) == REFERENCE_WINDOW_CODEPOINTS
    )
    table.set_order_batch(reference, None, None)
    records[reference]["ink_identical"] = True
    manifest = _write_shard(tmp_path, records)
    from_memory = built_group_from_memory(table, config_notes)
    assert from_memory["reference_window_duplicate_siblings"] is None
    assert from_memory == built_group(tmp_path, manifest)


def _pins(row_count: int) -> dict:
    """A small pin set in the checked-in file's two-block shape: an invariant block over three classes, and two volatile groups."""
    return {
        "invariant": {
            "classes": ["boundary-window", "a", "b"],
            "machine_approved_classes": ["a"],
            "no_verdict_classes": ["boundary-window"],
            "unmatched_groups": ["no-chain-gains"],
        },
        "volatile": {
            "audit": {"row_count": row_count, "units": 1},
            "unmatched_groups": {"census": {"no-chain-gains": 1}, "total": 1},
        },
    }


def _facts(pins: dict, generated_at: str = "2026-01-01T00:00:00Z") -> dict:
    manifest = {
        "generated_at": generated_at,
        "repo_head": "0000000",
        "inputs_fingerprint": {"data": "x"},
    }
    return {
        "format": FACTS_FORMAT,
        "corpus": manifest,
        "pins": pins,
        "premerge": {"units": 0, "workload_digest": "", "ink_identical": "", "unmatched_groups": []},
    }


def test_facts_round_trip(tmp_path):
    written = _facts(_pins(row_count=2))
    write_facts(tmp_path, written)
    assert (tmp_path / facts.FACTS_FILENAME).read_text(encoding="utf-8").endswith("}\n")
    assert load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"}) == written


def test_load_facts_refuses_a_missing_wrong_format_or_orphaned_sidecar(tmp_path):
    """`load_facts` accepts only the corpus's own sidecar. It raises on a missing file, an unknown format, or a `generated_at` that differs from the manifest's, since then the sidecar and the corpus came from different builds."""
    with pytest.raises(ValueError, match="rebuild.review.build"):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    stale = _facts({})
    stale["format"] = "ams-review-facts/0"
    write_facts(tmp_path, stale)
    with pytest.raises(ValueError, match=FACTS_FORMAT):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    write_facts(tmp_path, _facts({}, generated_at="2026-01-01T00:00:00Z"))
    with pytest.raises(ValueError, match="2026-02-02T00:00:00Z"):
        load_facts(tmp_path, {"generated_at": "2026-02-02T00:00:00Z"})


def test_invariant_group_keeps_each_sources_own_order():
    """The invariant block keeps each source's order: the classes and the no-verdict classes in manifest class order, the machine-approved classes in the order of the manifest's `by_class` histogram, and the unmatched groups in the order `unmatched_group_counts` emits them (`UNMATCHED_GROUP_ORDER`). A block whose order changed on every pass would make `invariant_delta` report reorders that mean nothing."""
    manifest = {
        "classes": [
            {"id": "boundary-window", "no_verdict": True},
            {"id": "bare-name-live-join", "no_verdict": False},
            {"id": "halves-entry-extension-restored", "no_verdict": True},
        ],
        "machine_approved": {"units": 5, "by_class": {"bare-name-live-join": 3, "boundary-window": 2}},
    }
    assert invariant_group(manifest, {"no-chain-gains": 8, "deferred-ss03": 1}) == {
        "classes": ["boundary-window", "bare-name-live-join", "halves-entry-extension-restored"],
        "machine_approved_classes": ["bare-name-live-join", "boundary-window"],
        "no_verdict_classes": ["boundary-window", "halves-entry-extension-restored"],
        "unmatched_groups": ["no-chain-gains", "deferred-ss03"],
    }


def test_build_facts_reduces_its_own_premerge_records(tmp_path):
    """The sidecar's pins are reductions of the pre-merge records beside them, so a reader can recompute them from those records. The invariant block is reduced from the same manifest and unmatched-group counts as the volatile groups, so the two blocks cannot disagree."""
    table, config_notes, records = _example_table()
    manifest = _write_shard(tmp_path, records)
    capture = capture_premerge(
        load_table(
            [
                _row("default", MERGE_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
                _row("default", STANDALONE_MATCHED, "boundary-window", ("qsKey", "qsKey")),
            ],
            load_ledger(LEDGER_PATH),
            dict(LETTERS),
        )[0]
    )
    premerge = PremergeFacts(
        units=len(capture),
        workload_digest=workload_digest(capture.grains()),
        ink_flags="10",
        unmatched_groups=[(_index_of(capture, MERGE_WINDOW, "default"), "no-chain-gains")],
    )
    built = build_facts(manifest, table, config_notes, capture, premerge, row_count=2)
    assert built["format"] == FACTS_FORMAT
    assert built["corpus"]["generated_at"] == manifest["generated_at"]
    volatile = built["pins"]["volatile"]
    assert volatile["audit"] == {"row_count": 2, "units": 2}
    assert volatile["built"] == built_group(tmp_path, manifest)
    assert volatile["unmatched_groups"] == {"census": {"no-chain-gains": 1}, "total": 1}
    assert volatile["ink"] == ink_group_from_flags(capture.class_rows(), "10")
    assert built["pins"]["invariant"] == {
        "classes": [meta["id"] for meta in manifest["classes"]],
        "machine_approved_classes": ["bare-name-live-join", "boundary-window"],
        "no_verdict_classes": ["boundary-window"],
        "unmatched_groups": ["no-chain-gains"],
    }
    assert built["premerge"]["ink_identical"] == "10"
    assert built["premerge"]["workload_digest"] == workload_digest(capture.grains())


def _cli_corpus(tmp_path: Path, pins: dict) -> Path:
    """Write a corpus for the review-facts CLI: a manifest whose class list and machine-approved histogram agree with the invariant block of `pins`, and a sidecar carrying `pins`."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    invariant = pins["invariant"]
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0000000",
        "classes": [
            {"id": identifier, "no_verdict": identifier in invariant["no_verdict_classes"]}
            for identifier in invariant["classes"]
        ],
        "machine_approved": {
            "units": len(invariant["machine_approved_classes"]),
            "by_class": {identifier: 1 for identifier in invariant["machine_approved_classes"]},
        },
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_facts(corpus, _facts(pins))
    return corpus


def test_check_reads_the_sidecar_and_reports_per_key_mismatches(tmp_path, monkeypatch, capsys):
    """`--check --corpus DIR` compares that corpus's pins with the checked-in pins and prints one line per changed key. Both blocks are compared, so each key name starts with its block."""
    pins_path = tmp_path / "pins.json"
    monkeypatch.setattr(facts, "PINS_PATH", pins_path)
    corpus = _cli_corpus(tmp_path, _pins(row_count=2))

    pins_path.write_text(json.dumps(_pins(row_count=2)), encoding="utf-8")
    assert facts.main(["--check", "--corpus", str(corpus)]) == 0

    pins_path.write_text(json.dumps(_pins(row_count=1)), encoding="utf-8")
    assert facts.main(["--check", "--corpus", str(corpus)]) == 1
    assert "  volatile.audit.row_count: pinned 1 != computed 2" in capsys.readouterr().err.splitlines()


def test_update_copies_the_sidecars_volatile_block_and_reduces_the_invariant_again(tmp_path, monkeypatch):
    """`--update` copies the sidecar's volatile block into the pins file unchanged and recomputes the invariant block from the corpus's manifest and the sidecar's unmatched-group counts. The file gets the invariant block's current shape even when an older build wrote the sidecar with a different one."""
    pins_path = tmp_path / "pins.json"
    monkeypatch.setattr(facts, "PINS_PATH", pins_path)
    monkeypatch.setattr(facts, "REPO_ROOT", tmp_path)
    pins = _pins(row_count=2)
    stale = {"invariant": {"classes_count": 3}, "volatile": pins["volatile"]}
    corpus = _cli_corpus(tmp_path, pins)
    write_facts(corpus, _facts(stale))
    assert facts.main(["--update", "--corpus", str(corpus)]) == 0
    assert json.loads(pins_path.read_text(encoding="utf-8")) == pins


def test_from_scratch_recomputes_from_sources_without_the_sidecar(tmp_path, monkeypatch):
    """`compute_pins(from_scratch=True)`, the `--from-scratch` path, recomputes the pre-merge groups from the source artifacts without reading review-facts.json, which this test makes unparsable. It returns the same two-block shape, with the invariant block's unmatched groups taken from the recomputed unmatched_groups group."""
    _table, _config_notes, records = _example_table()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    manifest = _write_shard(corpus, records)
    (corpus / facts.FACTS_FILENAME).write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(facts, "audit_group", lambda repo_root=REPO_ROOT: {"audit": "sentinel"})
    monkeypatch.setattr(facts, "ink_group", lambda repo_root=REPO_ROOT: {"ink": "sentinel"})
    monkeypatch.setattr(
        facts,
        "unmatched_groups_group",
        lambda repo_root=REPO_ROOT: {"census": {"junction-loss-unjoined": 3}, "total": 3},
    )

    pins = facts.compute_pins(corpus=corpus, from_scratch=True)
    volatile = pins["volatile"]
    assert volatile["audit"] == {"audit": "sentinel"}
    assert volatile["ink"] == {"ink": "sentinel"}
    assert volatile["unmatched_groups"] == {"census": {"junction-loss-unjoined": 3}, "total": 3}
    assert volatile["built"] == built_group(corpus, manifest)
    assert pins["invariant"] == {
        "classes": [meta["id"] for meta in manifest["classes"]],
        "machine_approved_classes": ["bare-name-live-join", "boundary-window"],
        "no_verdict_classes": ["boundary-window"],
        "unmatched_groups": ["junction-loss-unjoined"],
    }


def _ledger_entry(identifier: str, *, ink_identical: bool = False, no_verdict: bool = False) -> LedgerClass:
    return LedgerClass(
        id=identifier,
        status="accepted",
        why="",
        ink_identical=ink_identical,
        no_verdict=no_verdict,
        count=0,
        exemplar_keys=frozenset(),
    )


_ACCEPTED_INVARIANT = {
    "classes": ["boundary-window", "bare-name-live-join", "deferred-ss10"],
    "machine_approved_classes": ["boundary-window", "bare-name-live-join"],
    "no_verdict_classes": ["boundary-window"],
    "unmatched_groups": ["no-chain-gains", "deferred-ss10"],
}


def test_invariant_delta_is_empty_exactly_when_nothing_moved():
    assert invariant_delta(_ACCEPTED_INVARIANT, dict(_ACCEPTED_INVARIANT)) == []


def test_invariant_delta_names_what_appeared_and_what_went_in_the_blocks_own_order():
    """`invariant_delta` names the ids that were added to or removed from each list (classes, machine-approved classes, no-verdict classes, unmatched groups), each in the block's own order, so the cycle log can be searched the same way on every pass."""
    current = {
        "classes": ["boundary-window", "bare-name-live-join", "see-out-fused", "deferred-ss04"],
        "machine_approved_classes": ["boundary-window", "bare-name-live-join", "see-out-fused"],
        "no_verdict_classes": ["boundary-window", "see-out-fused"],
        "unmatched_groups": ["no-chain-gains", "deferred-ss04"],
    }
    assert invariant_delta(_ACCEPTED_INVARIANT, current) == [
        "classes +2 (see-out-fused, deferred-ss04)",
        "classes -1 (deferred-ss10)",
        "machine-approved +1 (see-out-fused)",
        "no-verdict +1 (see-out-fused)",
        "unmatched groups +1 (deferred-ss04)",
        "unmatched groups -1 (deferred-ss10)",
    ]


def test_invariant_delta_tells_a_reorder_and_a_shape_change_from_a_corpus_change():
    """A ledger reorder changes a list's order but not its members, and a key that changes shape (a count replaced by a list) is a change to the block, not to the corpus. Neither may be reported as classes appearing."""
    reordered = {
        **_ACCEPTED_INVARIANT,
        "classes": ["bare-name-live-join", "boundary-window", "deferred-ss10"],
    }
    assert invariant_delta(_ACCEPTED_INVARIANT, reordered) == ["classes reordered"]
    counted = {**_ACCEPTED_INVARIANT}
    counted["classes_count"] = counted.pop("classes")
    assert invariant_delta(counted, _ACCEPTED_INVARIANT) == [
        "classes_count no longer recorded",
        "classes newly recorded",
    ]


def test_invariant_diff_is_the_blocks_own_unified_diff():
    """The cycle prints this when the invariant block changed: the block's unified diff without the volatile block, formatted like the pins file so the lines match what `git diff` shows for those keys."""
    current = {**_ACCEPTED_INVARIANT, "unmatched_groups": ["no-chain-gains"]}
    lines = invariant_diff(_ACCEPTED_INVARIANT, current)
    assert lines[:2] == ["--- invariant (accepted)", "+++ invariant (this corpus)"]
    assert '-    "deferred-ss10"' in lines
    assert all("units" not in line for line in lines)
    assert invariant_diff(_ACCEPTED_INVARIANT, dict(_ACCEPTED_INVARIANT)) == []


def test_ledger_coverage_holds_the_ledgers_declarations_against_what_the_corpus_reached():
    """A class is machine-approved when the build approved any of its units, so the ledger's ink-identical declarations and the machine-approved classes can disagree in both directions. A no-verdict declaration or a ledger entry is unreached when no unit in the corpus matches it. `ledger_coverage` compares the ledger's declarations with the invariant block."""
    ledger = [
        _ledger_entry("boundary-window", no_verdict=True),
        _ledger_entry("bare-name-live-join", ink_identical=True),
        _ledger_entry("see-out-fused", ink_identical=True),
        _ledger_entry("vie-baseline-entry-extension-dropped", no_verdict=True),
    ]
    invariant = {
        "classes": ["boundary-window", "bare-name-live-join", "see-out-fused", "deferred-ss10"],
        "machine_approved_classes": ["boundary-window", "bare-name-live-join", "deferred-ss10"],
        "no_verdict_classes": ["boundary-window"],
        "unmatched_groups": ["deferred-ss10"],
    }
    coverage = ledger_coverage(ledger, invariant)
    assert coverage.unreached == ("vie-baseline-entry-extension-dropped",)
    assert coverage.ink_declared == ("bare-name-live-join", "see-out-fused")
    assert coverage.ink_declared_unapproved == ("see-out-fused",)
    assert coverage.machine_approved_undeclared == ("boundary-window", "deferred-ss10")
    assert coverage.no_verdict_declared == ("boundary-window", "vie-baseline-entry-extension-dropped")
    assert coverage.no_verdict_reached == ("boundary-window",)
    assert coverage.no_verdict_unreached == ("vie-baseline-entry-extension-dropped",)
    assert coverage.describe() == (
        "machine-approved: 3 classes approve units, 2 undeclared;"
        " ink-identical: 2 declared, 1 (see-out-fused) approving none;"
        " no-verdict: 1 of 2 declared reached, unreached 1 (vie-baseline-entry-extension-dropped);"
        " ledger: 3 of 4 classes reached, unreached 1 (vie-baseline-entry-extension-dropped)"
    )
    assert coverage.as_json()["ink_declared_unapproved"] == ["see-out-fused"]


def test_ledger_coverage_reads_clean_when_the_ledger_and_the_corpus_agree():
    ledger = [
        _ledger_entry("boundary-window", no_verdict=True),
        _ledger_entry("bare-name-live-join", ink_identical=True),
    ]
    coverage = ledger_coverage(ledger, _ACCEPTED_INVARIANT)
    assert coverage.unreached == ()
    assert coverage.ink_declared_unapproved == ()
    assert coverage.no_verdict_unreached == ()
    assert coverage.describe() == (
        "machine-approved: 2 classes approve units, 1 undeclared; ink-identical: 1 declared, all approving;"
        " no-verdict: 1 of 1 declared reached; ledger: 2 of 2 classes reached"
    )
