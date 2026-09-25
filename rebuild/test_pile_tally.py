"""Tests for the debug pile tally in rebuild/tools/pile_tally.py: it is off unless `AMS_SURFACE_PILE_TALLY` is exactly `1`, its estimates scale a sample by the count, and its lines match the formats the module docstring documents."""

import io
import re
from array import array
from dataclasses import dataclass
from typing import Any

import pytest

from rebuild.review import columns
from rebuild.tools import pile_tally

_PILE_LINE = re.compile(r"^\[tally\] (\S+) (\S+) count=(\d+) est_bytes=(\d+) est_gb=(\d+\.\d\d)$")
_PACKED_LINE = re.compile(
    r"^\[tally\] (\S+) (\S+) count=(\d+) est_bytes=(\d+) est_gb=(\d+\.\d\d) packed_bytes=(\d+) "
    r"packed_per=(\d+\.\d) walked_per=(\d+\.\d) ratio=(\d+\.\d\d|-) strings=(\d+) string_bytes=(\d+)$"
)
_LARGEST_LINE = re.compile(r"^\[tally\] (\S+) largest=(\S+)$")


@dataclass(slots=True)
class _Row:
    label: str
    names: tuple[str, ...]


@dataclass(slots=True)
class _Unit:
    key: str
    rows: tuple[_Row, ...]
    flags: dict


@dataclass(slots=True)
class _State:
    flag: bool
    digest: str | None
    names: tuple[str, ...]
    deltas: dict[str, str]
    home: _Row | None
    key: str


def _state(index: int) -> _State:
    shared = ("qsPea", "qsBay") if index % 3 else ("qsTea",)
    return _State(
        bool(index % 3),
        f"d-{index:012x}" if index < 1000 else "d-shared",
        shared,
        {} if index % 5 else {"default": "delta-a", "ss03": "delta-b"},
        None if index % 7 else _Row("ss10", shared),
        "ab" * 32,
    )


_STATE_SHAPE = pile_tally.Record(
    {
        "flag": pile_tally.Flag(),
        "digest": pile_tally.Id(),
        "names": pile_tally.Many(pile_tally.Id()),
        "deltas": pile_tally.Table(pile_tally.Id(), pile_tally.Id()),
        "home": pile_tally.Record({"label": pile_tally.Id(), "names": pile_tally.Many(pile_tally.Slot(2))}),
        "key": pile_tally.Hex(),
    }
)


def _packed_bytes(state: _State) -> float:
    fixed = pile_tally.OFFSET_WIDTH + pile_tally.COUNT_WIDTH
    return (
        1 / 8
        + pile_tally.ID_WIDTH
        + fixed
        + pile_tally.ID_WIDTH * len(state.names)
        + fixed
        + 2 * pile_tally.ID_WIDTH * len(state.deltas)
        + pile_tally.ID_WIDTH
        + fixed
        + (0 if state.home is None else 2 * len(state.home.names))
        + 32
    )


@dataclass(slots=True)
class _Pinned:
    flags: tuple[bool, ...]
    key: str
    codepoints: tuple[int, ...]
    cluster: str
    names: tuple[str, ...]
    deltas: dict[str, str]
    address: tuple[str, int, int]


def _pinned() -> _Pinned:
    return _Pinned(
        (True, False, False, True, False, False, False, True),
        "ab" * 32,
        (0xE650, 0xE651),
        "cluster-a",
        ("qsPea", "qsBay"),
        {"default": "qsPea"},
        ("units/qsPea.json", 12, 40),
    )


_PINNED_SHAPE = pile_tally.Record(
    {
        "flags": pile_tally.Positional((pile_tally.Flag(),) * 8),
        "key": pile_tally.Hex(),
        "codepoints": pile_tally.Derived(lambda values: 1 + 2 * len(values)),
        "cluster": pile_tally.Id(),
        "names": pile_tally.Many(pile_tally.Id()),
        "deltas": pile_tally.Table(pile_tally.Id(), pile_tally.Id()),
        "address": pile_tally.Positional((pile_tally.Id(), pile_tally.Slot(4), pile_tally.Slot(4))),
    }
)


class _CountedPile(list):
    """A list that counts the passes made over it, so a test can check how many times the string-table walk reads every member."""

    def __init__(self, members) -> None:
        super().__init__(members)
        self.passes = 0

    def __iter__(self):
        self.passes += 1
        return list.__iter__(self)


