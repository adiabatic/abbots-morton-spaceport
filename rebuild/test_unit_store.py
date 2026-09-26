"""Tests for the packed unit store (`rebuild/review/unit_store.py`). Each accessor returns what the load was given, in the shape the build's whole-corpus passes and the store writer read: the `PrimaryUnitProjection` the primary-unit resolution pass compares, the `proj` and `junctions` a store line carries, and the `CachedUnit` whose `record_line` matches the previous store's line byte for byte. The tests also cover the id index, the length checks, and the size estimate.

Most tests load synthetic projections and records. The one real workload is the checked-in mini bundle under `rebuild/review/fixtures/mini/`, whose projections the serial runner loads; no test reads a live artifact.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from rebuild.review import build as review_build
from rebuild.review import unit_cache, unit_store
from rebuild.review.audit import load_workload, sort_for_triage, triage_key
from rebuild.review.build import _junction_records
from rebuild.review.enrich import LETTERS, PrimaryUnitProjection
from rebuild.review.unit_store import UnitStore
from rebuild.tools import memory_tally

REPO_ROOT = Path(__file__).resolve().parents[1]
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"


@dataclass(frozen=True, slots=True)
class _Projection:
    """The fields `load_projection` reads from `build._UnitProjection`, so a test can build a projection without the runner."""

    unit_id: str
    input_key: str
    content_key: str
    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    ink_deltas: tuple[tuple[str, str], ...]
    diffs_digest: str
    cluster: str
    unmatched_group: str
    pair_codepoints: tuple[int, int] | None
    primary_unit_projection: PrimaryUnitProjection
    junction_rects: tuple[tuple[tuple[int, int], dict, dict], ...]
    mismatches: tuple[str, ...]
    ordinal: int = -1
    part: str = ""
    start: int = 0
    length: int = 0


def _key(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _primary_unit_projection(
    unit_id: str, codepoints: tuple[int, ...], *, junctions: bool = True
) -> PrimaryUnitProjection:
    return PrimaryUnitProjection(
        unit_id=unit_id,
        codepoint_values=codepoints,
        ink_identical=False,
        picture_identical=False,
        pair=(0, 1),
        after_spans=((0, 1), (1, 2)),
        after_cells=tuple("qsTea/half/None/x-height/ qsIt/hapax/x-height/None/".split()),
        after_junctions=tuple("y5".split()),
        before_spans=((0, 1), (1, 2)),
        before_glyphs=tuple("qsTea.half.ex-y5 qsIt.en-y5".split()),
        before_junctions=tuple("break".split()),
        junction_pairs=((0, 1),) if junctions else (),
    )


def _rects(primary_unit_projection: PrimaryUnitProjection) -> tuple[tuple[tuple[int, int], dict, dict], ...]:
    return tuple(
        (
            pair,
            {"x_min": 0, "x_max": 5 + index, "advance_total": 9},
            {"x_min": 1, "x_max": 6 + index, "advance_total": 9},
        )
        for index, pair in enumerate(primary_unit_projection.junction_pairs)
    )


def _projection(label: str, codepoints: tuple[int, ...] = (0xE652, 0xE670), **overrides) -> _Projection:
    content_key = _key(f"content:{label}")
    unit_id = unit_cache.unit_id_for(content_key)
    primary_unit_projection = overrides.pop("primary_unit_projection", None) or _primary_unit_projection(
        unit_id, codepoints
    )
    fields: dict = dict(
        unit_id=unit_id,
        input_key=_key(f"input:{label}"),
        content_key=content_key,
        ink_identical=False,
        picture_identical=False,
        junior_equivalent=False,
        ink_deltas=(("ss03", "d-0123456789ab"), ("default", "d-ba9876543210")),
        diffs_digest="deadbeef",
        cluster="c-12345678",
        unmatched_group="",
        pair_codepoints=(1, 0),
        primary_unit_projection=primary_unit_projection,
        junction_rects=_rects(primary_unit_projection),
        mismatches=(),
    )
    fields.update(overrides)
    fields["primary_unit_projection"] = replace(
        fields["primary_unit_projection"],
        ink_identical=fields["ink_identical"],
        picture_identical=fields["picture_identical"],
    )
    return _Projection(**fields)


_WINDOW = (0xE652, 0xE670)


def _record(primary_unit_projection: PrimaryUnitProjection) -> dict:
    """The expected `proj` dict for a projection, with the keys and lists `unit_cache.CachedUnit.to_record` writes. `primary_unit_projection_record` is compared against it."""
    return {
        "pair": list(primary_unit_projection.pair) if primary_unit_projection.pair else None,
        "after_spans": [list(span) for span in primary_unit_projection.after_spans],
        "after_cells": list(primary_unit_projection.after_cells),
        "after_junctions": list(primary_unit_projection.after_junctions),
        "before_spans": [list(span) for span in primary_unit_projection.before_spans],
        "before_glyphs": list(primary_unit_projection.before_glyphs),
        "before_junctions": list(primary_unit_projection.before_junctions),
    }


def _spooled(projection: _Projection, start: int) -> unit_cache.PriorFragment:
    return unit_cache.PriorFragment(
        "units/serial.000.json", start, 700 + start, projection.unit_id, projection.content_key
    )


def test_a_recomputed_projection_reads_back_what_the_load_was_handed():
    """Every accessor on a loaded recomputed projection returns what the projection carried, with the ink deltas in loaded order and the junction rects in the shape `_junction_records` gives them."""
    projection = _projection("one", ink_identical=True, mismatches=("ss03 E652:E670: derived cells differ",))
    store = UnitStore(1)
    store.set_input_key(0, projection.input_key)
    assert (
        store.load_projection(projection, no_verdict=False, ordinal=0, address=_spooled(projection, 12)) == 0
    )
    assert store.machine_flags(0) == (True, False, False) and store.machine_approved(0)
    assert store.machine_check(0) == "ink_identical"
    flags = store.flags(0)
    assert (flags.ink_identical, flags.picture_identical, flags.junior_equivalent) == (True, False, False)
    assert (flags.cached, flags.slim, flags.exemplar, flags.no_verdict, flags.byte_copied) == (
        False,
        True,
        False,
        False,
        False,
    )
    assert store.invisible(0)
    assert store.ink_deltas(0) == dict(projection.ink_deltas)
    assert list(store.ink_deltas(0)) == ["ss03", "default"]
    assert store.diffs_digest(0) == projection.diffs_digest
    assert store.cluster(0) == projection.cluster
    assert store.unmatched_group(0) == projection.unmatched_group == ""
    assert store.pair_codepoints(0) == projection.pair_codepoints
    assert store.cell_pair(0) == projection.primary_unit_projection.pair
    assert store.codepoints(0) == projection.primary_unit_projection.codepoint_values == _WINDOW
    assert store.primary_unit_projection(0) == projection.primary_unit_projection
    assert store.projection(0) == replace(store.primary_unit_projection(0), unit_id="")
    assert store.junction_rects(0) == _junction_records(projection.junction_rects)
    assert store.junction_pairs(0) == projection.primary_unit_projection.junction_pairs
    assert store.junction_count(0) == 1
    assert store.mismatches(0) == list(projection.mismatches)
    assert store.source(0) == _spooled(projection, 12)
    assert store.content_key_hex(0) == projection.content_key
    assert store.input_key_hex(0) == projection.input_key
    assert store.unit_id(0) == projection.unit_id
    assert store.primary_unit_projection_record(0) == _record(projection.primary_unit_projection)
    assert store.written_address(0) is None
    assert store.policy_file(0) is None and store.config_note(0) is None
    assert store.cached_class(0) is None and store.cached_duplicate_group(0) is None
    assert store.cached_primary_units(0) == [[None, False]]
    assert store.primary_units(0) == ((None, False),)
    assert list(store.windows()) == [(0, _WINDOW)]


def test_the_input_key_column_is_written_once_and_a_load_checks_its_key_against_it():
    """The plan writes each unit's input key before any load. A load whose key differs from the written one raises instead of overwriting it, a load into a row with no written key fills it, and `emptied` keeps the keys and drops everything else."""
    projection = _projection("keyed")
    store = UnitStore(2)
    store.set_input_key(0, projection.input_key)
    with pytest.raises(ValueError, match="not the key the plan wrote"):
        store.load_projection(replace(projection, input_key=_key("other")), no_verdict=False, ordinal=0)
    store = store.emptied()
    assert store.input_key_hex(0) == projection.input_key and not store.loaded(0)
    store.load_projection(projection, no_verdict=True, ordinal=0)
    assert store.flags(0).slim
    store.load_projection(_projection("unkeyed"), no_verdict=False, ordinal=1)
    assert store.input_key_hex(1) == _key("input:unkeyed")
    with pytest.raises(ValueError, match="32 bytes"):
        store.set_input_key(0, "ab")
    with pytest.raises(ValueError):
        UnitStore(1, input_keys=bytearray(3))


