"""Tests for the two serializations at the kernel boundary in `rebuild/pipeline/kernel_io.py`.

The resolved-spec dump is how the Rust kernel reads a spec. These tests cover the value round trip, canonical form, the collection order the dump preserves, and the errors that stop a wrong dump from parsing as a partial one. They run on the mini fixture, widened by `_reaching_mini` so the encoder meets every shape it meets on the live alphabet, which `TestTheMiniReachesEveryShapeTheLiveDumpDoes` checks. The live alphabet is used there and in `TestTheCrateEchoesTheDumpByteForByte`, where the dump goes through the crate's `spec-echo` and must come back byte for byte; that is where a Rust model lagging a `model.py` change shows up.

The transition stream is the return direction. Its tests write a fixpoint product, parse it back, compare the two field by field and row by row, and check the wire layout against the raw bytes. They use the mini spec alone, because they test the format.
"""

import dataclasses
import gzip
import json
import warnings

import pytest

from rebuild.pipeline import fixtures, kernel_exec, kernel_io, spec_load
from rebuild.pipeline import table as table_module
from rebuild.pipeline.kernel_exec import enumerate_transitions
from rebuild.pipeline.model import (
    Bitmap,
    Condition,
    Pairing,
    PolicyRecord,
    ResolvedSpec,
    Rune,
    SurfaceRow,
    Unlock,
    When,
)

MINI = fixtures.mini_spec()
CONFIGS = {"default": frozenset(), "ss03": frozenset({"ss03"}), "ss04": frozenset({"ss04"})}
CONTEXT = 48


def _reaching_mini() -> ResolvedSpec:
    """The mini fixture plus three encoder shapes it does not reach by itself: a populated `Bitmap | None` (`Rune.mono`), a `When | None` set to None (an `Unlock` with no `when`), and a populated `tuple[str, str | None]` (a resolve record's `against`, as in the live `qsTea_qsOy`). They are added here, not in `fixtures.mini_spec`, because an unconditional unlock and a resolve record change settlement and would move every mini-built table and settle test; this spec is only dumped, never settled."""
    spec = fixtures.mini_spec()
    runes = dict(spec.runes)
    it = runes["qsIt"]
    hapax = it.stances["hapax"]
    surface = dataclasses.replace(
        hapax.surface,
        unlocks=(
            *hapax.surface.unlocks,
            Unlock(feature="ss04", pairing=Pairing("baseline", "baseline")),
        ),
    )
    runes["qsIt"] = dataclasses.replace(
        it,
        mono=Bitmap(("#",) * 6),
        stances={**it.stances, "hapax": dataclasses.replace(hapax, surface=surface)},
    )
    tea_oy = runes["qsTea_qsOy"]
    runes["qsTea_qsOy"] = dataclasses.replace(
        tea_oy,
        policy=dataclasses.replace(
            tea_oy.policy,
            resolve=(
                PolicyRecord(
                    kind="resolve",
                    against=("qsIt", "withhold-before-no-after-oy"),
                    when=When(right=Condition(family=("qsIt",), then=Condition(family=("qsDay",)))),
                    pick={"exit": "baseline"},
                ),
            ),
        ),
    )
    return dataclasses.replace(spec, runes=runes)


SPEC = _reaching_mini()


def _first_difference(written: bytes, echoed: bytes) -> str:
    """Describe where two byte strings first differ: the offset of the first differing byte, or the shorter length when one is a prefix of the other, with the surrounding bytes of each."""
    shared = min(len(written), len(echoed))
    offset = next((index for index in range(shared) if written[index] != echoed[index]), shared)
    start = max(0, offset - CONTEXT)
    stop = offset + CONTEXT
    return (
        f"first difference at byte {offset} of {len(written)} written, {len(echoed)} echoed\n"
        f"  python[{start}:{stop}] {written[start:stop]!r}\n"
        f"  kernel[{start}:{stop}] {echoed[start:stop]!r}"
    )


@pytest.fixture(scope="module")
def spec() -> ResolvedSpec:
    return SPEC


@pytest.fixture(scope="module")
def live_spec() -> ResolvedSpec:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", spec_load.SpecWarning)
        return spec_load.load_default_spec()


def _multi_stance_rune(spec: ResolvedSpec) -> tuple[str, Rune]:
    for name, rune in spec.runes.items():
        if len(rune.stances) > 1:
            return name, rune
    pytest.fail("the spec has no rune with more than one stance, so stance order proves nothing")


def _multi_exit_stance(spec: ResolvedSpec) -> tuple[str, str]:
    for rune_name, rune in spec.runes.items():
        for stance_name, stance in rune.stances.items():
            if len(stance.surface.exits) > 1:
                return rune_name, stance_name
    pytest.fail("the spec has no stance with more than one exit, so exit order proves nothing")


