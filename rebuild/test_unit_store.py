"""Tests for the packed unit store (`rebuild/review/unit_store.py`). Each accessor returns what the fold was given, in the shape the build's reduces and the store writer read: the `SeamHomeUnit` the home reduce compares, the `proj` and `seams` a store line carries, and the `CachedUnit` whose `record_line` matches the previous store's line byte for byte. The tests also cover the id index, the length checks, and the census.

Most tests fold synthetic projections and records. The one real workload is the checked-in mini bundle under `rebuild/review/fixtures/mini/`, whose projections the serial runner folds; no test reads a live artifact.
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
from rebuild.review.build import _seam_records
from rebuild.review.enrich import LETTERS, SeamHomeUnit
from rebuild.review.unit_store import UnitStore
from rebuild.tools import pile_tally

REPO_ROOT = Path(__file__).resolve().parents[1]
MINI = REPO_ROOT / "rebuild" / "review" / "fixtures" / "mini"


@dataclass(frozen=True, slots=True)
class _Projection:
    """The fields `fold_projection` reads from `build._UnitProjection`, so a test can build a projection without the runner."""

    unit_id: str
    input_key: str
    content_key: str
    ink_identical: bool
    picture_identical: bool
    junior_equivalent: bool
    ink_deltas: tuple[tuple[str, str], ...]
    diffs_digest: str
    cluster: str
    family: str
    pair_codepoints: tuple[int, int] | None
    seam_home: SeamHomeUnit
    seam_rects: tuple[tuple[tuple[int, int], dict, dict], ...]
    mismatches: tuple[str, ...]
    ordinal: int = -1
    part: str = ""
    start: int = 0
    length: int = 0


def _key(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _seam_home(unit_id: str, codepoints: tuple[int, ...], *, seams: bool = True) -> SeamHomeUnit:
    return SeamHomeUnit(
        unit_id=unit_id,
        codepoint_values=codepoints,
        ink_identical=False,
        picture_identical=False,
        pair=(0, 1),
        after_spans=((0, 1), (1, 2)),
        after_cells=tuple("qsTea/half/None/x-height/ qsIt/hapax/x-height/None/".split()),
        after_seams=tuple("y5".split()),
        before_spans=((0, 1), (1, 2)),
        before_glyphs=tuple("qsTea.half.ex-y5 qsIt.en-y5".split()),
        before_seams=tuple("break".split()),
        seam_pairs=((0, 1),) if seams else (),
    )


def _rects(seam_home: SeamHomeUnit) -> tuple[tuple[tuple[int, int], dict, dict], ...]:
    return tuple(
        (
            pair,
            {"x_min": 0, "x_max": 5 + index, "advance_total": 9},
            {"x_min": 1, "x_max": 6 + index, "advance_total": 9},
        )
        for index, pair in enumerate(seam_home.seam_pairs)
    )


def _projection(label: str, codepoints: tuple[int, ...] = (0xE652, 0xE670), **overrides) -> _Projection:
    content_key = _key(f"content:{label}")
    unit_id = unit_cache.unit_id_for(content_key)
    seam_home = overrides.pop("seam_home", None) or _seam_home(unit_id, codepoints)
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
        family="",
        pair_codepoints=(1, 0),
        seam_home=seam_home,
        seam_rects=_rects(seam_home),
        mismatches=(),
    )
    fields.update(overrides)
    fields["seam_home"] = replace(
        fields["seam_home"],
        ink_identical=fields["ink_identical"],
        picture_identical=fields["picture_identical"],
    )
    return _Projection(**fields)


_WINDOW = (0xE652, 0xE670)


def _record(seam_home: SeamHomeUnit) -> dict:
    """The expected `proj` dict for a projection, with the keys and lists `unit_cache.CachedUnit.to_record` writes. `seam_home_record` is compared against it."""
    return {
        "pair": list(seam_home.pair) if seam_home.pair else None,
        "after_spans": [list(span) for span in seam_home.after_spans],
        "after_cells": list(seam_home.after_cells),
        "after_seams": list(seam_home.after_seams),
        "before_spans": [list(span) for span in seam_home.before_spans],
        "before_glyphs": list(seam_home.before_glyphs),
        "before_seams": list(seam_home.before_seams),
    }


def _spooled(projection: _Projection, start: int) -> unit_cache.PriorFragment:
    return unit_cache.PriorFragment(
        "units/serial.000.json", start, 700 + start, projection.unit_id, projection.content_key
    )


def test_a_fresh_projection_reads_back_what_the_fold_was_handed():
    """Every accessor on a folded fresh projection returns what the projection carried, with the ink deltas in folded order and the seam rects in the shape `_seam_records` gives them."""
    projection = _projection("one", ink_identical=True, mismatches=("ss03 E652:E670: derived cells differ",))
    store = UnitStore(1)
    store.set_input_key(0, projection.input_key)
    assert (
        store.fold_projection(projection, no_verdict=False, ordinal=0, address=_spooled(projection, 12)) == 0
    )
    assert store.machine_flags(0) == (True, False, False) and store.machine_approved(0)
    assert store.machine_channel(0) == "ink_identical"
    flags = store.flags(0)
    assert (flags.ink_identical, flags.picture_identical, flags.junior_equivalent) == (True, False, False)
    assert (flags.served, flags.slim, flags.exemplar, flags.no_verdict, flags.verbatim) == (
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
    assert store.family(0) == projection.family == ""
    assert store.pair_codepoints(0) == projection.pair_codepoints
    assert store.cell_pair(0) == projection.seam_home.pair
    assert store.codepoints(0) == projection.seam_home.codepoint_values == _WINDOW
    assert store.seam_home(0) == projection.seam_home
    assert store.projection(0) == replace(store.seam_home(0), unit_id="")
    assert store.seam_rects(0) == _seam_records(projection.seam_rects)
    assert store.seam_pairs(0) == projection.seam_home.seam_pairs
    assert store.seam_count(0) == 1
    assert store.mismatches(0) == list(projection.mismatches)
    assert store.source(0) == _spooled(projection, 12)
    assert store.content_key_hex(0) == projection.content_key
    assert store.input_key_hex(0) == projection.input_key
    assert store.unit_id(0) == projection.unit_id
    assert store.seam_home_record(0) == _record(projection.seam_home)
    assert store.written_address(0) is None
    assert store.policy_file(0) is None and store.config_note(0) is None
    assert store.served_class(0) is None and store.served_echo(0) is None
    assert store.served_homes(0) == [[None, False]]
    assert store.homes(0) == ((None, False),)
    assert list(store.windows()) == [(0, _WINDOW)]


def test_the_input_key_column_is_written_once_and_a_fold_holds_its_key_to_it():
    """The plan writes each unit's input key before any fold. A fold whose key differs from the written one raises instead of overwriting it, a fold into a row with no written key fills it, and `emptied` keeps the keys and drops everything else."""
    projection = _projection("keyed")
    store = UnitStore(2)
    store.set_input_key(0, projection.input_key)
    with pytest.raises(ValueError, match="not the key the plan wrote"):
        store.fold_projection(replace(projection, input_key=_key("other")), no_verdict=False, ordinal=0)
    store = store.emptied()
    assert store.input_key_hex(0) == projection.input_key and not store.folded(0)
    store.fold_projection(projection, no_verdict=True, ordinal=0)
    assert store.flags(0).slim
    store.fold_projection(_projection("unkeyed"), no_verdict=False, ordinal=1)
    assert store.input_key_hex(1) == _key("input:unkeyed")
    with pytest.raises(ValueError, match="32 bytes"):
        store.set_input_key(0, "ab")
    with pytest.raises(ValueError):
        UnitStore(1, input_keys=bytearray(3))