def test_the_load_takes_the_ordinal_and_address_off_the_projection_when_not_given():
    """`load_projection` takes the ordinal and the spool address from its arguments or, when they are omitted, from the projection's `ordinal`, `part`, `start`, and `length`. A projection with no ordinal either way raises."""
    base = _projection("addressed")
    addressed = replace(base, ordinal=1, part="units/w0.000.json", start=3, length=40)
    store = UnitStore(2)
    assert store.load_projection(addressed, no_verdict=False) == 1
    assert store.source(1) == unit_cache.PriorFragment(
        "units/w0.000.json", 3, 40, base.unit_id, base.content_key
    )
    other = _projection("no-address")
    assert store.load_projection(other, no_verdict=False, ordinal=0) == 0
    assert store.source(0) is None
    with pytest.raises(ValueError, match="no ordinal"):
        UnitStore(1).load_projection(_projection("bare"), no_verdict=False)


def _cached_record(
    label: str, primary_units: list[list], address: tuple[str, int, int] | None
) -> unit_cache.CachedUnit:
    content_key = _key(f"content:{label}")
    return unit_cache.CachedUnit(
        key=_key(f"input:{label}"),
        prior_id=unit_cache.unit_id_for(content_key),
        prior_class="boundary-window",
        content_key=content_key,
        slim=False,
        address=address,
        ink_identical=False,
        picture_identical=False,
        junior_equivalent=False,
        ink_deltas={"default": "d-0123456789ab"},
        diffs_digest="deadbeef",
        cluster="c-12345678",
        unmatched_group="",
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
                "pair": [0, 1],
                "before": {"x_min": 0, "x_max": 5, "advance_total": 9},
                "after": {"x_min": 1, "x_max": 6, "advance_total": 9},
            }
        ],
        mismatches=[],
        duplicate_group="e-2WvdGAWe6bX",
        exemplar=False,
        no_verdict=False,
        primary_units=primary_units,
        policy_file="glyph_data/runes/qsTea.yaml",
    )