def _first_entry_row(spec: ResolvedSpec) -> tuple[str, str, str, SurfaceRow]:
    for rune_name, rune in spec.runes.items():
        for stance_name, stance in rune.stances.items():
            for height, row in stance.surface.entries.items():
                return rune_name, stance_name, height, row
    pytest.fail("the spec has no entry row to mutate")


def _replace_stances(spec: ResolvedSpec, rune_name: str, stances) -> ResolvedSpec:
    runes = dict(spec.runes)
    runes[rune_name] = dataclasses.replace(spec.runes[rune_name], stances=stances)
    return dataclasses.replace(spec, runes=runes)


class TestRoundTrip:
    def test_the_dump_parses_back_to_an_equal_spec(self, spec):
        assert kernel_io.spec_of(kernel_io.spec_json(spec)) == spec

    def test_dumping_a_parsed_dump_returns_the_same_text(self, spec):
        text = kernel_io.spec_json(spec)
        assert kernel_io.spec_json(kernel_io.spec_of(text)) == text

    def test_two_dumps_of_one_spec_are_identical(self, spec):
        assert kernel_io.spec_json(spec) == kernel_io.spec_json(spec)

    def test_the_dump_is_in_canonical_json_form(self, spec):
        """Checks the two parts of canonical form a value round trip cannot see: compact separators and ASCII-only text. Re-encoding the parsed payload with those settings must reproduce the dump byte for byte. The ASCII check can fail: with `ensure_ascii=False` the same payload is not ASCII, because the prose in the spec contains `·` wherever it names a letter."""
        text = kernel_io.spec_json(spec)
        assert text.isascii()
        assert not json.dumps(json.loads(text), ensure_ascii=False).isascii()
        assert text == json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=True)

    def test_the_dump_declares_its_format_first(self, spec):
        payload = json.loads(kernel_io.spec_json(spec))
        assert list(payload) == ["format", "runes", "registry"]
        assert payload["format"] == kernel_io.SPEC_FORMAT

    def test_a_written_file_reads_back_as_the_same_spec(self, spec, tmp_path):
        path = tmp_path / "nested" / "spec.json"
        kernel_io.write_spec(spec, path)
        assert kernel_io.read_spec(path) == spec

    def test_the_declared_container_types_come_back(self, spec):
        parsed = kernel_io.spec_of(kernel_io.spec_json(spec))
        rune = next(iter(parsed.runes.values()))
        assert isinstance(rune.stances[rune.default_stance].traits, tuple)
        assert all(isinstance(members, frozenset) for members in parsed.registry.predicate_classes.values())
        grouped = [rune for rune in parsed.runes.values() if rune.policy.groups]
        assert grouped, "the spec resolves no rune-local groups, so the frozenset shape proves nothing"
        assert all(isinstance(members, frozenset) for members in grouped[0].policy.groups.values())


class TestOrderIsPreserved:
    def test_the_runes_keep_their_resolved_order(self, spec):
        parsed = kernel_io.spec_of(kernel_io.spec_json(spec))
        assert list(parsed.runes) == list(spec.runes)

    def test_a_runes_stances_keep_their_declaration_order(self, spec):
        name, rune = _multi_stance_rune(spec)
        parsed = kernel_io.spec_of(kernel_io.spec_json(spec))
        assert list(parsed.runes[name].stances) == list(rune.stances)

    def test_a_stances_exits_keep_their_declaration_order(self, spec):
        rune_name, stance_name = _multi_exit_stance(spec)
        parsed = kernel_io.spec_of(kernel_io.spec_json(spec))
        expected = list(spec.runes[rune_name].stances[stance_name].surface.exits)
        assert list(parsed.runes[rune_name].stances[stance_name].surface.exits) == expected

    def test_the_registry_heights_keep_their_order(self, spec):
        parsed = kernel_io.spec_of(kernel_io.spec_json(spec))
        assert list(parsed.registry.heights) == list(spec.registry.heights)

    def test_reordering_stances_moves_the_dump(self, spec):
        name, rune = _multi_stance_rune(spec)
        reversed_stances = dict(reversed(list(rune.stances.items())))
        shuffled = _replace_stances(spec, name, reversed_stances)
        assert kernel_io.spec_json(shuffled) != kernel_io.spec_json(spec)
        assert list(kernel_io.spec_of(kernel_io.spec_json(shuffled)).runes[name].stances) == list(
            reversed_stances
        )