def test_the_fold_takes_the_ordinal_and_address_off_the_projection_when_not_given():
    """`fold_projection` takes the ordinal and the spool address from its arguments or, when they are omitted, from the projection's `ordinal`, `part`, `start`, and `length`. A projection with no ordinal either way raises."""
    base = _projection("addressed")
    addressed = replace(base, ordinal=1, part="units/w0.000.json", start=3, length=40)
    store = UnitStore(2)
    assert store.fold_projection(addressed, no_verdict=False) == 1
    assert store.source(1) == unit_cache.PriorFragment(
        "units/w0.000.json", 3, 40, base.unit_id, base.content_key
    )
    other = _projection("no-address")
    assert store.fold_projection(other, no_verdict=False, ordinal=0) == 0
    assert store.source(0) is None
    with pytest.raises(ValueError, match="no ordinal"):
        UnitStore(1).fold_projection(_projection("bare"), no_verdict=False)


def _served_record(
    label: str, homes: list[list], address: tuple[str, int, int] | None
) -> unit_cache.CachedUnit:
    content_key = _key(f"content:{label}")
    return unit_cache.CachedUnit(
        key=_key(f"input:{label}"),
        prior_id=unit_cache.unit_id_for(content_key),
        prior_class="boundary-echo",
        content_key=content_key,
        slim=False,
        address=address,
        ink_identical=False,
        picture_identical=False,
        junior_equivalent=False,
        ink_deltas={"default": "d-0123456789ab"},
        diffs_digest="deadbeef",
        cluster="c-12345678",
        family="",
        pair_codepoints=(1, 2),
        proj={
            "pair": [0, 1],
            "after_spans": [[0, 1], [1, 2]],
            "after_cells": ["c", "d"],
            "after_seams": ["y5"],
            "before_spans": [[0, 1], [1, 2]],
            "before_glyphs": ["a", "b"],
            "before_seams": ["break"],
        },
        seams=[
            {
                "pair": [0, 1],
                "before": {"x_min": 0, "x_max": 5, "advance_total": 9},
                "after": {"x_min": 1, "x_max": 6, "advance_total": 9},
            }
        ],
        mismatches=[],
        echo="e-2WvdGAWe6bX",
        exemplar=False,
        no_verdict=False,
        homes=homes,
        policy_file="glyph_data/runes/qsTea.yaml",
    )


