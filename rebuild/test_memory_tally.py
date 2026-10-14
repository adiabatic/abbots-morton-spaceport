"""Tests for the debug memory tally in rebuild/tools/memory_tally.py: it is off unless `AMS_CORPUS_MEMORY_TALLY` is exactly `1`, its estimates scale a sample by the count, and its lines match the formats the module docstring documents."""

import io
import re
from array import array
from dataclasses import dataclass

from rebuild.review import columns
from rebuild.tools import memory_tally

_COLLECTION_LINE = re.compile(r"^\[tally\] (\S+) (\S+) count=(\d+) est_bytes=(\d+) est_gb=(\d+\.\d\d)$")
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


def test_the_tally_exists_only_when_the_variable_is_exactly_one():
    assert memory_tally.from_environment({}) is None
    assert memory_tally.from_environment({memory_tally.TALLY_ENV: "0"}) is None
    assert memory_tally.from_environment({memory_tally.TALLY_ENV: ""}) is None
    assert isinstance(memory_tally.from_environment({memory_tally.TALLY_ENV: "1"}), memory_tally.MemoryTally)
    assert memory_tally.TALLY_ENV == "AMS_CORPUS_MEMORY_TALLY"


def test_deep_size_enters_slots_and_dicts_and_charges_a_shared_object_once():
    shared = ("qsPea", "qsBay")
    rows = (_Row("default", shared), _Row("ss03", shared))
    unit = _Unit("E650:E651", rows, {"ink": True})
    seen: set[int] = set()
    shared_size = memory_tally.deep_size(shared, seen)
    rest = memory_tally.deep_size(unit, seen)
    assert shared_size > 0 and rest > 0
    assert memory_tally.deep_size(unit, set()) == shared_size + rest
    assert id(shared) in seen and id(rows[0]) in seen and id(unit.flags) in seen
    assert memory_tally.deep_size(unit, seen) == 0


def test_a_leaf_type_is_counted_shallow_so_one_collection_cannot_subsume_another():
    rows = tuple(_Row(f"config-{index}", ("qsPea",) * 40) for index in range(8))
    unit = _Unit("E650", rows, {})
    entered = memory_tally.deep_size(unit, set())
    stopped = memory_tally.deep_size(unit, set(), leaf_types=(_Row,))
    assert stopped < entered
    assert stopped >= memory_tally.deep_size(unit.rows, set(), leaf_types=(_Row,))


def test_estimate_scales_a_bounded_sample_by_the_count():
    collection = [_Unit(f"{index:04X}", (), {"n": index}) for index in range(4096)]
    count, sampled = memory_tally.estimate(collection, sample_size=64)
    _, walked = memory_tally.estimate(collection, sample_size=len(collection))
    assert count == 4096
    assert abs(sampled - walked) / walked < 0.05
    assert memory_tally.estimate([]) == (0, memory_tally.estimate([])[1])
    assert memory_tally.estimate({})[0] == 0


def test_a_nested_collection_samples_each_table_and_counts_their_rows():
    tables = {
        f"config-{index}": {f"{row:05X}": _Row("r", ("qsPea",)) for row in range(2000)} for index in range(3)
    }
    count, sampled = memory_tally.estimate(tables, sample_size=32, nested=True)
    _, walked = memory_tally.estimate(tables, sample_size=6000)
    assert count == 6000
    assert abs(sampled - walked) / walked < 0.05
    assert memory_tally.estimate({"empty": {}}, nested=True)[0] == 0
    assert memory_tally.estimate(["scalar", "members"], nested=True)[0] == 2


def test_a_mapping_is_sampled_as_items_so_keys_are_charged_too():
    values = {f"unit-{index:05d}": ("x",) for index in range(1000)}
    keys_only = memory_tally.estimate(list(values))[1]
    both = memory_tally.estimate(values)[1]
    assert both > keys_only


def test_a_boundary_prints_the_documented_lines_sorted_largest_first():
    out = io.StringIO()
    tally = memory_tally.MemoryTally(out=out)
    tally.hold("small", ["a"])
    tally.hold("large", [{"key": "value" * 50} for _ in range(300)])
    tally.hold_reading("ink.shape_memo", lambda: (12, 4_500_000_000))
    readings = tally.boundary("units")
    lines = out.getvalue().splitlines()
    assert [reading.collection for reading in readings] == ["ink.shape_memo", "large", "small"]
    collections = [_COLLECTION_LINE.match(line) for line in lines[:-1]]
    assert all(collections)
    assert [match.group(2) for match in collections if match] == ["ink.shape_memo", "large", "small"]
    assert all(match.group(1) == "units" for match in collections if match)
    memo = collections[0]
    assert memo and (memo.group(3), memo.group(4), memo.group(5)) == ("12", "4500000000", "4.50")
    largest = _LARGEST_LINE.match(lines[-1])
    assert largest and largest.groups() == ("units", "ink.shape_memo")
    assert all(line.startswith(memory_tally.TALLY) for line in lines)


