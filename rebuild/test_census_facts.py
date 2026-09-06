"""Tests for the census-facts sidecar (rebuild/review/census.py): the projection of a surface build's post-merge phase-1 products back onto the pre-merge grain the pins are defined over, the two mirror functions that must reproduce their source-reading twins exactly, the sidecar's own read/write contract, and the CLI paths that consume it.

Everything here is synthetic and hermetic — hand-built audit rows, hand-set ink verdicts and families, no fonts, no shaping, no live workload — so nothing here can move when the corpus does. The live census numbers shift with every migrated letter and belong to the diff of the checked-in pins; a failure in this module is a derivation bug, always.
"""

import json
from pathlib import Path

import pytest

from rebuild.review import census
from rebuild.review.audit import (
    AuditRow,
    Unit,
    Workload,
    build_units,
    load_ledger,
    merge_ink_duplicate_units,
)
from rebuild.review.census import (
    FACTS_FORMAT,
    WORKED_EXAMPLE_CODEPOINTS,
    PremergeFacts,
    build_facts,
    built_group,
    built_group_from_memory,
    capture_premerge,
    derive_premerge,
    ink_group_from_flags,
    ink_histogram,
    invariant_group,
    load_facts,
    workload_digest,
    write_facts,
)
from rebuild.review.enrich import LETTERS

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "rebuild" / "m1-divergences.yaml"

FOLD_WINDOW = "E650:E665"
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


def _index_of(capture, codepoints: str, config: str) -> int:
    return next(
        index
        for index, snap in enumerate(capture)
        if snap.codepoints == codepoints and config in snap.configs
    )


def _folded_fixture():
    """Four windows covering every shape the projection has to handle: a default-reachable UNMATCHED survivor absorbing a relabeled ss04 sibling, two stylistic-set-only UNMATCHED siblings that defer to different buckets, a no-verdict matched unit absorbing an UNMATCHED sibling, and two standalone units that never fold. Returns the pre-merge capture and the post-fold live list, with phase 1's products hand-set on the survivors."""
    rows = [
        _row("default", FOLD_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss03", FOLD_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss04", FOLD_WINDOW, "UNMATCHED", ("qsPea.ss04", "qsMay")),
        _row("ss03", DEFERRED_WINDOW, "UNMATCHED", ("qsBay", "qsMay")),
        _row("ss04", DEFERRED_WINDOW, "UNMATCHED", ("qsBay.ss04", "qsMay")),
        _row("default", MIXED_WINDOW, "boundary-echo", ("qsTea", "qsMay")),
        _row("ss04", MIXED_WINDOW, "UNMATCHED", ("qsTea.ss04", "qsMay")),
        _row("default", STANDALONE_UNMATCHED, "UNMATCHED", ("qsDay", "qsDay")),
        _row("default", STANDALONE_MATCHED, "dangling-anchor-dropped", ("qsKey", "qsKey")),
    ]
    ledger = load_ledger(LEDGER_PATH)
    units = build_units(rows, ledger, dict(LETTERS))
    capture = capture_premerge(units)
    exempt = {entry.id for entry in ledger if entry.no_verdict}
    merge_ink_duplicate_units(units, lambda text, config: text, exempt)

    verdicts = {
        FOLD_WINDOW: (True, "no-chain-gains"),
        DEFERRED_WINDOW: (False, "deferred-ss04"),
        MIXED_WINDOW: (True, "unmatched-misc"),
        STANDALONE_UNMATCHED: (False, "seam-loss-withdrawal"),
        STANDALONE_MATCHED: (True, ""),
    }
    for unit in units:
        unit.ink_identical, unit.family_id = verdicts[unit.codepoints]
    return capture, units


def test_folded_siblings_take_their_survivors_ink_verdict():
    """A fold is proof that every config of every folded sibling renders one identical picture, so the survivor's ink verdict is the whole window's — each captured sibling reports its survivor's flag, and the units that never folded report their own."""
    capture, units = _folded_fixture()
    facts = derive_premerge(capture, units)
    flags = facts.ink_flags
    assert facts.units == len(capture) == len(flags) == 8
    assert flags[_index_of(capture, FOLD_WINDOW, "default")] == "1"
    assert flags[_index_of(capture, FOLD_WINDOW, "ss04")] == "1"
    assert flags[_index_of(capture, DEFERRED_WINDOW, "ss03")] == "0"
    assert flags[_index_of(capture, DEFERRED_WINDOW, "ss04")] == "0"
    assert flags[_index_of(capture, MIXED_WINDOW, "default")] == "1"
    assert flags[_index_of(capture, MIXED_WINDOW, "ss04")] == "1"
    assert flags[_index_of(capture, STANDALONE_UNMATCHED, "default")] == "0"
    assert flags[_index_of(capture, STANDALONE_MATCHED, "default")] == "1"