def _load(tmp_path: Path, records: list[unit_cache.CachedUnit]) -> dict[str, unit_cache.ServedUnit]:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    parts = sorted({record.address[0] for record in records if record.address})
    for part in parts:
        (tmp_path / part).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / part).write_bytes(b"[" + b" " * 999 + b"]")
    unit_cache.write_store(tmp_path, "env-a", records, parts=parts)
    loaded = unit_cache.load_store(tmp_path, "env-a")
    assert loaded is not None
    return loaded


def test_a_served_record_reads_back_and_its_cached_unit_is_the_store_line(tmp_path):
    """A store record that is written, loaded as a `ServedUnit`, and folded reads back through `cached_unit` as the same store line after its homes are set by id and its written address is recorded. `home_ordinals` resolves a home id to its ordinal through the index. The served-only columns hold the record's class, echo, flags, and homes, which are what `served_as_is` compares."""
    home = _served_record("home", [[None, False]], ("units/small.json", 1, 5))
    homed = _served_record("homed", [[home.prior_id, False]], ("units/small.json", 7, 5))
    loaded = _load(tmp_path, [home, homed])
    store = UnitStore(2)
    windows = [_WINDOW, (0xE652, 0xE670, 0xE652)]
    for ordinal, (record, window) in enumerate(zip((homed, home), windows)):
        cached = loaded[record.key]
        assert store.fold_served(ordinal, cached, codepoints=window) == ordinal
        assert store.unit_id(ordinal) == record.prior_id
    homed_served = loaded[homed.key]
    assert store.seam_home(0) == SeamHomeUnit(
        unit_id=store.unit_id(0),
        codepoint_values=windows[0],
        ink_identical=homed_served.ink_identical,
        picture_identical=homed_served.picture_identical,
        pair=homed_served.pair,
        after_spans=homed_served.after_spans,
        after_cells=homed_served.after_cells,
        after_seams=homed_served.after_seams,
        before_spans=homed_served.before_spans,
        before_glyphs=homed_served.before_glyphs,
        before_seams=homed_served.before_seams,
        seam_pairs=homed_served.seam_pairs,
    )
    assert store.seam_home_record(0) == homed.proj
    assert json.dumps(store.seam_home_record(0)) == json.dumps(homed.proj)
    assert store.seam_rects(0) == homed.seams
    assert store.ink_deltas(0) == homed.ink_deltas
    source = store.source(0)
    assert source == homed_served.located() and source is not None and source.verbatim
    assert store.flags(0).served and store.flags(0).verbatim and not store.flags(0).slim
    assert store.served_class(0) == "boundary-echo"
    assert store.served_echo(0) == "e-2WvdGAWe6bX"
    assert store.policy_file(0) == homed.policy_file
    assert store.served_homes(0) == homed.homes
    assert store.served_homes(1) == home.homes == [[None, False]]
    assert store.cell_pair(0) == (0, 1)
    assert store.pair_codepoints(0) == (1, 2)
    assert store.ordinal_of(home.prior_id) == 1
    store.set_homes(0, [(home.prior_id, False)])
    assert store.homes(0) == ((home.prior_id, False),)
    assert store.home_ordinals(0) == ((1, False),)
    assert store.homes_record(0) == homed.homes

    def held(ordinal: int, echo: str | None = "e-2WvdGAWe6bX", no_verdict: bool = False) -> bool:
        return store.served_as_is(
            ordinal, class_id="boundary-echo", echo=echo, exemplar=False, no_verdict=no_verdict
        )

    assert held(0) and held(1)
    for ordinal, record in enumerate((homed, home)):
        assert record.address is not None
        store.set_written_address(ordinal, record.address)
        assert store.written_address(ordinal) == record.address
        cached_unit = store.cached_unit(
            ordinal, class_id="boundary-echo", echo="e-2WvdGAWe6bX", exemplar=False, no_verdict=False
        )
        assert unit_cache.record_line(cached_unit) == unit_cache.record_line(record)
    assert not held(0, echo="e-other")
    assert not held(0, no_verdict=True)
    assert store.cached_unit(0, class_id="boundary-echo", echo=None, exemplar=False, no_verdict=True).slim
    store.set_homes(0, [(1, True)])
    assert not held(0)
    store.set_homes(0, [(None, False)])
    assert store.homes_record(0) == [[None, False]] and not held(0)
    with pytest.raises(ValueError):
        store.set_homes(0, [])
    with pytest.raises(IndexError):
        store.set_homes(0, [(5, False)])