def _load(tmp_path: Path, records: list[unit_cache.CachedUnit]) -> dict[str, unit_cache.ParsedCachedUnit]:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    parts = sorted({record.address[0] for record in records if record.address})
    for part in parts:
        (tmp_path / part).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / part).write_bytes(b"[" + b" " * 999 + b"]")
    unit_cache.write_store(tmp_path, "env-a", records, parts=parts)
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None
    return loaded


def test_a_cached_record_reads_back_and_its_cached_unit_is_the_store_line(tmp_path):
    """A store record that is written, parsed as a `ParsedCachedUnit`, and loaded reads back through `cached_unit` as the same store line after its primary units are set by id and its written address is recorded. `primary_unit_ordinals` resolves a primary unit id to its ordinal through the index. The cached-only columns hold the record's class, duplicate group, flags, and primary units, which are what `cached_as_is` compares."""
    primary_unit = _cached_record("primary_unit", [[None, False]], ("units/small.json", 1, 5))
    with_primary = _cached_record(
        "with_primary", [[primary_unit.prior_id, False]], ("units/small.json", 7, 5)
    )
    loaded = _load(tmp_path, [primary_unit, with_primary])
    store = UnitStore(2)
    windows = [_WINDOW, (0xE652, 0xE670, 0xE652)]
    for ordinal, (record, window) in enumerate(zip((with_primary, primary_unit), windows)):
        cached = loaded[record.key]
        assert store.load_cached(ordinal, cached, codepoints=window) == ordinal
        assert store.unit_id(ordinal) == record.prior_id
    with_primary_cached = loaded[with_primary.key]
    assert store.primary_unit_projection(0) == PrimaryUnitProjection(
        unit_id=store.unit_id(0),
        codepoint_values=windows[0],
        ink_identical=with_primary_cached.ink_identical,
        picture_identical=with_primary_cached.picture_identical,
        pair=with_primary_cached.pair,
        after_spans=with_primary_cached.after_spans,
        after_cells=with_primary_cached.after_cells,
        after_junctions=with_primary_cached.after_junctions,
        before_spans=with_primary_cached.before_spans,
        before_glyphs=with_primary_cached.before_glyphs,
        before_junctions=with_primary_cached.before_junctions,
        junction_pairs=with_primary_cached.junction_pairs,
    )
    assert store.primary_unit_projection_record(0) == with_primary.proj
    assert json.dumps(store.primary_unit_projection_record(0)) == json.dumps(with_primary.proj)
    assert store.junction_rects(0) == with_primary.junctions
    assert store.ink_deltas(0) == with_primary.ink_deltas
    source = store.source(0)
    assert source == with_primary_cached.located() and source is not None and source.byte_copied
    assert store.flags(0).cached and store.flags(0).byte_copied and not store.flags(0).slim
    assert store.cached_class(0) == "boundary-window"
    assert store.cached_duplicate_group(0) == "e-2WvdGAWe6bX"
    assert store.policy_file(0) == with_primary.policy_file
    assert store.cached_primary_units(0) == with_primary.primary_units
    assert store.cached_primary_units(1) == primary_unit.primary_units == [[None, False]]
    assert store.cell_pair(0) == (0, 1)
    assert store.pair_codepoints(0) == (1, 2)
    assert store.ordinal_of(primary_unit.prior_id) == 1
    store.set_primary_units(0, [(primary_unit.prior_id, False)])
    assert store.primary_units(0) == ((primary_unit.prior_id, False),)
    assert store.primary_unit_ordinals(0) == ((1, False),)
    assert store.primary_units_record(0) == with_primary.primary_units

    def held(ordinal: int, duplicate_group: str | None = "e-2WvdGAWe6bX", no_verdict: bool = False) -> bool:
        return store.cached_as_is(
            ordinal,
            class_id="boundary-window",
            duplicate_group=duplicate_group,
            exemplar=False,
            no_verdict=no_verdict,
        )

    assert held(0) and held(1)
    for ordinal, record in enumerate((with_primary, primary_unit)):
        assert record.address is not None
        store.set_written_address(ordinal, record.address)
        assert store.written_address(ordinal) == record.address
        cached_unit = store.cached_unit(
            ordinal,
            class_id="boundary-window",
            duplicate_group="e-2WvdGAWe6bX",
            exemplar=False,
            no_verdict=False,
        )
        assert unit_cache.record_line(cached_unit) == unit_cache.record_line(record)
    assert not held(0, duplicate_group="e-other")
    assert not held(0, no_verdict=True)
    assert store.cached_unit(
        0, class_id="boundary-window", duplicate_group=None, exemplar=False, no_verdict=True
    ).slim
    store.set_primary_units(0, [(1, True)])
    assert not held(0)
    store.set_primary_units(0, [(None, False)])
    assert store.primary_units_record(0) == [[None, False]] and not held(0)
    with pytest.raises(ValueError):
        store.set_primary_units(0, [])
    with pytest.raises(IndexError):
        store.set_primary_units(0, [(5, False)])