def test_families_read_deferral_from_the_premerge_config_classes():
    """The family of a pre-merge UNMATCHED unit is its own deferred bucket when it has one and its survivor's phase-1 family otherwise, and the bucket has to come from the pre-merge config classes the fold is about to widen: the ss03-only survivor here stays deferred-ss03 even though the ss04 sibling it absorbs would push the merged unit to deferred-ss04. A matched unit claims no family at all."""
    capture, units = _folded_fixture()
    facts = derive_premerge(capture, units)
    assert dict(facts.families) == {
        _index_of(capture, FOLD_WINDOW, "default"): "no-chain-gains",
        _index_of(capture, FOLD_WINDOW, "ss04"): "deferred-ss04",
        _index_of(capture, DEFERRED_WINDOW, "ss03"): "deferred-ss03",
        _index_of(capture, DEFERRED_WINDOW, "ss04"): "deferred-ss04",
        _index_of(capture, MIXED_WINDOW, "ss04"): "deferred-ss04",
        _index_of(capture, STANDALONE_UNMATCHED, "default"): "seam-loss-withdrawal",
    }
    assert [index for index, _family in facts.families] == sorted(index for index, _family in facts.families)
    matched = {_index_of(capture, MIXED_WINDOW, "default"), _index_of(capture, STANDALONE_MATCHED, "default")}
    assert matched.isdisjoint(index for index, _family in facts.families)