def test_a_served_record_is_folded_with_the_fragment_the_plan_located(tmp_path):
    """A record without an address is folded with the fragment the walk found. That fragment becomes the source and is not verbatim, so `served_as_is` is false. Folding the record with no fragment, or with another unit's fragment, raises."""
    record = _served_record("walked", [[None, False]], None)
    cached = _load(tmp_path, [record])[record.key]
    assert cached.located() is None
    store = UnitStore(1)
    with pytest.raises(ValueError):
        store.fold_served(0, cached, codepoints=_WINDOW)
    other = unit_cache.PriorFragment("units/x.json", 1, 2, "u-11111111111", record.content_key)
    with pytest.raises(ValueError):
        store.fold_served(0, cached, codepoints=_WINDOW, found=other)
    walked = unit_cache.PriorFragment("units/x.json", 1, 2, record.prior_id, record.content_key)
    store.fold_served(0, cached, codepoints=_WINDOW, found=walked)
    assert store.source(0) == walked and not store.flags(0).verbatim
    assert not store.served_as_is(
        0, class_id="boundary-echo", echo="e-2WvdGAWe6bX", exemplar=False, no_verdict=False
    )


def test_the_id_index_refuses_a_duplicate_and_answers_ordinal_of():
    """`ordinal_of` returns each unit's ordinal from its id and raises `KeyError` for an id no unit has. Building the index exits with a message naming both windows when two units share a content key, and raises on an unfolded row before it would sort that row's all-zero key. Folding an ordinal a second time raises."""
    store = UnitStore(3)
    projections = [_projection(f"p{index}", (0xE652, 0xE670 + index)) for index in range(3)]
    for ordinal, projection in enumerate(reversed(projections)):
        store.fold_projection(projection, no_verdict=False, ordinal=ordinal)
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
    partial.fold_projection(projections[0], no_verdict=False, ordinal=1)
    with pytest.raises(ValueError, match="ordinal 0 was never folded"):
        partial.index()
    twice = UnitStore(2)
    twice.fold_projection(projections[0], no_verdict=False, ordinal=0)
    twice.fold_projection(
        replace(projections[1], content_key=projections[0].content_key, unit_id=projections[0].unit_id),
        no_verdict=False,
        ordinal=1,
    )
    with pytest.raises(
        SystemExit, match=f"two units share the id {projections[0].unit_id}: E652:E670 and E652:E671"
    ):
        twice.index()
    with pytest.raises(ValueError, match="folded twice"):
        store.fold_projection(projections[0], no_verdict=False, ordinal=0)


def test_id_string_order_is_the_order_of_the_words_they_spell():
    """`unit_cache.unit_id_for` writes the key's first 64 bits at a fixed width over an ASCII-ordered alphabet, so ids sort in the same order as their words, and `id_word_of` inverts the encoding. The home reduce relies on this to break ties on the integer."""
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