def test_a_cached_record_is_loaded_with_the_fragment_the_plan_located(tmp_path):
    """A record without an address is loaded with the fragment the walk found. That fragment becomes the source and is not byte-copied, so `cached_as_is` is false. Loading the record with no fragment, or with another unit's fragment, raises."""
    record = _cached_record("walked", [[None, False]], None)
    cached = _load(tmp_path, [record])[record.key]
    assert cached.located() is None
    store = UnitStore(1)
    with pytest.raises(ValueError):
        store.load_cached(0, cached, codepoints=_WINDOW)
    other = unit_cache.PriorFragment("units/x.json", 1, 2, "u-11111111111", record.content_key)
    with pytest.raises(ValueError):
        store.load_cached(0, cached, codepoints=_WINDOW, found=other)
    walked = unit_cache.PriorFragment("units/x.json", 1, 2, record.prior_id, record.content_key)
    store.load_cached(0, cached, codepoints=_WINDOW, found=walked)
    assert store.source(0) == walked and not store.flags(0).byte_copied
    assert not store.cached_as_is(
        0, class_id="boundary-window", duplicate_group="e-2WvdGAWe6bX", exemplar=False, no_verdict=False
    )


def test_the_id_index_refuses_a_duplicate_and_answers_ordinal_of():
    """`ordinal_of` returns each unit's ordinal from its id and raises `KeyError` for an id no unit has. Building the index exits with a message naming both windows when two units share a content key, and raises on an unloaded row before it would sort that row's all-zero key. Loading an ordinal a second time raises."""
    store = UnitStore(3)
    projections = [_projection(f"p{index}", (0xE652, 0xE670 + index)) for index in range(3)]
    for ordinal, projection in enumerate(reversed(projections)):
        store.load_projection(projection, no_verdict=False, ordinal=ordinal)
    for ordinal, projection in enumerate(reversed(projections)):
        assert store.ordinal_of(projection.unit_id) == ordinal
        assert store.id_word(ordinal) == unit_store.id_word_of(projection.unit_id)
        assert store.id_word(ordinal) == int(projection.content_key[:16], 16)
    with pytest.raises(KeyError):
        store.ordinal_of(unit_cache.unit_id_for(_key("absent")))
    with pytest.raises(KeyError):
        store.ordinal_of("e-2WvdGAWe6bX")
    with pytest.raises(KeyError):
        store.ordinal_of("u-zzzzzzzzzzz")
    partial = UnitStore(2)
    partial.load_projection(projections[0], no_verdict=False, ordinal=1)
    with pytest.raises(ValueError, match="ordinal 0 was never loaded"):
        partial.index()
    twice = UnitStore(2)
    twice.load_projection(projections[0], no_verdict=False, ordinal=0)
    twice.load_projection(
        replace(projections[1], content_key=projections[0].content_key, unit_id=projections[0].unit_id),
        no_verdict=False,
        ordinal=1,
    )
    with pytest.raises(
        SystemExit, match=f"two units share the id {projections[0].unit_id}: E652:E670 and E652:E671"
    ):
        twice.index()
    with pytest.raises(ValueError, match="loaded twice"):
        store.load_projection(projections[0], no_verdict=False, ordinal=0)