def test_derive_premerge_refuses_a_unit_with_no_resolvable_survivor():
    """A captured unit whose object is gone must be answered by exactly one live unit of the same window carrying its earliest config. Nothing else is a survivor, and guessing would silently attribute one window's ink to another's."""
    rows = [
        _row("default", FOLD_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
        _row("ss04", FOLD_WINDOW, "UNMATCHED", ("qsPea.ss04", "qsMay")),
    ]
    units = build_units(rows, load_ledger(LEDGER_PATH), dict(LETTERS))
    capture = capture_premerge(units)
    del units[_index_of(capture, FOLD_WINDOW, "ss04")]
    with pytest.raises(ValueError, match=FOLD_WINDOW):
        derive_premerge(capture, units)


def test_derive_premerge_refuses_an_unmatched_unit_with_no_family():
    """Every pre-merge UNMATCHED window owes the census a family. A non-deferred one whose survivor never got a phase-1 family is a hole in the partition, not an empty string to be recorded."""
    units = build_units(
        [_row("default", STANDALONE_UNMATCHED, "UNMATCHED", ("qsDay", "qsDay"))],
        load_ledger(LEDGER_PATH),
        dict(LETTERS),
    )
    capture = capture_premerge(units)
    with pytest.raises(ValueError, match=STANDALONE_UNMATCHED):
        derive_premerge(capture, units)


class _Comparator:
    """An ink oracle reading a preset verdict per window, so the histogram under test measures the bookkeeping and not the fonts."""

    def __init__(self, verdicts: dict[str, bool]):
        self._verdicts = verdicts

    def ink_identical(self, text: str, configs) -> bool:
        return self._verdicts[text]


def test_ink_group_from_flags_mirrors_the_histogram():
    """The two formulations of the ink group must agree exactly — same keys, same counts, and the same first-seen insertion order in by_class — because one of them now writes the pins and the other is the only independent statement of what they mean."""
    classes = ["boundary-echo", "dangling-anchor-dropped", "UNMATCHED"]
    identical = [True, True, False, False, True, False, False, True, False, False]
    rows = [
        _row("default", f"E65{index:X}:E665", classes[index % 3], (f"q{index}",))
        for index in range(len(identical))
    ]
    ledger = load_ledger(LEDGER_PATH)
    units = build_units(rows, ledger, dict(LETTERS))
    verdicts = {_text(unit): flag for unit, flag in zip(units, identical, strict=True)}
    flags = "".join("1" if verdicts[_text(unit)] else "0" for unit in units)
    class_rows = [(unit.class_id, unit.no_verdict) for unit in units]

    workload = Workload(units=units, ledger=ledger, row_count=len(rows))
    assert ink_group_from_flags(class_rows, flags) == ink_histogram(workload, _Comparator(verdicts))


def test_workload_digest_tracks_order_and_configs():
    """The digest is what proves a flag string is indexed against the workload a reader just loaded, so it has to move when the order moves and when a unit's config set changes — either would silently misalign every index after it."""
    units = build_units(
        [
            _row("default", FOLD_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
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


def _stub_unit(unit_id: str, codepoints: str, batch: int | None, echo: str | None) -> Unit:
    unit = Unit(codepoints=codepoints, baseline=(), new=(), class_id="boundary-echo", rows=())
    unit.unit_id = unit_id
    unit.batch = batch
    unit.echo = echo
    return unit


def _write_shard(root: Path, records: list[dict]) -> dict:
    """A surface skeleton holding only what the census reads off one: the three class ids manifest_group looks up by name, each carrying the no-verdict flag and the machine-approved histogram invariant_group reduces, one shard carrying `records`, and the manifest scalars the sidecar stamps itself with."""
    (root / "units").mkdir(parents=True, exist_ok=True)
    ids = ["boundary-echo", "dangling-anchor-dropped", "bare-name-live-join"]
    for position, class_id in enumerate(ids):
        payload = records if position == 0 else []
        (root / "units" / f"{class_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    manifest = {
        "generated_at": "2026-01-01T00:00:00Z",
        "repo_head": "0000000",
        "inputs_fingerprint": {"data": "x"},
        "totals": {"units": len(records), "rows": len(records), "batches": 1, "echo_groups": 1},
        "classes": [
            {
                "id": class_id,
                "shards": [f"units/{class_id}.json"],
                "unit_count": len(records) if position == 0 else 0,
                "no_verdict": class_id == "boundary-echo",
            }
            for position, class_id in enumerate(ids)
        ],
        "machine_approved": {"units": 3, "by_class": {"bare-name-live-join": 2, "boundary-echo": 1}},
        "secondary_seams": {"units_with_markers": 0, "seams_homed": 0},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _example_records():
    units = [
        _stub_unit("u-0000", WORKED_EXAMPLE_CODEPOINTS, 0, "e-0000"),
        _stub_unit("u-0001", "E670:E653:E652:E650", 0, "e-0000"),
        _stub_unit("u-0002", "E670:E653:E652:E651", 1, "e-0001"),
        _stub_unit("u-0003", "E650:E651", None, None),
    ]
    notes = [None, None, "only under ss10", "only when ss03 is on"]
    config_notes = {unit.unit_id: note for unit, note in zip(units, notes, strict=True)}
    records = [
        {
            "ink_identical": unit.batch is None,
            "picture_identical": False,
            "junior_equivalent": False,
            "no_verdict": False,
            "echo": unit.echo,
            "codepoints": unit.codepoints,
            "config_note": config_notes[unit.unit_id],
        }
        for unit in units
    ]
    return units, config_notes, records


def test_built_group_from_memory_mirrors_the_shard_walk(tmp_path):
    """The built group computed off the build's own units and per-unit notes has to equal the one computed by re-reading the shards it wrote — same human-workload size, same echo-sibling count for the worked example, same encoded config-note histogram."""
    units, config_notes, records = _example_records()
    manifest = _write_shard(tmp_path, records)
    assert built_group_from_memory(units, config_notes) == built_group(tmp_path, manifest)


def test_built_group_reports_a_missing_worked_example_as_none(tmp_path):
    """A workload that never pages the worked example to a human — every mini surface a test builds — reports its echo-sibling count as None rather than refusing to build, and both formulations agree on that too; over the live corpus it is the pins diff — an accepted count replaced by a null — that surfaces the loss."""
    units, config_notes, records = _example_records()
    units[0].batch = None
    records[0]["ink_identical"] = True
    manifest = _write_shard(tmp_path, records)
    from_memory = built_group_from_memory(units, config_notes)
    assert from_memory["worked_example_echo_siblings"] is None
    assert from_memory == built_group(tmp_path, manifest)


def _pins(row_count: int) -> dict:
    """A pin set in the checked-in file's two-block shape, small enough to hand-write: one structural fact and one volatile group."""
    return {
        "invariant": {"classes_count": 3, "no_verdict_classes": ["boundary-echo"]},
        "volatile": {"audit": {"row_count": row_count, "units": 1}},
    }


def _facts(pins: dict, generated_at: str = "2026-01-01T00:00:00Z") -> dict:
    manifest = {
        "generated_at": generated_at,
        "repo_head": "0000000",
        "inputs_fingerprint": {"data": "x"},
    }
    return {
        "format": FACTS_FORMAT,
        "surface": manifest,
        "pins": pins,
        "premerge": {"units": 0, "workload_digest": "", "ink_identical": "", "families": []},
    }


def test_facts_round_trip(tmp_path):
    facts = _facts(_pins(row_count=2))
    write_facts(tmp_path, facts)
    assert (tmp_path / census.FACTS_FILENAME).read_text(encoding="utf-8").endswith("}\n")
    assert load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"}) == facts


def test_load_facts_refuses_a_missing_wrong_format_or_orphaned_sidecar(tmp_path):
    """The sidecar is only ever read as the surface's own. Absent or of an unknown format, the surface predates it; stamped for another generated_at, the two came from different builds and the pins would describe neither."""
    with pytest.raises(ValueError, match="rebuild.review.build"):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    stale = _facts({})
    stale["format"] = "ams-census-facts/0"
    write_facts(tmp_path, stale)
    with pytest.raises(ValueError, match=FACTS_FORMAT):
        load_facts(tmp_path, {"generated_at": "2026-01-01T00:00:00Z"})

    write_facts(tmp_path, _facts({}, generated_at="2026-01-01T00:00:00Z"))
    with pytest.raises(ValueError, match="2026-02-02T00:00:00Z"):
        load_facts(tmp_path, {"generated_at": "2026-02-02T00:00:00Z"})


def test_invariant_group_keeps_each_sources_own_order():
    """The structural block draws on three orders and preserves all of them: the machine-approved classes in the manifest's own by_class order (a histogram, not a sorted list), the no-verdict classes in manifest class order, and the families in the FAMILY_ORDER order family_census emits. A block that reshuffled would show a hunk on every pass and so tell a diff reader nothing."""
    manifest = {
        "classes": [
            {"id": "boundary-echo", "no_verdict": True},
            {"id": "bare-name-live-join", "no_verdict": False},
            {"id": "halves-entry-extension-restored", "no_verdict": True},
        ],
        "machine_approved": {"units": 5, "by_class": {"bare-name-live-join": 3, "boundary-echo": 2}},
    }
    assert invariant_group(manifest, {"no-chain-gains": 8, "deferred-ss03": 1}) == {
        "classes_count": 3,
        "machine_approved_classes": ["bare-name-live-join", "boundary-echo"],
        "no_verdict_classes": ["boundary-echo", "halves-entry-extension-restored"],
        "families": ["no-chain-gains", "deferred-ss03"],
    }


def test_build_facts_reduces_its_own_premerge_records(tmp_path):
    """The pins the sidecar carries are reductions of the records it carries beside them, so a reader can recompute either group and get the same answer. The invariant block is a reduction too — of the same manifest and the same family census the volatile groups came from, which is why the two blocks can restate each other without any risk of disagreeing."""
    units, config_notes, records = _example_records()
    manifest = _write_shard(tmp_path, records)
    capture = capture_premerge(
        build_units(
            [
                _row("default", FOLD_WINDOW, "UNMATCHED", ("qsPea", "qsMay")),
                _row("default", STANDALONE_MATCHED, "boundary-echo", ("qsKey", "qsKey")),
            ],
            load_ledger(LEDGER_PATH),
            dict(LETTERS),
        )
    )
    premerge = PremergeFacts(
        units=len(capture),
        workload_digest=workload_digest(capture),
        ink_flags="10",
        families=[(_index_of(capture, FOLD_WINDOW, "default"), "no-chain-gains")],
    )
    facts = build_facts(manifest, units, config_notes, capture, premerge, row_count=2)
    assert facts["format"] == FACTS_FORMAT
    assert facts["surface"]["generated_at"] == manifest["generated_at"]
    volatile = facts["pins"]["volatile"]
    assert volatile["audit"] == {"row_count": 2, "units": 2}
    assert volatile["built"] == built_group(tmp_path, manifest)
    assert volatile["families"] == {"census": {"no-chain-gains": 1}, "total": 1}
    assert volatile["ink"] == ink_group_from_flags(
        [(snap.class_id, snap.no_verdict) for snap in capture], "10"
    )
    assert facts["pins"]["invariant"] == {
        "classes_count": 3,
        "machine_approved_classes": ["bare-name-live-join", "boundary-echo"],
        "no_verdict_classes": ["boundary-echo"],
        "families": ["no-chain-gains"],
    }
    assert facts["premerge"]["ink_identical"] == "10"
    assert facts["premerge"]["workload_digest"] == workload_digest(capture)


def _cli_surface(tmp_path: Path, pins: dict) -> Path:
    surface = tmp_path / "surface"
    surface.mkdir()
    manifest = {"generated_at": "2026-01-01T00:00:00Z", "repo_head": "0000000"}
    (surface / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_facts(surface, _facts(pins))
    return surface


def test_check_reads_the_sidecar_and_reports_per_key_mismatches(tmp_path, monkeypatch, capsys):
    """`--check --surface DIR` is the manual comparison: a read of that surface's sidecar against the last accepted pins, reported one line per moved key rather than as a diff of the whole file. Both blocks are compared — the file carries no descriptive stamp to skip — so the key names carry their block."""
    pins_path = tmp_path / "pins.json"
    monkeypatch.setattr(census, "PINS_PATH", pins_path)
    surface = _cli_surface(tmp_path, _pins(row_count=2))

    pins_path.write_text(json.dumps(_pins(row_count=2)), encoding="utf-8")
    assert census.main(["--check", "--surface", str(surface)]) == 0

    pins_path.write_text(json.dumps(_pins(row_count=1)), encoding="utf-8")
    assert census.main(["--check", "--surface", str(surface)]) == 1
    assert "  volatile.audit.row_count: pinned 1 != computed 2" in capsys.readouterr().err.splitlines()


def test_update_copies_the_sidecars_pins_verbatim(tmp_path, monkeypatch):
    """`--update` is a straight copy of the sidecar's pins block into the checked-in file, both blocks and every key — the build's own emission is what lands, so accepting a census can only ever be "review one diff"."""
    pins_path = tmp_path / "pins.json"
    monkeypatch.setattr(census, "PINS_PATH", pins_path)
    monkeypatch.setattr(census, "REPO_ROOT", tmp_path)
    pins = {
        "invariant": {"classes_count": 3, "families": ["no-chain-gains"]},
        "volatile": {
            "audit": {"row_count": 2, "units": 1},
            "families": {"census": {"no-chain-gains": 1}, "total": 1},
        },
    }
    surface = _cli_surface(tmp_path, pins)
    assert census.main(["--update", "--surface", str(surface)]) == 0
    assert json.loads(pins_path.read_text(encoding="utf-8")) == pins


def test_from_scratch_recomputes_from_sources_without_the_sidecar(tmp_path, monkeypatch):
    """`--from-scratch` is the standalone re-derivation the sidecar traded away: it re-reads the source artifacts for the pre-merge groups and never touches census-facts.json, which here is deliberately unreadable. It reaches the same two-block shape, the invariant block reading the families it just re-derived rather than any the sidecar might have held."""
    _units, _config_notes, records = _example_records()
    surface = tmp_path / "surface"
    surface.mkdir()
    manifest = _write_shard(surface, records)
    (surface / census.FACTS_FILENAME).write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(census, "audit_group", lambda repo_root=REPO_ROOT: {"audit": "sentinel"})
    monkeypatch.setattr(census, "ink_group", lambda repo_root=REPO_ROOT: {"ink": "sentinel"})
    monkeypatch.setattr(
        census,
        "families_group",
        lambda repo_root=REPO_ROOT: {"census": {"seam-loss-withdrawal": 3}, "total": 3},
    )

    pins = census.compute_pins(surface=surface, from_scratch=True)
    volatile = pins["volatile"]
    assert volatile["audit"] == {"audit": "sentinel"}
    assert volatile["ink"] == {"ink": "sentinel"}
    assert volatile["families"] == {"census": {"seam-loss-withdrawal": 3}, "total": 3}
    assert volatile["built"] == built_group(surface, manifest)
    assert pins["invariant"] == {
        "classes_count": 3,
        "machine_approved_classes": ["bare-name-live-join", "boundary-echo"],
        "no_verdict_classes": ["boundary-echo"],
        "families": ["seam-loss-withdrawal"],
    }
