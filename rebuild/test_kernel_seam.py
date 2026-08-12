"""The kernel boundary inside the table build: `enumerate_transitions` runs the fixpoint and returns its enriched transition stream, `assemble_tables` folds that stream and nothing else the engine saw, and the composition must be `build_tables` down to the serialized bytes. That equality is what licenses a port of the front half — a kernel that reproduces the product reproduces the artifacts — so these tests compare the two halves at every grain the artifacts are cut from: the rows with their settled fields, the rules, the treaty, the reachable cells, the cited provenance, and the TSV and windows bytes under a pinned stamp (the real stamp hashes the pipeline sources, so it moves on any edit here)."""

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline import table as table_module
from rebuild.pipeline.table import assemble_tables, build_tables, enumerate_transitions

SPEC = fixtures.mini_spec()
CONFIGS = {"default": frozenset(), "ss03": frozenset({"ss03"}), "ss04": frozenset({"ss04"})}
PINNED_STAMP = "seam-pinned-stamp"


@pytest.fixture(scope="module")
def seam():
    built = {}
    for name, features in CONFIGS.items():
        product = enumerate_transitions(SPEC, features)
        built[name] = (product, assemble_tables(SPEC, product), build_tables(SPEC, features))
    return built


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheHalvesRebuildTheWhole:
    def test_the_assembled_tables_are_the_composed_ones(self, seam, config):
        product, (seamed, seam_treaty), (composed, composed_treaty) = seam[config]
        assert seamed.config == composed.config == product.config
        assert seamed.transitions == composed.transitions
        assert seamed.rules == composed.rules
        assert seamed.identity_guard_rules == composed.identity_guard_rules
        assert seamed.cited_provenance == composed.cited_provenance
        assert seamed.deep_classes == composed.deep_classes
        assert seamed.reachable_cells() == composed.reachable_cells()
        assert seam_treaty.rows == composed_treaty.rows

    def test_both_tables_serialize_to_the_same_bytes(self, seam, config, tmp_path):
        _product, (seamed, seam_treaty), (composed, composed_treaty) = seam[config]
        pairs = (
            ("settlement", seamed, composed),
            ("treaties", seam_treaty, composed_treaty),
        )
        for label, left, right in pairs:
            left_path, right_path = tmp_path / f"{label}-a.tsv", tmp_path / f"{label}-b.tsv"
            left.write_tsv(left_path)
            right.write_tsv(right_path)
            assert left_path.read_bytes() == right_path.read_bytes(), label
        windows_a, windows_b = tmp_path / "windows-a.tsv.gz", tmp_path / "windows-b.tsv.gz"
        table_module.write_windows(seamed, windows_a, PINNED_STAMP)
        table_module.write_windows(composed, windows_b, PINNED_STAMP)
        assert windows_a.read_bytes() == windows_b.read_bytes()


@pytest.mark.parametrize("config", sorted(CONFIGS))
class TestTheProductStandsAlone:
    def test_the_stream_is_key_sorted_without_duplicates(self, seam, config):
        product, _seamed, _composed = seam[config]
        keys = [row.key for row in product.transitions]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)

    def test_every_settled_cell_the_rows_name_is_in_the_product(self, seam, config):
        product, _seamed, _composed = seam[config]
        for row in product.transitions:
            assert row.settled.cell in product.cells
            if row.left_settled is not None:
                assert row.left_settled.cell in product.cells

    def test_the_prospect_divergence_pass_runs_in_the_back_half(self, seam, config):
        """The fold genuinely raises joint flags the fixpoint alone left unflagged — the prospect-divergence pass runs in the back half rather than vacuously — and it is monotone, never clearing a joint the trace floor set. That the product's own `joint` is the trace floor holds by construction in the front half rather than by an assertion here. The mini spec's one deep class never covers a divergent row, so the claim is stated over the class-grain stream as a whole instead of over a class row in particular."""
        product, (seamed, _treaty), _composed = seam[config]
        assert [row.key for row in product.transitions] == [row.key for row in seamed.transitions]
        flipped = [
            before.key
            for before, after in zip(product.transitions, seamed.transitions)
            if after.joint and not before.joint
        ]
        assert flipped
        assert not any(
            before.joint and not after.joint for before, after in zip(product.transitions, seamed.transitions)
        )


def test_the_default_configuration_enumerates_at_class_grain(seam):
    product, _seamed, _composed = seam["default"]
    assert product.deep_classes
    for token, members in product.deep_classes.items():
        assert token.startswith(table_module.DEEP_CLASS_PREFIX)
        assert len(members) > 1