def test_the_fold_refuses_rows_whose_lengths_disagree():
    """The store keeps one count for a row's spans, names, and seams, and one for its seam pairs, rects, and homes, so a fold raises when those lengths disagree instead of truncating. It also raises on rect edges that are not the three `enrich._highlight` keys in order, on mismatched ink flags or unit id, and on an ordinal outside the store."""
    base = _projection("lengths")
    home = base.seam_home
    cases = {
        "after_cells": replace(home, after_cells=home.after_cells[:1]),
        "after_seams": replace(home, after_seams=("y5", "y5")),
        "before_glyphs": replace(home, before_glyphs=home.before_glyphs + ("x",)),
        "before_seams": replace(home, before_seams=()),
    }
    for label, seam_home in cases.items():
        with pytest.raises(ValueError, match="spans"):
            UnitStore(1).fold_projection(replace(base, seam_home=seam_home), no_verdict=False, ordinal=0)
    with pytest.raises(ValueError, match="seam rects"):
        UnitStore(1).fold_projection(replace(base, seam_rects=()), no_verdict=False, ordinal=0)
    with pytest.raises(ValueError, match="is not seam pair"):
        UnitStore(1).fold_projection(
            replace(base, seam_rects=(((1, 0),) + base.seam_rects[0][1:],)), no_verdict=False, ordinal=0
        )
    edges = base.seam_rects[0]
    reordered = {"x_max": 5, "x_min": 0, "advance_total": 9}
    with pytest.raises(ValueError, match="rect edge"):
        UnitStore(1).fold_projection(
            replace(base, seam_rects=((edges[0], reordered, edges[2]),)), no_verdict=False, ordinal=0
        )
    extra = {**edges[1], "extra": 1}
    with pytest.raises(ValueError, match="rect edge"):
        UnitStore(1).fold_projection(
            replace(base, seam_rects=((edges[0], edges[1], extra),)), no_verdict=False, ordinal=0
        )
    with pytest.raises(ValueError, match="ink flags are not the unit's"):
        UnitStore(1).fold_projection(replace(base, ink_identical=True), no_verdict=False, ordinal=0)
    with pytest.raises(ValueError, match="is not the id of content key"):
        UnitStore(1).fold_projection(replace(base, unit_id="u-11111111111"), no_verdict=False, ordinal=0)
    with pytest.raises(IndexError):
        UnitStore(1).fold_projection(base, no_verdict=False, ordinal=1)


def test_a_served_record_with_homes_off_its_seams_is_refused(tmp_path):
    record = _served_record("homes", [[None, False], [None, False]], ("units/small.json", 1, 5))
    cached = _load(tmp_path, [record])[record.key]
    with pytest.raises(ValueError, match="served homes"):
        UnitStore(1).fold_served(0, cached, codepoints=_WINDOW)


def _column_bytes(store: UnitStore) -> int:
    return sum(
        len(value) if isinstance(value, bytearray) else len(value) * value.itemsize
        for value in vars(store).values()
        if isinstance(value, (bytearray, unit_store.array))
    )


def _string_bytes(store: UnitStore) -> tuple[int, int]:
    strings = {string for string in store._table._strings if string}
    return len(strings), sum(len(string.encode()) + pile_tally.OFFSET_WIDTH for string in strings)


def test_the_census_is_the_arrays_bytes_and_empty_homes_and_mismatches_cost_none():
    """`census` reports the columns' bytes plus the mismatch lines as the packed figure, with the string table's counts beside it, and the packed figure plus the string table as the walked figure (`pile_tally.column_census`). A unit with no seam and no mismatch adds nothing to the seam side arrays, the home columns, or the mismatch dict."""
    store = UnitStore(2)
    seamless = _projection("seamless", seam_home=_seam_home("", (0xE652, 0xE670), seams=False))
    seamless = replace(seamless, seam_home=replace(seamless.seam_home, unit_id=seamless.unit_id))
    store.fold_projection(seamless, no_verdict=False, ordinal=0)
    seam_side = sum(
        len(column)
        for column in (
            store._seam_pairs,
            store._rect_edges,
            store._home,
            store._home_suppressed,
            store._served_home,
            store._served_home_suppressed,
        )
    )
    assert seam_side == 0 and store._mismatches == {}
    assert (
        store.seam_rects(0) == []
        and store.homes(0) == ()
        and store.homes_record(0) == []
        and store.served_homes(0) == []
    )
    store.set_homes(0, [])
    assert store.seam_count(0) == 0
    before = store.census()
    strings, string_bytes = _string_bytes(store)
    assert before == pile_tally.Measure(
        2,
        _column_bytes(store) + string_bytes,
        pile_tally.PackedCost(_column_bytes(store), strings, string_bytes),
    )
    store.fold_projection(_projection("seamed", mismatches=("a line",)), no_verdict=False, ordinal=1)
    after = store.census()
    strings, string_bytes = _string_bytes(store)
    lines = len("a line".encode()) + pile_tally.OFFSET_WIDTH
    assert after == pile_tally.Measure(
        2,
        _column_bytes(store) + string_bytes + lines,
        pile_tally.PackedCost(_column_bytes(store) + lines, strings, string_bytes),
    )
    assert after.est_bytes > before.est_bytes
    assert after.packed is not None and after.packed.strings == strings


