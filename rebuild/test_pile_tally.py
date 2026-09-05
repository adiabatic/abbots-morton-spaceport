"""The debug pile tally: off unless asked for by exactly the documented variable, an estimate that is a scaled sample rather than a full walk, and the one line format the module docstring promises a grep can read back."""

import io
import re
from dataclasses import dataclass

from rebuild.tools import pile_tally

_PILE_LINE = re.compile(r"^\[tally\] (\S+) (\S+) count=(\d+) est_bytes=(\d+) est_gb=(\d+\.\d\d)$")
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