def test_id_string_order_is_the_order_of_the_words_they_spell():
    """`unit_cache.unit_id_for` writes the key's first 64 bits at a fixed width over an ASCII-ordered alphabet, so ids sort in the same order as their words, and `id_word_of` inverts the encoding. The primary-unit resolution pass relies on this to break ties on the integer."""
    generator = random.Random(299)
    words = [generator.getrandbits(64) for _ in range(4096)] + [
        0,
        1,
        57,
        58,
        2**64 - 1,
        2**63,
        58**10,
        58**10 - 1,
    ]
    ids = [unit_cache.unit_id_for(f"{word:016x}" + "0" * 48) for word in words]
    assert [unit_store.id_word_of(unit_id) for unit_id in ids] == words
    assert sorted(ids) == [unit_id for _, unit_id in sorted(zip(words, ids))]
    assert list(unit_cache.BASE58_ALPHABET) == sorted(unit_cache.BASE58_ALPHABET)


def test_the_load_refuses_rows_whose_lengths_disagree():
    """The store keeps one count for a row's spans, names, and junctions, and one for its junction pairs, rects, and primary units, so a load raises when those lengths disagree instead of truncating. It also raises on rect edges that are not the three `enrich._highlight` keys in order, on mismatched ink flags or unit id, and on an ordinal outside the store."""
    base = _projection("lengths")
    primary_unit = base.primary_unit_projection
    cases = {
        "after_cells": replace(primary_unit, after_cells=primary_unit.after_cells[:1]),
        "after_junctions": replace(primary_unit, after_junctions=("y5", "y5")),
        "before_glyphs": replace(primary_unit, before_glyphs=primary_unit.before_glyphs + ("x",)),
        "before_junctions": replace(primary_unit, before_junctions=()),
    }
    for label, primary_unit_projection in cases.items():
        with pytest.raises(ValueError, match="spans"):
            UnitStore(1).load_projection(
                replace(base, primary_unit_projection=primary_unit_projection), no_verdict=False, ordinal=0
            )
    with pytest.raises(ValueError, match="junction rects"):
        UnitStore(1).load_projection(replace(base, junction_rects=()), no_verdict=False, ordinal=0)
    with pytest.raises(ValueError, match="is not junction pair"):
        UnitStore(1).load_projection(
            replace(base, junction_rects=(((1, 0),) + base.junction_rects[0][1:],)),
            no_verdict=False,
            ordinal=0,
        )
    edges = base.junction_rects[0]
    reordered = {"x_max": 5, "x_min": 0, "advance_total": 9}
    with pytest.raises(ValueError, match="rect edge"):
        UnitStore(1).load_projection(
            replace(base, junction_rects=((edges[0], reordered, edges[2]),)), no_verdict=False, ordinal=0
        )
    extra = {**edges[1], "extra": 1}
    with pytest.raises(ValueError, match="rect edge"):
        UnitStore(1).load_projection(
            replace(base, junction_rects=((edges[0], edges[1], extra),)), no_verdict=False, ordinal=0
        )
    with pytest.raises(ValueError, match="ink flags are not the unit's"):
        UnitStore(1).load_projection(replace(base, ink_identical=True), no_verdict=False, ordinal=0)
    with pytest.raises(ValueError, match="is not the id of content key"):
        UnitStore(1).load_projection(replace(base, unit_id="u-11111111111"), no_verdict=False, ordinal=0)
    with pytest.raises(IndexError):
        UnitStore(1).load_projection(base, no_verdict=False, ordinal=1)