class _CountedView:
    def __init__(self, owner, items) -> None:
        self._owner = owner
        self._items = items

    def __iter__(self):
        self._owner.passes += 1
        return iter(self._items)


class _CountedMap(dict):
    """A dict that counts the passes made over its items view, so a test can check that the string-table walk reads the view and does not copy it into a list."""

    def __init__(self, members) -> None:
        super().__init__(members)
        self.passes = 0

    def items(self) -> Any:
        return _CountedView(self, dict.items(self))


def test_the_tally_exists_only_when_the_variable_is_exactly_one():
    assert pile_tally.from_environment({}) is None
    assert pile_tally.from_environment({pile_tally.TALLY_ENV: "0"}) is None
    assert pile_tally.from_environment({pile_tally.TALLY_ENV: ""}) is None
    assert isinstance(pile_tally.from_environment({pile_tally.TALLY_ENV: "1"}), pile_tally.PileTally)
    assert pile_tally.TALLY_ENV == "AMS_SURFACE_PILE_TALLY"


def test_deep_size_enters_slots_and_dicts_and_charges_a_shared_object_once():
    shared = ("qsPea", "qsBay")
    rows = (_Row("default", shared), _Row("ss03", shared))
    unit = _Unit("E650:E651", rows, {"ink": True})
    seen: set[int] = set()
    shared_size = pile_tally.deep_size(shared, seen)
    rest = pile_tally.deep_size(unit, seen)
    assert shared_size > 0 and rest > 0
    assert pile_tally.deep_size(unit, set()) == shared_size + rest
    assert id(shared) in seen and id(rows[0]) in seen and id(unit.flags) in seen
    assert pile_tally.deep_size(unit, seen) == 0


def test_a_leaf_type_is_counted_shallow_so_one_pile_cannot_subsume_another():
    rows = tuple(_Row(f"config-{index}", ("qsPea",) * 40) for index in range(8))
    unit = _Unit("E650", rows, {})
    entered = pile_tally.deep_size(unit, set())
    stopped = pile_tally.deep_size(unit, set(), leaf_types=(_Row,))
    assert stopped < entered
    assert stopped >= pile_tally.deep_size(unit.rows, set(), leaf_types=(_Row,))


def test_estimate_scales_a_bounded_sample_by_the_count():
    pile = [_Unit(f"{index:04X}", (), {"n": index}) for index in range(4096)]
    count, sampled = pile_tally.estimate(pile, sample_size=64)
    _, walked = pile_tally.estimate(pile, sample_size=len(pile))
    assert count == 4096
    assert abs(sampled - walked) / walked < 0.05
    assert pile_tally.estimate([]) == (0, pile_tally.estimate([])[1])
    assert pile_tally.estimate({})[0] == 0


def test_a_nested_pile_samples_each_table_and_counts_their_rows():
    tables = {
        f"config-{index}": {f"{row:05X}": _Row("r", ("qsPea",)) for row in range(2000)} for index in range(3)
    }
    count, sampled = pile_tally.estimate(tables, sample_size=32, nested=True)
    _, walked = pile_tally.estimate(tables, sample_size=6000)
    assert count == 6000
    assert abs(sampled - walked) / walked < 0.05
    assert pile_tally.estimate({"empty": {}}, nested=True)[0] == 0
    assert pile_tally.estimate(["scalar", "members"], nested=True)[0] == 2


def test_a_mapping_is_sampled_as_items_so_keys_are_charged_too():
    values = {f"unit-{index:05d}": ("x",) for index in range(1000)}
    keys_only = pile_tally.estimate(list(values))[1]
    both = pile_tally.estimate(values)[1]
    assert both > keys_only