def test_a_column_size_estimate_charges_a_shared_string_table_to_one_line():
    """A packed collection's walked figure is its columns plus its string table, or its columns alone when another collection's line already holds the table (`holds_table=False`). The table is reported beside the packed figure in both cases, so the two readings differ only in `est_bytes`."""
    table = columns.StringTable()
    for value in ("ink", "picture", "junior"):
        table.id(value)
    rows = [array("I", [1, 2, 3]), bytearray(b"\x01\x02\x03")]
    strings = len("inkpicturejunior") + 3 * memory_tally.OFFSET_WIDTH
    held = memory_tally.column_sizes(3, rows, table, 4)
    shared = memory_tally.column_sizes(3, rows, table, 4, holds_table=False)
    assert held == memory_tally.Measure(
        3, 12 + 3 + 4 + strings, memory_tally.PackedCost(12 + 3 + 4, 3, strings)
    )
    assert shared == memory_tally.Measure(3, 12 + 3 + 4, held.packed)


def test_an_empty_boundary_is_still_written_to_the_record():
    out = io.StringIO()
    tally = memory_tally.MemoryTally(out=out)
    assert tally.boundary("manifest+check") == []
    assert out.getvalue() == "[tally] manifest+check largest=-\n"


def test_a_held_collection_is_reread_at_every_boundary_and_a_released_one_is_not():
    out = io.StringIO()
    tally = memory_tally.MemoryTally(out=out)
    collection: dict[str, str] = {}
    tally.hold("spooled", collection)
    tally.boundary("first")
    collection.update({str(index): "x" * 100 for index in range(50)})
    tally.boundary("second")
    tally.release("spooled")
    tally.boundary("third")
    lines = out.getvalue().splitlines()
    counts = [match.group(3) for match in map(_COLLECTION_LINE.match, lines) if match]
    assert counts == ["0", "50"]
    assert lines[-1] == "[tally] third largest=-"


def test_a_column_reading_prints_the_documented_tokens_and_a_walked_one_prints_none():
    table = columns.StringTable()
    for value in ("ink", "picture", "junior"):
        table.id(value)
    rows = [array("I", [1, 2, 3]), bytearray(b"\x01\x02\x03")]
    strings = len("inkpicturejunior") + 3 * memory_tally.OFFSET_WIDTH
    out = io.StringIO()
    tally = memory_tally.MemoryTally(out=out)
    tally.hold_reading("store", lambda: memory_tally.column_sizes(3, rows, table, 4))
    tally.hold_reading("shared", lambda: memory_tally.column_sizes(3, rows, table, holds_table=False))
    tally.hold_reading("empty", lambda: memory_tally.Measure(0, 0, memory_tally.PackedCost(0, 0, 0)))
    tally.hold("plain", [_Row("ss03", ("qsPea",))] * 3)
    tally.hold_reading("ink.shape_memo", lambda: (12, 4_500_000_000))
    readings = {reading.collection: reading for reading in tally.boundary("units")}
    lines = {line.split()[2]: line for line in out.getvalue().splitlines()[:-1]}
    assert _COLLECTION_LINE.match(lines["plain"]) and _COLLECTION_LINE.match(lines["ink.shape_memo"])
    assert readings["plain"].packed is None and readings["ink.shape_memo"].packed is None
    store = _PACKED_LINE.match(lines["store"])
    packed_bytes = 12 + 3 + 4
    assert store and store.group(3, 4, 6) == ("3", str(packed_bytes + strings), str(packed_bytes))
    assert store.group(7, 8) == (f"{packed_bytes / 3:.1f}", f"{(packed_bytes + strings) / 3:.1f}")
    assert store.group(9) == f"{(packed_bytes + strings) / packed_bytes:.2f}"
    assert store.group(10, 11) == ("3", str(strings))
    shared = _PACKED_LINE.match(lines["shared"])
    assert shared and shared.group(4) == shared.group(6) == str(12 + 3) and shared.group(9) == "1.00"
    empty = _PACKED_LINE.match(lines["empty"])
    assert empty and empty.groups()[5:] == ("0", "0.0", "0.0", "-", "0", "0")
    assert all(line.startswith(memory_tally.TALLY) for line in out.getvalue().splitlines())