def test_a_cached_record_with_primary_units_off_its_junctions_is_refused(tmp_path):
    record = _cached_record("primary_units", [[None, False], [None, False]], ("units/small.json", 1, 5))
    cached = _load(tmp_path, [record])[record.key]
    with pytest.raises(ValueError, match="cached primary_units"):
        UnitStore(1).load_cached(0, cached, codepoints=_WINDOW)


def _column_bytes(store: UnitStore) -> int:
    return sum(
        len(value) if isinstance(value, bytearray) else len(value) * value.itemsize
        for value in vars(store).values()
        if isinstance(value, (bytearray, unit_store.array))
    )


def _string_bytes(store: UnitStore) -> tuple[int, int]:
    strings = {string for string in store._table._strings if string}
    return len(strings), sum(len(string.encode()) + memory_tally.OFFSET_WIDTH for string in strings)


def test_the_sizes_are_the_arrays_bytes_and_empty_primary_units_and_mismatches_cost_none():
    """`sizes` reports the columns' bytes plus the mismatch lines as the packed figure, with the string table's counts beside it, and the packed figure plus the string table as the walked figure (`memory_tally.column_sizes`). A unit with no junction and no mismatch adds nothing to the junction side arrays, the primary unit columns, or the mismatch dict."""
    store = UnitStore(2)
    without_junctions = _projection(
        "without_junctions",
        primary_unit_projection=_primary_unit_projection("", (0xE652, 0xE670), junctions=False),
    )
    without_junctions = replace(
        without_junctions,
        primary_unit_projection=replace(
            without_junctions.primary_unit_projection, unit_id=without_junctions.unit_id
        ),
    )
    store.load_projection(without_junctions, no_verdict=False, ordinal=0)
    junction_side = sum(
        len(column)
        for column in (
            store._junction_pairs,
            store._rect_edges,
            store._primary_unit,
            store._primary_unit_suppressed,
            store._cached_primary_unit,
            store._cached_primary_unit_suppressed,
        )
    )
    assert junction_side == 0 and store._mismatches == {}
    assert (
        store.junction_rects(0) == []
        and store.primary_units(0) == ()
        and store.primary_units_record(0) == []
        and store.cached_primary_units(0) == []
    )
    store.set_primary_units(0, [])
    assert store.junction_count(0) == 0
    before = store.sizes()
    strings, string_bytes = _string_bytes(store)
    assert before == memory_tally.Measure(
        2,
        _column_bytes(store) + string_bytes,
        memory_tally.PackedCost(_column_bytes(store), strings, string_bytes),
    )
    store.load_projection(_projection("with_junctions", mismatches=("a line",)), no_verdict=False, ordinal=1)
    after = store.sizes()
    strings, string_bytes = _string_bytes(store)
    lines = len("a line".encode()) + memory_tally.OFFSET_WIDTH
    assert after == memory_tally.Measure(
        2,
        _column_bytes(store) + string_bytes + lines,
        memory_tally.PackedCost(_column_bytes(store) + lines, strings, string_bytes),
    )
    assert after.est_bytes > before.est_bytes
    assert after.packed is not None and after.packed.strings == strings