def test_a_boundary_prints_the_documented_lines_sorted_largest_first():
    out = io.StringIO()
    tally = pile_tally.PileTally(out=out)
    tally.hold("small", ["a"])
    tally.hold("large", [{"key": "value" * 50} for _ in range(300)])
    tally.hold_reading("ink.shape_memo", lambda: (12, 4_500_000_000))
    readings = tally.boundary("units")
    lines = out.getvalue().splitlines()
    assert [reading.pile for reading in readings] == ["ink.shape_memo", "large", "small"]
    piles = [_PILE_LINE.match(line) for line in lines[:-1]]
    assert all(piles)
    assert [match.group(2) for match in piles if match] == ["ink.shape_memo", "large", "small"]
    assert all(match.group(1) == "units" for match in piles if match)
    memo = piles[0]
    assert memo and (memo.group(3), memo.group(4), memo.group(5)) == ("12", "4500000000", "4.50")
    largest = _LARGEST_LINE.match(lines[-1])
    assert largest and largest.groups() == ("units", "ink.shape_memo")
    assert all(line.startswith(pile_tally.TALLY) for line in lines)


def test_a_column_census_charges_a_shared_string_table_to_one_line():
    """A packed pile's walked figure is its columns plus its string table, or its columns alone when another pile's line already holds the table (`holds_table=False`). The table is reported beside the packed figure in both cases, so the two readings differ only in `est_bytes`."""
    table = columns.StringTable()
    for value in ("ink", "picture", "junior"):
        table.id(value)
    rows = [array("I", [1, 2, 3]), bytearray(b"\x01\x02\x03")]
    strings = len("inkpicturejunior") + 3 * pile_tally.OFFSET_WIDTH
    held = pile_tally.column_census(3, rows, table, 4)
    shared = pile_tally.column_census(3, rows, table, 4, holds_table=False)
    assert held == pile_tally.Measure(3, 12 + 3 + 4 + strings, pile_tally.PackedCost(12 + 3 + 4, 3, strings))
    assert shared == pile_tally.Measure(3, 12 + 3 + 4, held.packed)


def test_an_empty_boundary_still_lands_on_the_record():
    out = io.StringIO()
    tally = pile_tally.PileTally(out=out)
    assert tally.boundary("manifest+check") == []
    assert out.getvalue() == "[tally] manifest+check largest=-\n"


def test_a_held_pile_is_reread_at_every_boundary_and_a_released_one_is_not():
    out = io.StringIO()
    tally = pile_tally.PileTally(out=out)
    pile: dict[str, str] = {}
    tally.hold("spooled", pile)
    tally.boundary("first")
    pile.update({str(index): "x" * 100 for index in range(50)})
    tally.boundary("second")
    tally.release("spooled")
    tally.boundary("third")
    lines = out.getvalue().splitlines()
    counts = [match.group(3) for match in map(_PILE_LINE.match, lines) if match]
    assert counts == ["0", "50"]
    assert lines[-1] == "[tally] third largest=-"


def test_a_packed_shape_prices_each_field_off_the_member_it_reads():
    for index in (0, 1, 5, 7, 1200):
        state = _state(index)
        assert pile_tally.packed_size(_STATE_SHAPE, state) == _packed_bytes(state)
    absent = pile_tally.packed_size(_STATE_SHAPE, None)
    assert absent == 1 / 8 + 4 + 5 + 5 + 4 + 5 + 32
    assert (
        pile_tally.packed_size(pile_tally.Positional((pile_tally.Slot(3), pile_tally.Flag())), None) == 3.125
    )
    assert pile_tally.packed_size(pile_tally.Derived(lambda text: 1 + 2 * len(text)), "abc") == 7


def test_a_drifted_declaration_raises_rather_than_pricing_the_wrong_field():
    with pytest.raises(TypeError):
        pile_tally.packed_size(pile_tally.Id(), 7)
    with pytest.raises(TypeError):
        pile_tally.packed_size(pile_tally.Many(pile_tally.Id()), "not a sequence")
    with pytest.raises(ValueError):
        pile_tally.packed_size(pile_tally.Positional((pile_tally.Slot(1),)), (1, 2))
    with pytest.raises(AttributeError):
        pile_tally.packed_size(pile_tally.Record({"missing": pile_tally.Slot(1)}), _state(0))


def test_a_hex_column_charges_its_width_whether_the_digest_is_there_or_not():
    """A member whose digest is None or empty costs the same bytes as one that has it. Otherwise the packed figure would jump by the column width whenever a boundary sampled a member that had no key yet. A digest of a different length raises, because it means the column was declared for the wrong field."""
    digest = pile_tally.Hex()
    assert pile_tally.packed_size(digest, "ab" * 32) == pile_tally.HEX_WIDTH == 32
    assert pile_tally.packed_size(digest, None) == 32
    assert pile_tally.packed_size(digest, "") == 32
    assert pile_tally.packed_size(pile_tally.Hex(8), "ab" * 8) == 8
    with pytest.raises(ValueError):
        pile_tally.packed_size(pile_tally.Hex(8), "ab" * 32)
    with pytest.raises(TypeError):
        pile_tally.packed_size(digest, 7)