def test_the_written_address_policy_file_and_config_note_round_trip():
    store = UnitStore(1)
    store.fold_projection(_projection("write"), no_verdict=False, ordinal=0)
    store.set_written_address(0, ("units/boundary-echo.000.json", 1234, 5678))
    store.set_policy_file(0, "glyph_data/runes/qsTea.yaml")
    store.set_config_note(0, "ss03 only")
    assert store.written_address(0) == ("units/boundary-echo.000.json", 1234, 5678)
    assert store.policy_file(0) == "glyph_data/runes/qsTea.yaml"
    assert store.config_note(0) == "ss03 only"
    store.set_policy_file(0, None)
    store.set_config_note(0, None)
    assert store.policy_file(0) is None and store.config_note(0) is None


class _RecordingStore(UnitStore):
    """A `UnitStore` that also keeps each projection it folds, keyed by ordinal, so a test can compare every accessor with it."""

    def __init__(self, n: int, **kwargs) -> None:
        super().__init__(n, **kwargs)
        self.projections: dict[int, unit_store.FreshProjection] = {}

    def fold_projection(self, projection, **kwargs):
        ordinal = super().fold_projection(projection, **kwargs)
        self.projections[ordinal] = projection
        return ordinal


def test_the_mini_bundle_folds_and_reads_back_every_projection(mini_bundle, tmp_path):
    """The serial runner folds the mini bundle's real projections as it drafts them. Every accessor equals the folded projection, the source is the spool address the projection carries, the input key is the one the plan wrote, the index finds every id, and `windows` lists every unit's window. `census` counts only the columns' bytes, because the string table belongs to the workload table and is charged under `workload.units`. The permutation `sort_for_triage` returns equals a sort by `triage_key`, which uses the id string."""
    workload = load_workload(MINI / "audit.tsv", mini_bundle.ledger, dict(LETTERS))
    table = workload.table
    store = _RecordingStore(table.n, strings=table.strings)
    keys = [_key(f"mini:{ordinal}") for ordinal in range(table.n)]
    for ordinal, key in enumerate(keys):
        store.set_input_key(ordinal, key)
    runner = review_build._FreshRunner(
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
    seams = 0
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
        assert (store.diffs_digest(ordinal), store.cluster(ordinal), store.family(ordinal)) == (
            projection.diffs_digest,
            projection.cluster,
            projection.family,
        )
        assert store.pair_codepoints(ordinal) == projection.pair_codepoints
        assert store.seam_home(ordinal) == projection.seam_home
        assert store.seam_home_record(ordinal) == _record(projection.seam_home)
        assert store.seam_rects(ordinal) == _seam_records(projection.seam_rects)
        assert store.mismatches(ordinal) == list(projection.mismatches)
        assert store.source(ordinal) == unit_cache.PriorFragment(
            getattr(projection, "part"),
            getattr(projection, "start"),
            getattr(projection, "length"),
            projection.unit_id,
            projection.content_key,
        )
        assert store.codepoints(ordinal) == unit.codepoint_values
        assert store.seam_count(ordinal) == len(projection.seam_home.seam_pairs)
        seams += store.seam_count(ordinal)
    assert seams, "the mini workload must hold seam-bearing units"
    assert [window for _, window in store.windows()] == [unit.codepoint_values for unit in units]
    reading = store.census()
    strings, string_bytes = _string_bytes(store)
    assert reading == pile_tally.Measure(
        len(units),
        _column_bytes(store),
        pile_tally.PackedCost(_column_bytes(store), strings, string_bytes),
    )
    restarted = store.emptied().census()
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