class TestTheDumpSeesTheWholeTree:
    def test_a_field_deep_in_the_tree_moves_the_dump(self, spec):
        rune_name, stance_name, height, row = _first_entry_row(spec)
        rune = spec.runes[rune_name]
        stance = rune.stances[stance_name]
        entries = dict(stance.surface.entries)
        entries[height] = dataclasses.replace(row, x=row.x + 1)
        stances = dict(rune.stances)
        stances[stance_name] = dataclasses.replace(
            stance, surface=dataclasses.replace(stance.surface, entries=entries)
        )
        moved = _replace_stances(spec, rune_name, stances)
        assert kernel_io.spec_json(moved) != kernel_io.spec_json(spec)
        assert kernel_io.spec_of(kernel_io.spec_json(moved)) == moved

    def test_prose_rides_along(self, spec):
        parsed = kernel_io.spec_of(kernel_io.spec_json(spec))
        assert all(parsed.runes[name].ductus == rune.ductus for name, rune in spec.runes.items())
        assert all(parsed.runes[name].notes == rune.notes for name, rune in spec.runes.items())


class TestAnOutgrownCodecFailsLoudly:
    """The codec reads `model.py` through its type hints, not a field list. These tests call the encoder directly with shapes no current field has, so a new field of an unsupported shape raises `TypeError` instead of being dropped from the dump."""

    def test_a_container_shape_with_no_rule_is_refused(self):
        @dataclasses.dataclass(frozen=True)
        class Outgrown:
            items: list[str]

        with pytest.raises(TypeError):
            kernel_io._encode(Outgrown, Outgrown(["a"]))

    def test_a_union_that_is_not_merely_optional_is_refused(self):
        @dataclasses.dataclass(frozen=True)
        class Widened:
            pick: int | str | None

        with pytest.raises(TypeError):
            kernel_io._encode(Widened, Widened(3))


class TestRefusals:
    def test_text_without_a_format_marker_is_refused(self):
        with pytest.raises(ValueError):
            kernel_io.spec_of(json.dumps({"runes": {}, "registry": {}}))

    def test_text_marked_as_another_format_is_refused(self):
        with pytest.raises(ValueError):
            kernel_io.spec_of(json.dumps({"format": "ams-m1-spec/0", "runes": {}, "registry": {}}))

    def test_text_that_is_not_an_object_is_refused(self):
        with pytest.raises(ValueError):
            kernel_io.spec_of(json.dumps([kernel_io.SPEC_FORMAT]))

    def test_a_record_missing_a_field_is_refused(self, spec):
        payload = json.loads(kernel_io.spec_json(spec))
        rune = next(iter(payload["runes"].values()))
        del rune["notes"]
        with pytest.raises(ValueError):
            kernel_io.spec_of(json.dumps(payload))

    def test_a_record_carrying_an_unknown_field_is_refused(self, spec):
        payload = json.loads(kernel_io.spec_json(spec))
        next(iter(payload["runes"].values()))["ligature"] = None
        with pytest.raises(ValueError):
            kernel_io.spec_of(json.dumps(payload))


class TestTheMiniReachesEveryShapeTheLiveDumpDoes:
    """The codec reads `model.py` through its type hints, so which codec paths are tested depends on which hints the encoder receives. This test spies on `_encode` and fails if the live dump reaches a shape (a hint, and whether its value is None) that the widened mini does not. That lets the tests above run on the mini alone."""

    def test_the_widened_mini_hands_the_encoder_every_live_shape(self, monkeypatch, live_spec):
        original = kernel_io._encode
        seen: set[tuple[str, bool]] = set()

        def spy(hint, value):
            seen.add((repr(hint), value is None))
            return original(hint, value)

        monkeypatch.setattr(kernel_io, "_encode", spy)

        def reach(target: ResolvedSpec) -> set[tuple[str, bool]]:
            seen.clear()
            kernel_io.spec_json(target)
            return set(seen)

        missing = reach(live_spec) - reach(SPEC)
        assert (
            missing == set()
        ), f"the live dump hands the encoder shapes the mini never does: {sorted(missing)} — widen `_reaching_mini` until it reaches them, or those codec paths go untested"