def test_a_derived_column_prices_an_absent_value_without_reading_it():
    """An absent value is never passed to the callable and costs the column's `absent` width, by default the `COUNT_WIDTH` count slot. The build's `checker.identity` shape in rebuild/review/build.py is a codepoint window in a `Positional` triple, and a member with no window must cost a zero count there instead of raising."""
    window = pile_tally.Derived(lambda values: 1 + 2 * len(values))
    assert pile_tally.packed_size(window, (0xE650, 0xE651)) == 5
    assert pile_tally.packed_size(window, None) == pile_tally.COUNT_WIDTH
    assert pile_tally.packed_size(pile_tally.Derived(lambda values: 9, absent=2), None) == 2
    identity = pile_tally.Positional((window, pile_tally.Flag(), pile_tally.Flag()))
    assert pile_tally.packed_size(identity, None) == pile_tally.COUNT_WIDTH + 2 / 8


def test_a_nested_pile_takes_no_packed_shape_at_the_call_site_or_at_the_boundary():
    """A nested pile's count is the rows of its tables, while a packed shape describes one member, so a line with both would divide bytes by the wrong count. `measure` and `hold` raise at once, not at a later boundary."""
    tables = {"default": {"E650": _Row("r", ("qsPea",))}}
    with pytest.raises(ValueError):
        pile_tally.measure(tables, nested=True, packed=_STATE_SHAPE)
    with pytest.raises(ValueError):
        pile_tally.PileTally().hold("subset.rows", tables, nested=True, packed=_STATE_SHAPE)
    assert pile_tally.measure(tables, nested=True).count == 1


def test_a_pinned_record_prices_to_the_width_it_is_declared_at():
    """A member with one field of each shape kind costs a known number of bytes: eight flags take one byte, a digest its thirty-two raw bytes, a window a count byte plus two bytes per codepoint, an id four bytes, a variable-length field an offset and a count plus its elements, a mapping the same pair plus a key and a value per entry, and a positional address its three slots. An absent member costs every fixed column under it and the offset-and-count pair of every variable one."""
    assert pile_tally.packed_size(_PINNED_SHAPE, _pinned()) == 80
    assert pile_tally.packed_size(_PINNED_SHAPE, None) == 60
    cost = pile_tally.packed_estimate([_pinned()] * 64, _PINNED_SHAPE, sample_size=8)
    assert cost.est_bytes == 80 * 64
    assert cost.strings == 5
    assert cost.string_bytes == sum(
        len(string) + pile_tally.OFFSET_WIDTH
        for string in ("cluster-a", "qsPea", "qsBay", "default", "units/qsPea.json")
    )


def test_the_packed_rows_scale_with_the_sample_and_the_strings_are_counted_over_every_member():
    pile = [_state(index) for index in range(4096)]
    cost = pile_tally.packed_estimate(pile, _STATE_SHAPE, sample_size=64)
    walked = sum(map(_packed_bytes, pile))
    assert abs(cost.est_bytes - walked) / walked < 0.05
    strings = {state.digest for state in pile if state.digest} | {
        "qsPea",
        "qsBay",
        "qsTea",
        "default",
        "ss03",
        "delta-a",
        "delta-b",
        "ss10",
    }
    assert cost.strings == len(strings) == 1000 + 1 + 8
    assert cost.string_bytes == sum(len(string) + pile_tally.OFFSET_WIDTH for string in strings)
    assert pile_tally.packed_estimate([], _STATE_SHAPE) == pile_tally.PackedCost(0, 0, 0)


