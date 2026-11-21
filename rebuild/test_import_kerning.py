"""Tests for the importer that carries the old font's Senior kerning over, `rebuild/tools/import_kerning.py`, over rebuild/test_kern.py's small alphabet with a sidecar and an alias map of the test's own: the evidence it reads through the oracle's evaluator, the records it plans by scope, what it leaves out and reports, the validation's refusals, the second `--apply` that writes nothing, and the refusals of `--apply`."""

from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path

import pytest

from rebuild.review import kern_writer, store_lock
from rebuild.test_kern import NO_FLIPPED, NO_LOOP, TEA_FULL, TEA_HALF, cell
from rebuild.test_review_kern_routes import ROWS, Tree, block, minted, no_kernel  # noqa: F401
from rebuild.tools import import_kerning

NO_EXITING = cell("qsNo", "loop", None, "baseline")
TEA_ENTERED = cell("qsTea", "full", "baseline", None)
JOINED_ROWS = {"default": [*ROWS["default"], (NO_EXITING, TEA_ENTERED, "baseline")]}
ALIASES = """\
space: boundary
qsNo: {rune: qsNo, stance: loop}
qsNo.ss10: {rune: qsNo, stance: loop}
qsNo.ex-y0: {rune: qsNo, stance: loop, exit: baseline}
qsNo.alt: {rune: qsNo, stance: flipped}
qsTea: {rune: qsTea, stance: full}
qsTea.en-y0: {rune: qsTea, stance: full, entry: baseline}
qsTea.half: {rune: qsTea, stance: half}
qsIt: {rune: qsIt, stance: sole}
qsUtter: {rune: qsUtter, stance: sole}
"""


def sidecar(*rules: str) -> str:
    return "# a test sidecar\n" + "".join(f"---\n{rule}\n" for rule in rules)


NO_TEA = "left_family: [qsNo]\nright_family: [qsTea]\nvalue: -1"


@pytest.fixture
def world(tmp_path):
    """A tree whose routes the importer reads, with the test's alias map, and a function that writes a sidecar and runs the import."""
    tree = Tree(tmp_path, rows=JOINED_ROWS)
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text(ALIASES)
    out = tmp_path / "var" / "kern"
    lock = tmp_path / "var" / "cycle" / "pass.lock"

    def run(*rules: str, apply: bool = False) -> tuple[int, str, dict]:
        path = tmp_path / "sidecar.yaml"
        path.write_text(sidecar(*rules))
        stream = io.StringIO()
        status = import_kerning.run(
            routes=tree.routes,
            out_dir=out,
            apply=apply,
            pass_lock=lock,
            sidecar=path,
            aliases=aliases,
            stream=stream,
        )
        report_path = out / import_kerning.REPORT_NAME
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        return status, stream.getvalue(), report

    tree.run = run  # pyright: ignore[reportAttributeAccessIssue]
    tree.out = out  # pyright: ignore[reportAttributeAccessIssue]
    tree.lock = lock  # pyright: ignore[reportAttributeAccessIssue]
    return tree


pytestmark = pytest.mark.usefixtures("no_kernel", "minted")


def imported(tree) -> list[dict]:
    return json.loads((tree.out / import_kerning.IMPORTED_NAME).read_text())["pairs"]