def test_the_written_address_policy_file_and_config_note_round_trip():
    store = UnitStore(1)
    store.load_projection(_projection("write"), no_verdict=False, ordinal=0)
    store.set_written_address(0, ("units/boundary-window.000.json", 1234, 5678))
    store.set_policy_file(0, "glyph_data/runes/qsTea.yaml")
    store.set_config_note(0, "ss03 only")
    assert store.written_address(0) == ("units/boundary-window.000.json", 1234, 5678)
    assert store.policy_file(0) == "glyph_data/runes/qsTea.yaml"
    assert store.config_note(0) == "ss03 only"
    store.set_policy_file(0, None)
    store.set_config_note(0, None)
    assert store.policy_file(0) is None and store.config_note(0) is None


class _RecordingStore(UnitStore):
    """A `UnitStore` that also keeps each projection it loads, keyed by ordinal, so a test can compare every accessor with it."""

    def __init__(self, n: int, **kwargs) -> None:
        super().__init__(n, **kwargs)
        self.projections: dict[int, unit_store.RecomputedProjection] = {}

    def load_projection(self, projection, **kwargs):
        ordinal = super().load_projection(projection, **kwargs)
        self.projections[ordinal] = projection
        return ordinal


def test_the_mini_bundle_loads_and_reads_back_every_projection(mini_bundle, tmp_path):
    """The serial runner loads the mini bundle's real projections as it drafts them. Every accessor equals the loaded projection, the source is the spool address the projection carries, the input key is the one the plan wrote, the index finds every id, and `windows` lists every unit's window. `sizes` counts only the columns' bytes, because the string table belongs to the workload table and is charged under `workload.units`. The permutation `sort_for_triage` returns equals a sort by `triage_key`, which uses the id string."""
    workload = load_workload(MINI / "audit.tsv", mini_bundle.ledger, dict(LETTERS))
    table = workload.table
    store = _RecordingStore(table.n, strings=table.strings)
    keys = [_key(f"mini:{ordinal}") for ordinal in range(table.n)]
    for ordinal, key in enumerate(keys):
        store.set_input_key(ordinal, key)
    runner = review_build._RecomputeRunner(
        range(table.n),
        1,
        MINI,
        review_build.SITE_BEFORE_FONT,
        MINI / "M1.otf",
        review_build.SITE_JUNIOR_FONT,
        REPO_ROOT,
        spec_root=mini_bundle.spec_root,
        table=table,
        out_dir=tmp_path,
        subset_pack=mini_bundle.subset_pack,
    )
    try:
        runner.phase1(store)
    finally:
        runner.close()
    assert sorted(store.projections) == list(range(table.n))
    units = table.units(store)
    junctions = 0
    for ordinal, unit in enumerate(units):
        projection = store.projections[ordinal]
        assert unit.unit_id == projection.unit_id and unit.input_key == keys[ordinal]
        assert store.ordinal_of(unit.unit_id) == ordinal
        assert store.unit_id(ordinal) == unit.unit_id
        assert store.input_key_hex(ordinal) == keys[ordinal] == projection.input_key
        flags = store.flags(ordinal)
        assert (flags.ink_identical, flags.picture_identical, flags.junior_equivalent, flags.slim) == (
            projection.ink_identical,
            projection.picture_identical,
            projection.junior_equivalent,
            unit.slim_fragment,
        )
        assert store.ink_deltas(ordinal) == dict(projection.ink_deltas)
        assert list(store.ink_deltas(ordinal)) == [config for config, _delta in projection.ink_deltas]
        assert (store.diffs_digest(ordinal), store.cluster(ordinal), store.unmatched_group(ordinal)) == (
            projection.diffs_digest,
            projection.cluster,
            projection.unmatched_group,
        )
        assert store.pair_codepoints(ordinal) == projection.pair_codepoints
        assert store.primary_unit_projection(ordinal) == projection.primary_unit_projection
        assert store.primary_unit_projection_record(ordinal) == _record(projection.primary_unit_projection)
        assert store.junction_rects(ordinal) == _junction_records(projection.junction_rects)
        assert store.mismatches(ordinal) == list(projection.mismatches)
        assert store.source(ordinal) == unit_cache.PriorFragment(
            getattr(projection, "part"),
            getattr(projection, "start"),
            getattr(projection, "length"),
            projection.unit_id,
            projection.content_key,
        )
        assert store.codepoints(ordinal) == unit.codepoint_values
        assert store.junction_count(ordinal) == len(projection.primary_unit_projection.junction_pairs)
        junctions += store.junction_count(ordinal)
    assert junctions, "the mini workload must hold junction-bearing units"
    assert [window for _, window in store.windows()] == [unit.codepoint_values for unit in units]
    reading = store.sizes()
    strings, string_bytes = _string_bytes(store)
    assert reading == memory_tally.Measure(
        len(units),
        _column_bytes(store),
        memory_tally.PackedCost(_column_bytes(store), strings, string_bytes),
    )
    restarted = store.emptied().sizes()
    assert restarted.packed is not None and restarted.est_bytes == restarted.packed.est_bytes
    assert (restarted.packed.strings, restarted.packed.string_bytes) == (strings, string_bytes)
    class_order = {entry.id: index for index, entry in enumerate(workload.classes_present)}
    order = sort_for_triage(table, store, class_order, dict(LETTERS))
    family_rank = {name: value for value, name in dict(LETTERS).items()}
    by_id = sorted(
        range(table.n),
        key=lambda ordinal: triage_key(
            class_order.get(table.class_id(ordinal), len(class_order)),
            table.group(ordinal),
            table.codepoints(ordinal),
            store.unit_id(ordinal),
            family_rank,
        ),
    )
    assert list(order) == by_id and sorted(order) == list(range(table.n))