def test_the_bare_ids_of_one_record_come_off_one_pass_over_the_members():
    """The string table is counted over every member, so each id column costs a pass over the whole pile. The bare `Id` fields of one record share a getter and so share one pass. Here the three id columns take two passes, plus one pass to take the sample."""
    shape = pile_tally.Record(
        {
            "cluster": pile_tally.Id(),
            "key": pile_tally.Id(),
            "names": pile_tally.Many(pile_tally.Id()),
        }
    )
    pile = _CountedPile(_pinned() for _ in range(64))
    cost = pile_tally.packed_estimate(pile, shape, sample_size=8)
    assert pile.passes == 3
    strings = {"cluster-a", "ab" * 32, "qsPea", "qsBay"}
    assert cost.strings == len(strings)
    assert cost.string_bytes == sum(len(string) + pile_tally.OFFSET_WIDTH for string in strings)


def test_a_mapping_is_priced_by_its_values_unless_a_table_prices_its_entries():
    pile = {f"{index:064x}": _state(index) for index in range(512)}
    by_ordinal = pile_tally.packed_estimate(pile, _STATE_SHAPE, sample_size=512)
    by_key = pile_tally.packed_estimate(
        pile, pile_tally.Table(pile_tally.Hex(), _STATE_SHAPE), sample_size=512
    )
    assert by_ordinal.est_bytes == round(sum(map(_packed_bytes, pile.values())))
    assert by_key.est_bytes == by_ordinal.est_bytes + 32 * 512
    assert by_key.strings == by_ordinal.strings
    named = pile_tally.packed_estimate(
        {"a": "x", "b": "x", "c": None}, pile_tally.Table(pile_tally.Id(), pile_tally.Id())
    )
    assert named == pile_tally.PackedCost(24, 4, sum(len(s) + 4 for s in "abcx"))


def test_the_string_table_reads_the_items_view_rather_than_a_list_of_the_entries():
    """The tally runs inside the process it measures, so the string-table walk must not hold anything per member. It reads the mapping's items view once per id column; copying the entries into a list first would add about 70 MB of tuples for a pile of a million units. Here there are five id columns plus the pass that takes the sample."""
    pile = _CountedMap({f"{index:064x}": _state(index) for index in range(64)})
    cost = pile_tally.packed_estimate(pile, pile_tally.Table(pile_tally.Hex(), _STATE_SHAPE), sample_size=8)
    assert pile.passes == 6
    assert cost.strings > 0


def test_a_pile_with_a_packed_shape_prints_the_documented_tokens_and_one_without_prints_none():
    out = io.StringIO()
    tally = pile_tally.PileTally(out=out, sample_size=64)
    tally.hold("states", [_state(index) for index in range(300)], packed=_STATE_SHAPE)
    tally.hold("plain", [_state(index) for index in range(3)])
    tally.hold("empty", {}, packed=pile_tally.Table(pile_tally.Hex(), _STATE_SHAPE))
    tally.hold_reading(
        "rows",
        lambda: pile_tally.measure(
            [_Row("ss03", ("qsPea",))], packed=pile_tally.Record({"label": pile_tally.Id()})
        ),
    )
    tally.hold_reading("ink.shape_memo", lambda: (12, 4_500_000_000))
    readings = {reading.pile: reading for reading in tally.boundary("units")}
    lines = {line.split()[2]: line for line in out.getvalue().splitlines()[:-1]}
    assert _PILE_LINE.match(lines["plain"]) and _PILE_LINE.match(lines["ink.shape_memo"])
    assert readings["plain"].packed is None and readings["ink.shape_memo"].packed is None
    states = _PACKED_LINE.match(lines["states"])
    assert states and states.group(3) == "300"
    packed = readings["states"].packed
    assert packed and states.group(6) == str(packed.est_bytes)
    assert states.group(7) == f"{packed.est_bytes / 300:.1f}"
    assert states.group(8) == f"{readings['states'].est_bytes / 300:.1f}"
    assert states.group(9) == f"{readings['states'].est_bytes / packed.est_bytes:.2f}"
    assert (states.group(10), states.group(11)) == (str(packed.strings), str(packed.string_bytes))
    assert float(states.group(9)) > 1
    rows = _PACKED_LINE.match(lines["rows"])
    assert rows and rows.group(6) == "4" and rows.group(10) == "1" and rows.group(11) == str(len("ss03") + 4)
    empty = _PACKED_LINE.match(lines["empty"])
    assert empty and empty.groups()[5:] == ("0", "0.0", "0.0", "-", "0", "0")
    assert all(line.startswith(pile_tally.TALLY) for line in out.getvalue().splitlines())