@pytest.mark.parametrize("arm", ["mini", "live"])
class TestTheCrateEchoesTheDumpByteForByte:
    """Checks that the crate's spec ingest loses nothing: `spec-echo` parses the dump into the crate's model and emits it again from that model alone, so a field the model does not carry, a reordered mapping, or a differently escaped string shows up as a byte difference. A `model.py` change the crate has not followed fails here. The mini case runs beside the live one because it is cheap and exercises the crate's `against`, `mono`, and `when: null` emit paths even if the live alphabet has none of those shapes. The spawn goes through `kernel_exec._run_kernel` so the uplift lock orders it against a concurrent `ensure_built`."""

    def test_the_dump_comes_back_out_of_the_binary_unchanged(self, arm, live_spec, tmp_path):
        subject = SPEC if arm == "mini" else live_spec
        path = tmp_path / f"spec-{arm}.json"
        kernel_io.write_spec(subject, path)
        kernel_exec.ensure_built()
        finished = kernel_exec._run_kernel([str(kernel_exec.BINARY), "spec-echo", str(path)], "spec-echo")
        written = path.read_bytes()
        assert (
            finished.returncode == 0
        ), f"the kernel exited {finished.returncode}: {finished.stderr.decode(errors='replace').strip()}"
        assert (
            finished.stderr == b""
        ), f"the kernel wrote to stderr on a clean exit: {finished.stderr.decode(errors='replace').strip()}"
        assert finished.stdout == written, _first_difference(written, finished.stdout)


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    directory = tmp_path_factory.mktemp("transitions")
    written = {}
    for name, features in CONFIGS.items():
        product = enumerate_transitions(MINI, features)
        path = directory / f"transitions-{name}.gz"
        kernel_io.write_transitions(product, path)
        written[name] = (product, path)
    return written


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheTransitionStreamCarriesTheWholeProduct:
    def test_a_parsed_stream_is_the_product_that_was_written(self, stream, config):
        product, path = stream[config]
        parsed = kernel_io.read_transitions(path)
        assert parsed.config == product.config
        assert parsed.transitions == product.transitions
        assert parsed.deep_classes == product.deep_classes
        assert parsed.cited_provenance == product.cited_provenance
        assert parsed.cells == product.cells
        assert parsed == product

    def test_the_rows_come_back_in_the_order_they_were_written(self, stream, config):
        product, path = stream[config]
        parsed = kernel_io.read_transitions(path)
        assert [row.key for row in parsed.transitions] == [row.key for row in product.transitions]

    def test_two_writes_of_one_product_are_identical(self, stream, config, tmp_path):
        product, path = stream[config]
        again = tmp_path / "again.gz"
        kernel_io.write_transitions(product, again)
        assert again.read_bytes() == path.read_bytes()

    def test_an_open_plain_handle_parses_to_the_same_product(self, stream, config, tmp_path):
        """`kernel_exec.read_stream` passes `read_transitions` an open handle on the crate's plain ndjson stream. The bytes are the same as the gzip file's contents, so the product must be the same."""
        product, path = stream[config]
        plain = tmp_path / f"transitions-{config}.ndjson"
        with gzip.open(path, "rb") as packed:
            plain.write_bytes(packed.read())
        with plain.open("rt", encoding="utf-8") as handle:
            assert kernel_io.read_transitions(handle) == product


class TestTheWireLayoutIsTheDocumentedOne:
    """A round trip cannot catch a Python writer and reader that changed together, and the crate writes the same `ams-m1-transitions/1` format, so this test checks the raw bytes against the layout the `kernel_io` module docstring documents: the marker line, the head's keys in order, the head's cell format, and all twelve row positions."""

    def test_the_stream_spells_the_layout_the_contract_names(self, stream):
        product, path = stream["default"]
        with gzip.open(path, "rt") as handle:
            marker, _, payload = handle.readline().rstrip("\n").partition("\t")
            head = json.loads(payload)
            first = json.loads(handle.readline())
        assert marker == f"# {kernel_io.TRANSITIONS_FORMAT}"
        assert list(head) == ["config", "cells", "deep_classes", "cited_provenance"]
        cells = sorted(product.cells, key=table_module._cell_key)
        lead = cells[0]
        assert head["cells"][0] == [lead.rune, lead.stance, lead.entry, lead.exit, list(lead.adjustments)]
        row = product.transitions[0]
        assert len(first) == 12
        assert first[:7] == [
            row.input_glyph,
            row.left,
            row.right1,
            row.right2,
            row.right3,
            row.right4,
            row.outcome,
        ]
        assert first[7] == [cells.index(row.settled.cell), row.settled.seam, row.settled.extension]
        assert first[8] == (
            None
            if row.left_settled is None
            else [cells.index(row.left_settled.cell), row.left_settled.seam, row.left_settled.extension]
        )
        assert first[9] == row.joint
        assert first[10] == row.prospect
        assert first[11] == list(row.provenance)


class TestTheStreamRefusesWhatItCannotCarry:
    def test_a_gzip_file_that_is_not_a_stream_is_refused(self, tmp_path):
        path = tmp_path / "elsewhere.gz"
        with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
            handle.write(b'# ams-m1-windows/1\t{"config":"default"}\n')
        with pytest.raises(ValueError):
            kernel_io.read_transitions(path)

    def test_an_absent_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            kernel_io.read_transitions(tmp_path / "never-written.gz")

    def test_a_row_settling_outside_the_products_cells_is_refused(self, stream, tmp_path):
        product, _path = stream["default"]
        starved = dataclasses.replace(
            product, cells=frozenset(product.cells - {product.transitions[0].settled.cell})
        )
        with pytest.raises(table_module.PartitionError):
            kernel_io.write_transitions(starved, tmp_path / "starved.gz")
