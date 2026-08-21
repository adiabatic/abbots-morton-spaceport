"""Both serializations at the kernel boundary. The resolved-spec dump is the leg the Rust settlement kernel will read a spec through — value round trip, canonical fixpoint, the collection order the dump promises to preserve, and the loud refusals that keep a wrong dump from parsing as a partial one; both specs are exercised there, the mini fixture for reach into hand-built corners and the live alphabet because that is the tree the port actually carries. The transition stream is the return leg, and its test is the sub-issue's own proof: write a fixpoint product, parse it back, hand the parsed value to `assemble_tables`, and get the tables a straight-through build produces down to the serialized bytes — the round trip is the format's proof, and it is what licensed the kernel that produces the stream to replace the half that once enumerated it. The stream runs on the mini spec alone, because what it proves is a property of the format rather than of any one alphabet."""

import dataclasses
import gzip
import json

import pytest

from rebuild.pipeline import fixtures, kernel_io, spec_load
from rebuild.pipeline import table as table_module
from rebuild.pipeline.kernel_exec import build_tables, enumerate_transitions
from rebuild.pipeline.model import ResolvedSpec, Rune, SurfaceRow
from rebuild.pipeline.table import assemble_tables

MINI = fixtures.mini_spec()
CONFIGS = {"default": frozenset(), "ss03": frozenset({"ss03"}), "ss04": frozenset({"ss04"})}
PINNED_STAMP = "kernel-io-pinned-stamp"


@pytest.fixture(scope="module", params=["mini", "live"])
def spec(request) -> ResolvedSpec:
    return fixtures.mini_spec() if request.param == "mini" else spec_load.load_default_spec()


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
        """The two canonicalization clauses a value round trip cannot see: compact separators and ASCII-only text. Re-encoding the parsed payload under exactly those settings must reproduce the dump byte for byte — the live spec carries enough non-ASCII prose to make the escape clause load-bearing."""
        text = kernel_io.spec_json(spec)
        assert text.isascii()
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
    """`model.py` is a cross-group contract that will keep growing, and the codec reads it through its type hints rather than a field list. These reach past `spec_json` into the encoder because the shapes they exercise are ones no current field has — the point is that adding one raises here rather than dumping a spec with the field quietly missing."""

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


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    directory = tmp_path_factory.mktemp("transitions")
    written = {}
    for name, features in CONFIGS.items():
        product = enumerate_transitions(MINI, features)
        path = directory / f"transitions-{name}.gz"
        kernel_io.write_transitions(product, path)
        written[name] = (product, path, build_tables(MINI, features))
    return written


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheTransitionStreamCarriesTheWholeProduct:
    def test_a_parsed_stream_is_the_product_that_was_written(self, stream, config):
        product, path, _tables = stream[config]
        parsed = kernel_io.read_transitions(path)
        assert parsed.config == product.config
        assert parsed.transitions == product.transitions
        assert parsed.deep_classes == product.deep_classes
        assert parsed.cited_provenance == product.cited_provenance
        assert parsed.cells == product.cells
        assert parsed == product

    def test_the_rows_come_back_in_the_order_they_were_written(self, stream, config):
        product, path, _tables = stream[config]
        parsed = kernel_io.read_transitions(path)
        assert [row.key for row in parsed.transitions] == [row.key for row in product.transitions]

    def test_two_writes_of_one_product_are_identical(self, stream, config, tmp_path):
        product, path, _tables = stream[config]
        again = tmp_path / "again.gz"
        kernel_io.write_transitions(product, again)
        assert again.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestAParsedStreamAssemblesTheSameTables:
    def test_the_tables_the_stream_folds_into_are_the_built_ones(self, stream, config):
        _product, path, (built, built_treaty) = stream[config]
        seamed, seam_treaty = assemble_tables(MINI, kernel_io.read_transitions(path))
        assert seamed.config == built.config
        assert seamed.transitions == built.transitions
        assert seamed.rules == built.rules
        assert seamed.identity_guard_rules == built.identity_guard_rules
        assert seamed.cited_provenance == built.cited_provenance
        assert seamed.deep_classes == built.deep_classes
        assert seamed.reachable_cells() == built.reachable_cells()
        assert seam_treaty.rows == built_treaty.rows

    def test_the_artifacts_the_stream_folds_into_are_byte_identical(self, stream, config, tmp_path):
        _product, path, (built, built_treaty) = stream[config]
        seamed, seam_treaty = assemble_tables(MINI, kernel_io.read_transitions(path))
        for label, left, right in (
            ("settlement", seamed, built),
            ("treaties", seam_treaty, built_treaty),
        ):
            left_path, right_path = tmp_path / f"{label}-a.tsv", tmp_path / f"{label}-b.tsv"
            left.write_tsv(left_path)
            right.write_tsv(right_path)
            assert left_path.read_bytes() == right_path.read_bytes(), label
        windows_a, windows_b = tmp_path / "windows-a.tsv.gz", tmp_path / "windows-b.tsv.gz"
        table_module.write_windows(seamed, windows_a, PINNED_STAMP)
        table_module.write_windows(built, windows_b, PINNED_STAMP)
        assert windows_a.read_bytes() == windows_b.read_bytes()


class TestTheWireLayoutIsTheDocumentedOne:
    """A symmetric round trip cannot pin a wire format — a writer and a reader that drifted together would keep agreeing with each other while a Rust reader built to `ams-m1-transitions/1` silently mis-parsed — so the layout the module docstring promises is asserted against the raw bytes: the marker line, the head's keys in their order, the head's cell spelling, and all twelve row positions."""

    def test_the_stream_spells_the_layout_the_contract_names(self, stream):
        product, path, _tables = stream["default"]
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
        product, _path, _tables = stream["default"]
        starved = dataclasses.replace(
            product, cells=frozenset(product.cells - {product.transitions[0].settled.cell})
        )
        with pytest.raises(table_module.PartitionError):
            kernel_io.write_transitions(starved, tmp_path / "starved.gz")