class TestTheRecords:
    def test_a_rune_pair_whose_evidence_agrees_takes_one_letters_record(self, world):
        status, printed, report = world.run(NO_TEA, apply=True)
        assert status == 0 and "applied to" in printed
        assert block(world) == "kern:\n- {right: {family: qsTea}, by: -1}\n"
        assert report["counts"]["letters_records"] == 1 and report["counts"]["facing_records"] == 0
        pairs = {(entry["lead"], entry["follow"]): entry for entry in imported(world)}
        assert set(pairs) == {("qsNo[loop]", "qsTea[full]"), ("qsNo[flipped]", "qsTea[half]")}
        assert {entry["by"] for entry in pairs.values()} == {-1}
        assert {entry["line"] for entry in pairs.values()} == {"- {right: {family: qsTea}, by: -1}"}
        assert {entry["scope"] for entry in pairs.values()} == {"letters"}

    def test_facing_pairs_with_different_values_take_one_record_each_and_a_zero_takes_none(self, world):
        undo_flipped = "left_stance: [qsNo.alt]\nright_family: [qsTea]\nvalue: 1"
        status, _printed, report = world.run(NO_TEA, undo_flipped, apply=True)
        assert status == 0
        assert block(world) == "kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: -1}\n"
        assert report["counts"]["facing_records"] == 1 and report["counts"]["zero"] >= 1

    def test_a_facing_pair_whose_members_disagree_takes_nothing_and_is_reported(self, world, tmp_path):
        aliases = tmp_path / "aliases.yaml"
        aliases.write_text(ALIASES + "qsTea.marked: {rune: qsTea, stance: full}\n")
        status, _printed, report = world.run(
            NO_TEA, "left_family: [qsNo]\nright_stance: [qsTea.marked]\nvalue: -1", apply=True
        )
        assert status == 0
        (found,) = report["disagreements"]
        assert (found["lead"], found["follow"], found["values"]) == ("qsNo[loop]", "qsTea[full]", [-2, -1])
        assert {value for member in found["members"] for value in member["values"].values()} == {-2, -1}
        assert block(world) == "kern:\n- {stance: flipped, right: {family: qsTea, stance: half}, by: -1}\n"
        inert = {entry["index"]: entry["reason"] for entry in report["inert_rules"]}
        assert "different values" in inert[1]

    def test_a_carve_out_keeps_the_letters_record_from_a_shape_with_no_evidence(self, world, tmp_path):
        """The old rule excludes flipped ·No, which the alias map here does not name, so flipped ·No before ·Tea has no evidence: a record for the two letters would carry the value to the shape the rule excluded, so the importer writes a record for loop ·No alone and reports the narrowing."""
        (tmp_path / "aliases.yaml").write_text(
            ALIASES.replace("qsNo.alt: {rune: qsNo, stance: flipped}\n", "")
        )
        carved = "left_family: [qsNo]\nexcept_left: [qsNo.alt]\nright_family: [qsTea]\nvalue: -1"
        status, printed, report = world.run(carved, apply=True)
        assert status == 0 and "1 pairs of letters narrowed" in printed
        assert block(world) == "kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: -1}\n"
        assert report["covered"] == []
        (narrowed,) = report["narrowed"]
        assert (narrowed["lead"], narrowed["follow"], narrowed["pairs"]) == (
            "qsNo",
            "qsTea",
            ["qsNo[loop] | qsTea[full]"],
        )
        assert narrowed["rules"][0]["rule"]["except_left"] == ["qsNo.alt"]

    def test_a_plain_family_rule_still_covers_a_facing_pair_with_no_evidence(self, world, tmp_path):
        (tmp_path / "aliases.yaml").write_text(
            ALIASES.replace("qsNo.alt: {rune: qsNo, stance: flipped}\n", "")
        )
        status, _printed, report = world.run(NO_TEA, apply=True)
        assert status == 0 and report["narrowed"] == []
        assert block(world) == "kern:\n- {right: {family: qsTea}, by: -1}\n"
        assert [(entry["lead"], entry["follow"]) for entry in report["covered"]] == [
            ("qsNo[flipped]", "qsTea[half]")
        ]

    def test_a_record_already_in_a_file_is_never_touched_and_its_facing_pair_is_skipped(self, world):
        rune = world.routes.runes_dir / "qsNo.yaml"
        hand = "kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: 2, why: 'by hand'}\n"
        rune.write_text(rune.read_text() + hand)
        status, _printed, report = world.run(NO_TEA, apply=True)
        assert status == 0
        assert block(world) == (
            "kern:\n- {stance: flipped, right: {family: qsTea, stance: half}, by: -1}\n"
            + hand.removeprefix("kern:\n")
        )
        (skipped,) = report["skipped"]
        assert (skipped["lead"], skipped["follow"], skipped["old"]) == ("qsNo[loop]", "qsTea[full]", -1)

    def test_a_value_the_contact_check_refuses_is_left_out_with_the_build_s_message(self, world):
        status, printed, report = world.run(
            NO_TEA, "left_family: [qsNo]\nright_family: [qsUtter]\nvalue: -4", apply=True
        )
        assert status == 0 and "refused: 1 records" in printed
        (refused,) = report["refused"]
        assert refused["line"] == "- {right: {family: qsUtter}, by: -4}"
        assert refused["issues"][0][0] == "E-CONTACT" and "kern-4" in refused["issues"][0][1]
        assert block(world) == "kern:\n- {right: {family: qsTea}, by: -1}\n"

    def test_a_refused_letters_record_gives_way_to_the_facing_pairs_that_pass(self, world):
        status, _printed, report = world.run(
            "left_family: [qsNo]\nright_family: [qsTea]\nvalue: -2", apply=True
        )
        assert status == 0
        refused_pairs = {pair for entry in report["refused"] for pair in entry["pairs"]}
        assert "qsNo[loop] | qsTea[full]" in refused_pairs
        assert "qsNo | qsTea" not in block(world)
        assert "{right: {family: qsTea}" not in block(world)


class TestTheReport:
    def test_a_join_the_old_rules_move_is_listed_and_dropped(self, world):
        _status, _printed, report = world.run(NO_TEA)
        (joined,) = report["joined"]
        assert (joined["lead"], joined["lead_stance"], joined["follow"], joined["height"]) == (
            "qsNo",
            "loop",
            "qsTea",
            "baseline",
        )
        assert joined["old_values"] == {"-1": 1} and joined["old_pairs"] == ["qsNo.ex-y0 qsTea.en-y0"]

    def test_each_old_rule_that_sets_nothing_says_why_and_the_zwnj_rule_is_left_out(self, world):
        rules = [
            NO_TEA,
            "left_family: [qsOwe]\nright_family: [qsTea]\nvalue: -1",
            "left_family: [qsDay]\nright_family: [qsTea]\nvalue: -1",
            "left_family: [qsIt]\nright_family: [qsTea]\nvalue: 0",
            "left_family: [qsNo]\nright_group: noentry\nvalue: -3",
            "left_stance: [qsNo.ex-y0]\nright_family: [qsTea]\nvalue: -1",
        ]
        _status, _printed, report = world.run(*rules)
        reasons = {entry["index"]: entry["reason"] for entry in report["inert_rules"]}
        assert reasons[1] == "its letters have no rune file: qsOwe"
        assert "has an alias" in reasons[2]
        assert reasons[3].startswith("its value is 0")
        assert reasons[5] == "its glyph pairs only join in the rebuild"
        assert 0 not in reasons
        assert [(entry["index"], entry["reason"]) for entry in report["left_out"]] == [
            (4, "a ZWNJ rule, which the rebuild does not carry over")
        ]
        assert "opentype-features.yaml" in report["not_read"]

    def test_the_dry_run_writes_only_the_report(self, world):
        before = (world.routes.runes_dir / "qsNo.yaml").read_bytes()
        status, printed, report = world.run(NO_TEA)
        assert status == 0 and "dry run over" in printed and report["counts"]["letters_records"] == 1
        assert (world.routes.runes_dir / "qsNo.yaml").read_bytes() == before
        assert sorted(path.name for path in world.out.iterdir()) == [import_kerning.REPORT_NAME]


class TestApplying:
    def test_a_second_apply_over_the_same_inputs_changes_nothing(self, world):
        assert world.run(NO_TEA, apply=True)[0] == 0
        files = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in [*world.routes.runes_dir.iterdir(), *world.out.iterdir()]
        }
        status, printed, _report = world.run(NO_TEA, apply=True)
        assert status == 0 and "nothing to change" in printed
        assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files} == files

    def test_the_records_load_and_resolve_as_the_build_reads_them(self, world):
        world.run(NO_TEA, "left_stance: [qsNo.alt]\nright_family: [qsTea]\nvalue: 1", apply=True)
        world.routes.spec()
        payload = world.routes.values_payload()
        assert payload["live"]["error"] is None
        values = {(value["lead"], value["follow"]): value["by"] for value in payload["live"]["values"]}
        assert values[("qsNo[loop]", "qsTea[full]")] == -1
        assert kern_writer.parse_block(block(world), "f")

    def test_apply_refuses_while_a_pass_holds_the_lock(self, world):
        before = (world.routes.runes_dir / "qsNo.yaml").read_bytes()
        with store_lock.hold_flock(world.lock) as handle:
            os.pwrite(handle, b"4242\n", 0)
            status, printed, _report = world.run(NO_TEA, apply=True)
        assert status == 1 and "4242" in printed
        assert (world.routes.runes_dir / "qsNo.yaml").read_bytes() == before

    def test_apply_refuses_over_tables_the_sources_no_longer_give(self, world):
        world.routes._tables_inputs = lambda: "another-stamp"
        status, printed, _report = world.run(NO_TEA, apply=True)
        assert status == 1 and "run the M1 build first" in printed
        assert block(world) == ""


def test_the_evaluator_reads_only_the_pair_rules(tmp_path):
    path = tmp_path / "sidecar.yaml"
    path.write_text(
        sidecar("global: {value: -5}", NO_TEA, "left_family: [qsNo]\nright_group: noentry\nvalue: -3")
    )
    rules = import_kerning.load_old_rules(path)
    assert [index for index, _rule in rules.rules] == [1]
    assert rules.evaluator.value_for("qsNo", "qsTea") == -1
    assert rules.evaluator.value_for("qsNo", "qsTea.noentry") == -1
    assert rules.evaluator.value_for("qsNo", "qsIt") == 0
    assert [entry["index"] for entry in rules.left_out] == [0, 2]


def test_the_importer_is_no_build_s_code():
    """No module of the build, the corpus or the server imports the importer, so an edit to it re-runs no stage."""
    root = Path(import_kerning.__file__).resolve().parents[2]
    for tree in ("pipeline", "review", "validation"):
        for module in (root / "rebuild" / tree).glob("*.py"):
            for node in ast.walk(ast.parse(module.read_bytes())):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or "", *(alias.name for alias in node.names)]
                assert not any("import_kerning" in name for name in names), module
