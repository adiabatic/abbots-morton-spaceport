"""Tests for the review server's kerning routes, `rebuild/review/kern_routes.py`: `GET /kern`'s answer over rebuild/test_kern.py's small alphabet with tables the test hands it, the `kern:` block each rune file reports, the shape `check_values_payload` holds the answer and the app's fixture to, that answering never runs the kernel or cargo, and both routes on a real server (`rebuild/test_review_autosave.py`'s harness) on a port of its own, over a tree under `tmp_path`."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rebuild.pipeline import kern, kern_inventory, kernel_exec, spec_load
from rebuild.review import kern_routes, store_lock
from rebuild.test_kern import (
    LIGATURE_FULL,
    NO_FLIPPED,
    NO_LOOP,
    TEA_FULL,
    TEA_HALF,
    UTTER,
    glyphs,
    load,
    tables,
)
from rebuild.test_review_autosave import REPO, free_port, start_server

FIXTURES = Path(__file__).resolve().parent / "review" / "fixtures"
FLIPPED_HALF = [{"stance": "flipped", "right": {"family": "qsTea", "stance": "half"}, "by": -1}]
ROWS = {"default": [(NO_LOOP, TEA_FULL, "break"), (NO_FLIPPED, TEA_HALF, "break")]}


class RowTables(dict):
    """The tables `tables` builds for some rows, which carry the rows, so the minting fixture mints their cells."""

    rows: dict = ROWS


class Tree:
    """A repository tree under `tmp_path` holding the test alphabet's rune files with `kerns`, and the routes over it, handed the tables `rows` gives (`ROWS` by default) in place of reading them from disk. The routes' journal and pass lock lie under the tree's `var/`."""

    def __init__(self, tmp_path: Path, kerns: dict | None = None, rows: dict | None = None) -> None:
        self.root = tmp_path
        self.spec = load(tmp_path, kerns)
        self.m1_out = tmp_path / "rebuild" / "out" / "m1"
        self.m1_out.mkdir(parents=True)
        self.routes = kern_routes.KernRoutes(
            tmp_path, self.m1_out, tables_inputs=lambda: "the-stamp", runes_dir=tmp_path / "runes"
        )
        self.routes.registry = tmp_path / "script.yaml"
        self.routes.schema_dir = spec_load.DEFAULT_SCHEMA_DIR
        built = RowTables(tables(self.spec, rows or ROWS))
        built.rows = rows or ROWS
        self.routes.tables = lambda: (built, "the-stamp", None)  # pyright: ignore[reportAttributeAccessIssue]
        self.glyphs = glyphs(self.spec, rows or ROWS)

    def classes(self, values: list) -> None:
        (self.m1_out / kern.KERN_CLASSES_JSON).write_text(
            json.dumps({"format": kern.KERN_CLASSES_FORMAT, "leading": {}, "following": {}, "values": values})
        )


def mint(spec, built):
    return glyphs(spec, getattr(built, "rows", ROWS))


@pytest.fixture
def no_kernel(monkeypatch):
    """Fail the test if anything builds or runs the kernel while the routes answer."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("the kern routes ran the kernel or cargo")

    monkeypatch.setattr(kernel_exec, "cargo_build", refuse)
    monkeypatch.setattr(kernel_exec, "ensure_built", refuse)
    monkeypatch.setattr(kernel_exec, "_settle_cases", refuse)
    monkeypatch.setattr(kernel_exec, "guard_sweep", refuse)


@pytest.fixture
def minted(monkeypatch):
    """Mint the test's cells the way rebuild/test_kern.py realizes them, since the handed tables have no decision table to read reachable cells from."""
    from rebuild.pipeline import run_m1

    monkeypatch.setattr(run_m1, "mint_cell_glyphs", mint)


class TestTheBlocks:
    def test_a_block_runs_from_its_kern_line_to_the_end_of_the_file(self):
        assert kern_routes.kern_block_text(
            "rune: qsNo\npolicy: {}\nkern:\n- {right: {family: qsTea}, by: -1}\n"
        ) == ("kern:\n- {right: {family: qsTea}, by: -1}\n")
        assert kern_routes.kern_block_text("rune: qsNo\n  kern: nested\n") == ""

    def test_each_rune_reports_its_block_its_digest_and_its_records(self, tmp_path):
        tree = Tree(tmp_path, {"qsNo": FLIPPED_HALF})
        blocks = kern_routes.rune_blocks(tree.spec, tree.routes.runes_dir, tmp_path)
        assert set(blocks) == set(tree.spec.runes)
        no = blocks["qsNo"]
        assert (
            no["text"].startswith("kern:\n")
            and no["sha256"] == hashlib.sha256(no["text"].encode()).hexdigest()
        )
        assert no["records"] == [
            {
                "path": "kern[0]",
                "by": -1,
                "text": "{stance: flipped, right: {family: qsTea, stance: half}, by: -1}",
                "why": None,
                "line": None,
                "sides": ["qsNo[flipped]", "qsTea[half]"],
                "narrow": True,
            }
        ]
        assert "one flow-style record per line" in no["refusal"]
        assert blocks["qsTea"] == {
            "file": "runes/qsTea.yaml",
            "sha256": hashlib.sha256(b"").hexdigest(),
            "text": "",
            "refusal": None,
            "records": [],
        }


class TestTheValues:
    def test_the_answer_holds_the_live_values_with_their_deciders_the_built_ones_and_the_staleness(
        self, tmp_path, no_kernel, minted
    ):
        tree = Tree(tmp_path, {"qsNo": FLIPPED_HALF})
        tree.classes([["qsNo[loop]", "qsTea[full]", -1], ["qsNo[flipped]", "qsTea[half]", 0]])
        payload = tree.routes.values_payload()
        assert kern_routes.check_values_payload(payload) == []
        assert payload["live"]["error"] is None
        (value,) = payload["live"]["values"]
        assert (value["lead"], value["follow"], value["by"]) == ("qsNo[flipped]", "qsTea[half]", -1)
        assert value["deciders"] == [
            {"file": str(tmp_path / "runes" / "qsNo.yaml"), "path": "kern[0]", "inherited": False}
        ]
        assert payload["built"] == {"present": True, "values": [["qsNo[loop]", "qsTea[full]", -1]]}
        assert payload["tables"] == {"inputs": "the-stamp", "current": True}
        assert payload["inventory"] == {
            "present": False,
            "stamp": None,
            "tables_current": None,
            "manual_current": None,
        }
        assert payload["pass"] is None
        assert payload["runes"]["qsNo"]["records"][0]["path"] == "kern[0]"

    def test_a_kern_error_is_reported_with_its_issues(self, tmp_path, no_kernel, minted):
        tree = Tree(tmp_path, {"qsIt": [{"right": {"family": "qsDay_qsUtter"}, "by": -1}]})
        payload = tree.routes.values_payload()
        assert payload["live"]["values"] == []
        assert [code for code, _message in payload["live"]["error"]["issues"]] == ["E-KERN-UNMATCHED"]
        assert kern_routes.check_values_payload(payload) == []

    def test_the_inventory_s_stamp_is_checked_against_the_tables_and_the_manual_pages(
        self, tmp_path, no_kernel, minted
    ):
        tree = Tree(tmp_path)
        pages = kern_inventory.read_manual_pages(tmp_path)
        stamp = kern_inventory.inventory_stamp("the-stamp", pages)
        path = tree.m1_out / kern_inventory.KERN_INVENTORY_JSON
        path.write_text(json.dumps({"format": kern_inventory.INVENTORY_FORMAT, "stamp": stamp}))
        assert tree.routes.values_payload()["inventory"] == {
            "present": True,
            "stamp": stamp,
            "tables_current": True,
            "manual_current": True,
        }
        (tmp_path / "site").mkdir()
        (tmp_path / "site" / "the-manual.html").write_text("")
        path.write_text(
            json.dumps({"format": kern_inventory.INVENTORY_FORMAT, "stamp": {**stamp, "tables": "older"}})
        )
        inventory = tree.routes.values_payload()["inventory"]
        assert (inventory["tables_current"], inventory["manual_current"]) == (False, False)
        assert tree.routes.inventory_bytes() == path.read_bytes()

    def test_a_running_pass_is_named_by_its_pid(self, tmp_path, no_kernel, minted):
        tree = Tree(tmp_path)
        with store_lock.hold_flock(tree.routes.pass_lock) as handle:
            os.pwrite(handle, b"4242\n", 0)
            assert tree.routes.values_payload()["pass"] == {"pid": "4242"}
        assert tree.routes.values_payload()["pass"] is None

    def test_the_answer_is_kept_until_a_rune_file_moves(self, tmp_path, no_kernel, minted, monkeypatch):
        tree = Tree(tmp_path, {"qsNo": FLIPPED_HALF})
        resolved: list = []
        real = kern.resolve
        monkeypatch.setattr(kern, "resolve", lambda *args: resolved.append(1) or real(*args))
        tree.routes.values_payload()
        tree.routes.values_payload()
        assert resolved == [1]
        rune = tree.routes.runes_dir / "qsNo.yaml"
        rune.write_text(rune.read_text().replace("by: -1", "by: -2"))
        os.utime(rune, ns=(rune.stat().st_atime_ns, rune.stat().st_mtime_ns + 1_000_000))
        assert tree.routes.values_payload()["live"]["values"][0]["by"] == -2
        assert resolved == [1, 1]


class TestTheShape:
    def test_the_app_fixture_is_an_answer(self):
        assert (
            kern_routes.check_values_payload(
                json.loads((FIXTURES / "kern-values.json").read_text(encoding="utf-8"))
            )
            == []
        )

    @pytest.mark.parametrize(
        "broken",
        [
            {"format": "other"},
            {"live": {"values": [{"lead": "a"}], "error": None}},
            {"runes": {"qsNo": {"file": "f", "sha256": "0", "text": "kern:\n", "records": []}}},
            {"inventory": {"present": True, "stamp": None}},
        ],
    )
    def test_a_broken_answer_is_named(self, broken):
        payload = {**json.loads((FIXTURES / "kern-values.json").read_text(encoding="utf-8")), **broken}
        assert kern_routes.check_values_payload(payload)


def get(port: int, path: str) -> tuple[int, dict, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=60) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_both_routes_answer_on_a_real_server_with_no_store(tmp_path):
    """A server over a tree with a corpus but no tables answers `GET /kern/inventory` with the inventory's bytes, a 404 once it is gone, and `GET /kern` with an answer whose live part says the tables cannot be read, each with `Cache-Control: no-store`."""
    corpus = tmp_path / "rebuild" / "out" / "review"
    corpus.mkdir(parents=True)
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-11-20T00:00:00Z", "human_unit_ids": []})
    )
    m1_out = tmp_path / "rebuild" / "out" / "m1"
    m1_out.mkdir(parents=True)
    inventory = (FIXTURES / "kern-inventory.json").read_bytes()
    (m1_out / kern_inventory.KERN_INVENTORY_JSON).write_bytes(inventory)
    server, port = start_server(tmp_path)
    try:
        status, headers, body = get(port, "/kern/inventory")
        assert (status, headers.get("Cache-Control"), body) == (200, "no-store", inventory)
        status, headers, body = get(port, "/kern")
        payload = json.loads(body)
        assert (status, headers.get("Cache-Control")) == (200, "no-store")
        assert kern_routes.check_values_payload(payload) == []
        assert (
            "cannot be read" in payload["live"]["error"]["message"]
            or "do not load" in payload["live"]["error"]["message"]
        )
        assert payload["inventory"]["present"] is True
        (m1_out / kern_inventory.KERN_INVENTORY_JSON).unlink()
        status, headers, body = get(port, "/kern/inventory")
        assert (status, headers.get("Cache-Control")) == (404, "no-store")
        assert "M1 build" in json.loads(body)["error"]
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=30)


class TestFailures:
    def test_tables_of_more_than_one_build_are_refused_as_an_error_answer(
        self, tmp_path, monkeypatch, no_kernel
    ):
        """Every configuration's tables must carry one stamp, as the inventory's command line demands; tables that mix two builds' are no build's, and the answer says so instead of resolving over them."""
        from rebuild.pipeline import conform
        from rebuild.pipeline.table import DecisionTable, JoinTable

        stamps = {
            config: "stamp-a" if index else "stamp-b"
            for index, config in enumerate(conform.SETTLEMENT_CONFIGS)
        }
        monkeypatch.setattr(
            kern_routes.table_module,
            "read_windows_head",
            lambda out_dir, config, cells_only=False: (stamps[config], DecisionTable(config=config)),
        )
        monkeypatch.setattr(kern_routes.table_module, "read_join_tsv", lambda path: JoinTable(config="x"))
        routes = kern_routes.KernRoutes(tmp_path, tmp_path, tables_inputs=lambda: "stamp-a")
        tables, stamp, error = routes.tables()
        assert tables is None and stamp is None and error is not None and "more than one stamp" in error
        payload = routes.values_payload()
        assert kern_routes.check_values_payload(payload) == []
        assert "more than one stamp" in payload["live"]["error"]["message"]

    def test_an_exception_while_answering_comes_back_as_the_error_answer(self, tmp_path, no_kernel, minted):
        tree = Tree(tmp_path)

        def broken():
            raise RuntimeError("the disk went away")

        tree.routes.live = broken  # pyright: ignore[reportAttributeAccessIssue]
        payload = tree.routes.values_payload()
        assert kern_routes.check_values_payload(payload) == []
        assert "RuntimeError: the disk went away" in payload["live"]["error"]["message"]

    def test_a_spec_that_does_not_load_is_not_loaded_again_until_a_file_moves(
        self, tmp_path, no_kernel, minted, monkeypatch
    ):
        """A rune file the loader trips over, whatever the exception, gives an error answer, and the next polls answer from that, loading nothing until a file moves."""
        tree = Tree(tmp_path)
        calls: list = []

        def trips(*_args):
            calls.append(1)
            raise TypeError("a rune the loader cannot read")

        monkeypatch.setattr(kern_routes.spec_load, "load_spec", trips)
        first = tree.routes.values_payload()
        second = tree.routes.values_payload()
        assert calls == [1]
        assert "TypeError" in first["live"]["error"]["message"] and second == first
        assert kern_routes.check_values_payload(first) == []


LOOP_FULL = ("qsNo[loop]", "qsTea[full]")
FLIPPED_HALF_PAIR = ("qsNo[flipped]", "qsTea[half]")


def base_of(tree: Tree, rune: str = "qsNo") -> str:
    return kern_routes.kern_writer.block_sha(
        kern_routes.kern_block_text(kern_routes.kern_writer.read_text(tree.routes.runes_dir / f"{rune}.yaml"))
    )


def facing(tree: Tree, pair: tuple[str, str], by: int | None, **extra) -> tuple[int, dict]:
    return tree.routes.post_delta(
        {
            "sets": [
                {
                    "scope": "facing",
                    "lead": pair[0],
                    "follow": pair[1],
                    "by": by,
                    "base": base_of(tree, pair[0].split("[")[0]),
                    **extra,
                }
            ]
        }
    )


def letters(tree: Tree, lead: str, follow: str, by: int | None, **extra) -> tuple[int, dict]:
    return tree.routes.post_delta(
        {
            "sets": [
                {
                    "scope": "letters",
                    "lead": lead,
                    "follow": follow,
                    "by": by,
                    "base": base_of(tree, lead),
                    **extra,
                }
            ]
        }
    )


def block(tree: Tree, rune: str = "qsNo") -> str:
    return kern_routes.kern_block_text(
        kern_routes.kern_writer.read_text(tree.routes.runes_dir / f"{rune}.yaml")
    )


def live(reply: dict) -> dict[tuple[str, str], int]:
    return {(value["lead"], value["follow"]): value["by"] for value in reply["values"]["live"]["values"]}


@pytest.mark.usefixtures("no_kernel", "minted")
class TestTheWrites:
    def test_an_edit_writes_its_scope_s_record_journals_it_and_answers_with_the_new_values(self, tmp_path):
        tree = Tree(tmp_path)
        before = (tree.routes.runes_dir / "qsNo.yaml").read_text()
        (tree.routes.runes_dir / "qsNo.yaml").chmod(0o640)
        listing = sorted(path.name for path in tree.routes.runes_dir.iterdir())
        status, reply = facing(tree, LOOP_FULL, -1)
        assert (status, reply["status"]) == (200, "applied")
        assert sorted(path.name for path in tree.routes.runes_dir.iterdir()) == listing
        assert (tree.routes.runes_dir / "qsNo.yaml").stat().st_mode & 0o777 == 0o640
        assert sorted(path.name for path in tree.routes.journal.path.parent.iterdir()) == [
            "journal.lock",
            "journal.ndjson",
        ]
        assert block(tree) == "kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: -1}\n"
        assert (tree.routes.runes_dir / "qsNo.yaml").read_text() == before + block(tree)
        assert live(reply)[LOOP_FULL] == -1
        assert reply["changes"] == [
            {
                "scope": "qsNo[loop] | qsTea[full]",
                "before": None,
                "after": "- {stance: loop, right: {family: qsTea, stance: full}, by: -1}",
            }
        ]
        assert kern_routes.check_values_payload(reply["values"]) == []
        (event,) = [json.loads(line) for line in tree.routes.journal.path.read_text().splitlines()]
        assert event["event"] == "applied" and event["seq"] == reply["seq"]
        assert (event["before"], event["after"]) == ("", block(tree))
        assert event["after_sha256"] == hashlib.sha256(block(tree).encode()).hexdigest()
        assert event["delta"]["sets"][0]["by"] == -1
        assert tree.routes.values_payload()["live"] == reply["values"]["live"]

    def test_a_value_set_on_the_letters_applies_to_every_facing_pair_without_a_record_of_its_own(
        self, tmp_path
    ):
        tree = Tree(tmp_path)
        assert facing(tree, FLIPPED_HALF_PAIR, 2)[0] == 200
        status, reply = letters(tree, "qsNo", "qsTea", -1)
        assert status == 200
        assert block(tree) == (
            "kern:\n- {right: {family: qsTea}, by: -1}\n- {stance: flipped, right: {family: qsTea, stance: half}, by: 2}\n"
        )
        assert live(reply)[LOOP_FULL] == -1 and live(reply)[FLIPPED_HALF_PAIR] == 2

    def test_a_record_equal_to_what_it_would_inherit_goes_and_the_last_takes_the_key(self, tmp_path):
        tree = Tree(tmp_path)
        original = (tree.routes.runes_dir / "qsNo.yaml").read_bytes()
        assert letters(tree, "qsNo", "qsTea", -1)[0] == 200
        assert facing(tree, FLIPPED_HALF_PAIR, 2)[0] == 200
        status, reply = facing(tree, FLIPPED_HALF_PAIR, -1)
        assert (status, reply["changes"][0]["after"]) == (200, None)
        assert block(tree) == "kern:\n- {right: {family: qsTea}, by: -1}\n"
        status, reply = letters(tree, "qsNo", "qsTea", 0)
        assert (status, reply["status"]) == (200, "applied")
        assert (tree.routes.runes_dir / "qsNo.yaml").read_bytes() == original
        status, reply = facing(tree, LOOP_FULL, 0)
        assert (status, reply["status"]) == (200, "unchanged")

    def test_setting_a_record_to_the_value_it_has_drops_it_once_it_is_what_it_would_inherit(self, tmp_path):
        tree = Tree(tmp_path)
        assert facing(tree, LOOP_FULL, -1)[0] == 200
        assert letters(tree, "qsNo", "qsTea", -1)[0] == 200
        assert block(tree).count("\n- ") == 2
        status, reply = facing(tree, LOOP_FULL, -1)
        assert (status, reply["status"]) == (200, "applied")
        assert block(tree) == "kern:\n- {right: {family: qsTea}, by: -1}\n"
        assert facing(tree, FLIPPED_HALF_PAIR, -1)[1]["status"] == "unchanged"

    def test_a_ligature_s_record_lives_in_its_own_file_and_one_it_would_inherit_goes(self, tmp_path):
        """A ligature that keeps ·Utter's outgoing stroke takes ·Utter's record for ·Tea; a value set on the ligature's letters lands in its own file, and the inherited value is no record at all."""
        rows = {"default": [(LIGATURE_FULL, TEA_FULL, "break"), (UTTER, TEA_FULL, "break")]}
        tree = Tree(tmp_path, rows=rows)
        assert letters(tree, "qsUtter", "qsTea", 1)[0] == 200
        status, reply = letters(tree, "qsDay_qsUtter", "qsTea", 2)
        assert (status, block(tree, "qsDay_qsUtter")) == (200, "kern:\n- {right: {family: qsTea}, by: 2}\n")
        status, reply = letters(tree, "qsDay_qsUtter", "qsTea", 1)
        assert (status, reply["status"], block(tree, "qsDay_qsUtter")) == (200, "applied", "")
        assert live(reply)[("qsDay_qsUtter", "qsTea")] == 1

    def test_a_note_is_stored_verbatim_kept_through_a_value_change_and_removed_when_empty(self, tmp_path):
        tree = Tree(tmp_path)
        facing(tree, LOOP_FULL, -1, why="yes: the loop sits, close")
        assert (
            block(tree)
            == 'kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: -1, why: "yes: the loop sits, close"}\n'
        )
        assert (
            tree.routes.values_payload()["runes"]["qsNo"]["records"][0]["why"] == "yes: the loop sits, close"
        )
        facing(tree, LOOP_FULL, 1)
        assert block(tree).endswith('by: 1, why: "yes: the loop sits, close"}\n')
        facing(tree, LOOP_FULL, 1, why="")
        assert block(tree) == "kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: 1}\n"

    def test_a_note_on_a_scope_with_no_record_of_its_own_is_refused(self, tmp_path):
        tree = Tree(tmp_path)
        status, reply = facing(tree, LOOP_FULL, 0, why="never collides")
        assert (status, reply["status"]) == (422, "refused") and "no record to hold the note" in reply[
            "message"
        ]
        assert block(tree) == ""

    def test_a_hand_written_broad_record_is_never_rewritten(self, tmp_path):
        tree = Tree(tmp_path)
        rune = tree.routes.runes_dir / "qsNo.yaml"
        hand = "kern:\n- {right: {class: can-enter-at-baseline}, by: 1, why: 'by hand'}\n"
        rune.write_text(rune.read_text() + hand)
        status, reply = facing(tree, LOOP_FULL, -1)
        assert status == 200
        assert block(tree) == hand + "- {stance: loop, right: {family: qsTea, stance: full}, by: -1}\n"
        assert live(reply)[LOOP_FULL] == -1 and live(reply)[("qsNo[loop]", "qsIt")] == 1

    def test_a_block_the_writer_cannot_write_back_refuses_every_edit_and_says_what_to_fix(self, tmp_path):
        tree = Tree(tmp_path)
        rune = tree.routes.runes_dir / "qsNo.yaml"
        rune.write_text(rune.read_text() + "kern:\n# kept by hand\n- {right: {family: qsTea}, by: -1}\n")
        written = rune.read_bytes()
        status, reply = facing(tree, LOOP_FULL, -1)
        assert (status, reply["status"]) == (422, "refused") and "comment" in reply["message"]
        assert rune.read_bytes() == written
        assert "comment" in reply["values"]["runes"]["qsNo"]["refusal"]

    def test_a_stale_base_is_a_conflict_that_changes_nothing(self, tmp_path):
        tree = Tree(tmp_path)
        stale = base_of(tree)
        facing(tree, LOOP_FULL, -1)
        written = block(tree)
        status, reply = tree.routes.post_delta(
            {"sets": [{"scope": "letters", "lead": "qsNo", "follow": "qsTea", "by": -1, "base": stale}]}
        )
        assert (status, reply["status"]) == (409, "conflict")
        assert reply["block"] == {
            "file": str(tree.routes.runes_dir / "qsNo.yaml"),
            "sha256": base_of(tree),
            "text": written,
        }
        assert block(tree) == written

    @pytest.mark.parametrize(
        ("pair", "by", "code", "says"),
        [
            (LOOP_FULL, -2, "E-CONTACT", "contact:qsNo.loop:qsTea.full:overlap:kern-2"),
            (("qsNo[loop]", "qsTea[half]"), -1, "E-KERN-UNMATCHED", "matches no break"),
            (("qsNo[curl]", "qsTea[full]"), -1, "spec", "unknown stance"),
        ],
    )
    def test_an_edit_the_build_would_refuse_is_refused_with_the_build_s_message(
        self, tmp_path, pair, by, code, says
    ):
        tree = Tree(tmp_path)
        status, reply = facing(tree, pair, by)
        assert (status, reply["status"]) == (422, "refused")
        assert [issue[0] for issue in reply["issues"]] == [code] and says in reply["issues"][0][1]
        assert str(tmp_path / "runes") + "/qsNo.yaml" in reply["issues"][0][1] or code == "E-CONTACT"
        assert block(tree) == "" and not tree.routes.journal.path.exists()

    def test_a_blessed_contact_is_written(self, tmp_path):
        tree = Tree(tmp_path)
        tree.routes.allow_path.parent.mkdir(parents=True, exist_ok=True)
        tree.routes.allow_path.write_text(
            "- {signature: 'contact:qsNo.loop:qsTea.full:overlap:kern-2', why: x}\n"
        )
        status, reply = facing(tree, LOOP_FULL, -2)
        assert (status, live(reply)[LOOP_FULL]) == (200, -2)

    @pytest.mark.parametrize(
        "body",
        [
            None,
            {"sets": []},
            {
                "sets": [
                    {"scope": "letters", "lead": "qsNo[loop]", "follow": "qsTea", "by": -1, "base": "0" * 64}
                ]
            },
            {
                "sets": [
                    {
                        "scope": "facing",
                        "lead": "qsNo[exit=none]",
                        "follow": "qsTea",
                        "by": -1,
                        "base": "0" * 64,
                    }
                ]
            },
            {"sets": [{"scope": "letters", "lead": "qsNo", "follow": "qsTea", "by": True, "base": "0" * 64}]},
            {"sets": [{"scope": "letters", "lead": "qsNo", "follow": "qsTea", "by": -1, "base": "short"}]},
            {
                "sets": [
                    {
                        "scope": "letters",
                        "lead": "qsNo",
                        "follow": "qsTea",
                        "by": -1,
                        "base": "0" * 64,
                        "why": "two\nlines",
                    }
                ]
            },
            {
                "sets": [
                    {
                        "scope": "letters",
                        "lead": "qsNo",
                        "follow": "qsTea",
                        "by": None,
                        "base": "0" * 64,
                        "why": "a note",
                    }
                ]
            },
            {
                "sets": [
                    {"scope": "letters", "lead": "qsNo", "follow": "qsTea", "by": -1, "base": "0" * 64},
                    {"scope": "letters", "lead": "qsIt", "follow": "qsTea", "by": -1, "base": "0" * 64},
                ]
            },
        ],
    )
    def test_a_body_that_is_not_a_delta_is_answered_400(self, tmp_path, body):
        tree = Tree(tmp_path)
        status, reply = tree.routes.post_delta(body)
        assert (status, reply["status"]) == (400, "invalid")


@pytest.mark.usefixtures("no_kernel", "minted")
class TestUndoAndPending:
    def test_undo_reverts_the_newest_change_then_the_one_before_and_is_journaled(self, tmp_path):
        tree = Tree(tmp_path)
        original = (tree.routes.runes_dir / "qsNo.yaml").read_bytes()
        older = facing(tree, LOOP_FULL, -1)[1]["seq"]
        first = block(tree)
        newer = facing(tree, FLIPPED_HALF_PAIR, -1)[1]["seq"]
        status, reply = tree.routes.post_undo({"undoes": newer})
        assert (status, reply["status"], block(tree)) == (200, "undone", first)
        assert live(reply).get(FLIPPED_HALF_PAIR, 0) == 0
        status, reply = tree.routes.post_undo({"undoes": older})
        assert (tree.routes.runes_dir / "qsNo.yaml").read_bytes() == original
        assert (status, reply["undoes"]) == (200, older)
        status, reply = tree.routes.post_undo({"undoes": older})
        assert (status, reply["status"]) == (200, "nothing") and "already undone" in reply["message"]
        events = [json.loads(line)["event"] for line in tree.routes.journal.path.read_text().splitlines()]
        assert events == ["applied", "applied", "undo", "undo"]

    def test_undo_of_a_block_changed_since_is_a_conflict(self, tmp_path):
        tree = Tree(tmp_path)
        seq = facing(tree, LOOP_FULL, -1)[1]["seq"]
        rune = tree.routes.runes_dir / "qsNo.yaml"
        rune.write_text(rune.read_text().replace("by: -1", "by: 1"))
        status, reply = tree.routes.post_undo({"undoes": seq})
        assert (status, reply["status"]) == (409, "conflict") and "by: 1" in block(tree)

    def test_a_running_pass_holds_the_writes_which_apply_in_order_when_it_ends(self, tmp_path):
        """While a pass holds the lock, two presses' deltas made against the same block wait in the journal, and an undo of a third cancels it; once the pass ends the two apply in order, the second over the first, and the third never does."""
        tree = Tree(tmp_path)
        base = base_of(tree)
        sets = lambda by: {
            "sets": [
                {"scope": "facing", "lead": LOOP_FULL[0], "follow": LOOP_FULL[1], "by": by, "base": base}
            ]
        }  # noqa: E731
        with store_lock.hold_flock(tree.routes.pass_lock) as handle:
            os.pwrite(handle, b"4242\n", 0)
            status, reply = tree.routes.post_delta(sets(1))
            assert (status, reply["status"]) == (202, "pending") and "4242" in reply["message"]
            status, reply = tree.routes.post_delta(sets(2))
            assert (status, reply["status"]) == (202, "pending")
            status, cancelled = tree.routes.post_delta(
                {
                    "sets": [
                        {
                            "scope": "facing",
                            "lead": FLIPPED_HALF_PAIR[0],
                            "follow": FLIPPED_HALF_PAIR[1],
                            "by": -1,
                            "base": base,
                        }
                    ]
                }
            )
            status, reply = tree.routes.post_undo({"undoes": cancelled["seq"]})
            assert (status, reply["status"]) == (200, "cancelled")
            assert block(tree) == ""
            assert tree.routes.values_payload()["journal"]["pending"] == 2
            tree.routes.drain()
            assert block(tree) == ""
        tree.routes.drain()
        assert block(tree) == "kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: 2}\n"
        journal = tree.routes.values_payload()["journal"]
        assert (journal["pending"], journal["dropped"]) == (0, [])

    def test_a_pending_change_the_file_no_longer_takes_is_dropped_and_reported(self, tmp_path):
        tree = Tree(tmp_path)
        base = base_of(tree)
        with store_lock.hold_flock(tree.routes.pass_lock):
            tree.routes.post_delta(
                {"sets": [{"scope": "letters", "lead": "qsNo", "follow": "qsTea", "by": -1, "base": base}]}
            )
            rune = tree.routes.runes_dir / "qsNo.yaml"
            rune.write_text(rune.read_text() + "kern:\n- {right: {family: qsTea}, by: 1}\n")
        status, reply = facing(tree, LOOP_FULL, -1)
        assert status == 200
        (dropped,) = reply["values"]["journal"]["dropped"]
        assert "not the one this change was made against" in dropped["reason"]
        assert dropped["request"]["delta"]["sets"][0]["by"] == -1
        assert (
            block(tree)
            == "kern:\n- {right: {family: qsTea}, by: 1}\n- {stance: loop, right: {family: qsTea, stance: full}, by: -1}\n"
        )


REAL_SERVER_CHILD = """
import sys
from pathlib import Path
from rebuild.pipeline import run_m1
from rebuild.review import accepted, journal, kern_routes, serve
from rebuild import test_review_kern_routes as tests
from rebuild.test_kern import tables
root = Path(sys.argv[1])
spec = tests.spec_load.load_spec(root / "alphabet" / "runes", root / "alphabet" / "script.yaml", tests.spec_load.DEFAULT_SCHEMA_DIR)
built = tests.RowTables(tables(spec, tests.ROWS))
kern_routes.KernRoutes.tables = lambda self: (built, "the-stamp", None)
kern_routes.KernRoutes._source_inputs = lambda self: "the-stamp"
run_m1.mint_cell_glyphs = tests.mint
original = kern_routes.KernRoutes.__init__
def init(self, *args, **kwargs):
    original(self, *args, **kwargs)
    self.registry = root / "alphabet" / "script.yaml"
    self.schema_dir = tests.spec_load.DEFAULT_SCHEMA_DIR
kern_routes.KernRoutes.__init__ = init
serve.REPO_ROOT = root
serve.REVIEW_DIR = root / "rebuild" / "out" / "review"
serve.M1_OUT = root / "rebuild" / "out" / "m1"
serve.RUNES_DIR = root / "alphabet" / "runes"
serve.CYCLE_SUMMARY_PATH = root / "rebuild" / "out" / "cycle_summary.json"
serve.AUTOSAVE_PATH = root / "verdicts-autosave.json"
serve.JOURNAL_PATH = root / journal.JOURNAL_NAME
serve.ACCEPTED_PATH = accepted.path_for(serve.AUTOSAVE_PATH)
serve.PORT = int(sys.argv[2])
serve.main()
"""


def post(port: int, path: str, body: object) -> tuple[int, dict, float]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            return reply.status, json.loads(reply.read()), time.perf_counter() - started
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read()), time.perf_counter() - started


def test_a_real_server_writes_a_copy_of_the_runes_journals_the_change_and_undoes_it(tmp_path):
    """A server whose routes write into a copy of the test alphabet's runes under `tmp_path` takes an edit, answers with its values and the block's new digest, keeps the journal under the tree's `var/kern/`, and undoes the edit, leaving the file byte for byte as it began."""
    corpus = tmp_path / "rebuild" / "out" / "review"
    corpus.mkdir(parents=True)
    (corpus / "manifest.json").write_text(
        json.dumps({"generated_at": "2026-11-20T00:00:00Z", "human_unit_ids": []})
    )
    (tmp_path / "rebuild" / "out" / "m1").mkdir(parents=True)
    (tmp_path / "alphabet").mkdir()
    load(tmp_path / "alphabet")
    rune = tmp_path / "alphabet" / "runes" / "qsNo.yaml"
    original = rune.read_bytes()
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-c", REAL_SERVER_CHILD, str(tmp_path), str(port)],
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                status, _headers, body = get(port, "/kern")
                if status == 200:
                    break
            except OSError:
                pass
            assert server.poll() is None and time.monotonic() < deadline, "the review server did not start"
            time.sleep(0.1)
        values = json.loads(body)
        assert values["live"]["error"] is None
        base = values["runes"]["qsNo"]["sha256"]
        status, reply, _seconds = post(
            port,
            "/kern",
            {
                "sets": [
                    {"scope": "facing", "lead": "qsNo[loop]", "follow": "qsTea[full]", "by": -1, "base": base}
                ]
            },
        )
        assert (status, reply["status"]) == (200, "applied")
        seq = reply["seq"]
        assert rune.read_text().endswith(
            "kern:\n- {stance: loop, right: {family: qsTea, stance: full}, by: -1}\n"
        )
        assert reply["values"]["runes"]["qsNo"]["sha256"] != base
        assert json.loads(get(port, "/kern")[2])["live"] == reply["values"]["live"]
        assert (tmp_path / "var" / "kern" / "journal.ndjson").is_file()
        status, reply, _seconds = post(port, "/kern", {"sets": "no"})
        assert (status, reply["status"]) == (400, "invalid")
        status, invalid, _seconds = post(port, "/kern/undo", {})
        assert (status, invalid["status"]) == (400, "invalid")
        status, marked, _seconds = post(
            port, "/kern/reviewed", {"scope": "letters", "lead": "qsNo", "follow": "qsTea", "reviewed": True}
        )
        assert (status, marked["status"]) == (422, "refused") and "carried over" in marked["message"]
        status, apart, _seconds = post(
            port, "/kern/apart", {"pairs": [{"lead": "qsNo[loop]", "follow": "qsTea[full]"}], "apart": True}
        )
        assert (status, apart["status"]) == (200, "apart")
        assert apart["values"]["apart"]["pairs"] == ["qsNo[loop]|qsTea[full]"]
        status, reply, _seconds = post(port, "/kern/undo", {"undoes": seq})
        assert (status, reply["status"]) == (200, "undone")
        assert rune.read_bytes() == original
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=30)


def journal_events(tree: Tree) -> list[str]:
    return [json.loads(line)["event"] for line in tree.routes.journal.path.read_text().splitlines()]


@pytest.mark.usefixtures("no_kernel", "minted")
class TestUndoNamesItsChange:
    def test_an_undo_of_a_change_that_is_not_the_newest_is_refused(self, tmp_path):
        tree = Tree(tmp_path)
        older = facing(tree, LOOP_FULL, -1)[1]["seq"]
        newer = facing(tree, FLIPPED_HALF_PAIR, 1)[1]["seq"]
        written = block(tree)
        status, reply = tree.routes.post_undo({"undoes": older})
        assert (status, reply["status"]) == (409, "conflict") and f"change {newer} is" in reply["message"]
        assert block(tree) == written
        assert tree.routes.post_undo({"undoes": newer})[1]["status"] == "undone"
        assert tree.routes.post_undo({"undoes": older})[1]["status"] == "undone"

    def test_a_queued_change_dropped_at_the_drain_leaves_the_change_before_it_alone(self, tmp_path):
        """The reviewed failure: A is written; during a pass B is queued and then undone. B's undo cancels B while it waits, so the drain neither writes B nor reverts A."""
        tree = Tree(tmp_path)
        assert facing(tree, FLIPPED_HALF_PAIR, 1)[0] == 200
        written = block(tree)
        with store_lock.hold_flock(tree.routes.pass_lock):
            status, queued = facing(tree, LOOP_FULL, -2)
            assert (status, queued["status"]) == (202, "pending")
            status, reply = tree.routes.post_undo({"undoes": queued["seq"]})
            assert (status, reply["status"], reply["cancels"]) == (200, "cancelled", queued["seq"])
        tree.routes.drain()
        assert block(tree) == written
        assert journal_events(tree) == ["applied", "pending", "cancelled"]
        assert tree.routes.values_payload()["journal"]["pending"] == 0

    def test_an_undo_of_a_queued_change_the_drain_dropped_reverts_nothing(self, tmp_path):
        tree = Tree(tmp_path)
        assert facing(tree, FLIPPED_HALF_PAIR, 1)[0] == 200
        written = block(tree)
        with store_lock.hold_flock(tree.routes.pass_lock):
            queued = facing(tree, LOOP_FULL, -2)[1]
        tree.routes.drain()
        assert journal_events(tree)[-1] == "dropped"
        status, reply = tree.routes.post_undo({"undoes": queued["seq"]})
        assert (status, reply["status"]) == (200, "nothing") and "E-CONTACT" in reply["message"]
        assert block(tree) == written

    def test_an_undo_of_a_queued_change_the_drain_wrote_reverts_what_it_wrote(self, tmp_path):
        tree = Tree(tmp_path)
        with store_lock.hold_flock(tree.routes.pass_lock):
            queued = facing(tree, LOOP_FULL, 1)[1]
        tree.routes.drain()
        assert block(tree) != ""
        status, reply = tree.routes.post_undo({"undoes": queued["seq"]})
        assert (status, reply["status"]) == (200, "undone") and block(tree) == ""

    def test_a_queued_undo_names_its_change_too(self, tmp_path):
        tree = Tree(tmp_path)
        older = facing(tree, LOOP_FULL, 1)[1]["seq"]
        newer = facing(tree, FLIPPED_HALF_PAIR, 1)[1]["seq"]
        written = block(tree)
        with store_lock.hold_flock(tree.routes.pass_lock):
            status, reply = tree.routes.post_undo({"undoes": older})
            assert (status, reply["status"]) == (202, "pending")
        tree.routes.drain()
        (dropped,) = tree.routes.values_payload()["journal"]["dropped"]
        assert f"change {newer} is" in dropped["reason"]
        assert block(tree) == written
        assert journal_events(tree)[-1] == "dropped"

    @pytest.mark.parametrize("body", [{}, None, {"undoes": "3"}, {"undoes": True}])
    def test_an_undo_that_names_no_change_is_answered_400(self, tmp_path, body):
        tree = Tree(tmp_path)
        assert tree.routes.post_undo(body)[0] == 400


@pytest.mark.usefixtures("no_kernel", "minted")
class TestNamesAndBounds:
    @pytest.mark.parametrize(
        ("scope", "lead", "follow"),
        [
            ("facing", "qsno[loop]", "qsTea[full]"),
            ("facing", "qsNo[loop]", "qstea[full]"),
            ("letters", "QSNO", "qsTea"),
        ],
    )
    def test_a_rune_named_in_another_case_is_refused_whatever_the_value(self, tmp_path, scope, lead, follow):
        tree = Tree(tmp_path)
        assert facing(tree, LOOP_FULL, -1)[0] == 200
        written = (tree.routes.runes_dir / "qsNo.yaml").read_bytes()
        for by in (None, 0, -1):
            status, reply = tree.routes.post_delta(
                {"sets": [{"scope": scope, "lead": lead, "follow": follow, "by": by, "base": base_of(tree)}]}
            )
            assert (status, reply["status"]) == (422, "refused") and "case-sensitive" in reply["message"]
        assert (tree.routes.runes_dir / "qsNo.yaml").read_bytes() == written
        assert live(reply)[LOOP_FULL] == -1

    def test_a_check_that_replaced_no_file_refuses(self, tmp_path):
        tree = Tree(tmp_path)
        context = tree.routes.context()
        with pytest.raises(kern_routes.Refused, match="no file named exactly"):
            tree.routes._validate(context, "qsno", (tree.routes.runes_dir / "qsNo.yaml").read_text())

    @pytest.mark.parametrize("by", [65, -65, 1000])
    def test_a_value_past_the_bound_is_answered_400(self, tmp_path, by):
        tree = Tree(tmp_path)
        status, reply = facing(tree, LOOP_FULL, by)
        assert (status, reply["status"]) == (400, "invalid") and "64" in reply["message"]

    def test_the_bound_is_the_schema_s(self, tmp_path):
        schema = json.loads((spec_load.DEFAULT_SCHEMA_DIR / "rune.schema.json").read_text())
        by = schema["$defs"]["kernRecord"]["properties"]["by"]
        assert (by["minimum"], by["not"]) == (-kern_routes.BY_LIMIT, {"minimum": kern_routes.BY_LIMIT + 1})
        assert load(tmp_path, {"qsNo": [{"right": {"family": "qsTea"}, "by": 64}]}).runes["qsNo"].kern.records
        with pytest.raises(spec_load.SpecError, match="forbidden form"):
            load(tmp_path, {"qsNo": [{"right": {"family": "qsTea"}, "by": 65}]})
        with pytest.raises(spec_load.SpecError, match=">= -64"):
            load(tmp_path, {"qsNo": [{"right": {"family": "qsTea"}, "by": -65}]})

    def test_a_result_the_kern_lookup_refuses_is_refused(self, tmp_path, monkeypatch):
        tree = Tree(tmp_path)

        def refuse(*_args):
            raise kern_routes.emit_gpos.KernPlanError("qsTea.full carries a cursive entry anchor")

        monkeypatch.setattr(kern_routes.emit_gpos, "kern_plan", refuse)
        status, reply = facing(tree, LOOP_FULL, -1)
        assert (status, reply["status"]) == (422, "refused")
        assert reply["issues"] == [["KernPlanError", "qsTea.full carries a cursive entry anchor"]]
        assert block(tree) == ""


@pytest.mark.usefixtures("no_kernel", "minted")
class TestLineEndingsOnDisk:
    def test_a_crlf_rune_keeps_its_bytes_through_an_edit_and_its_undo(self, tmp_path):
        tree = Tree(tmp_path)
        rune = tree.routes.runes_dir / "qsNo.yaml"
        rune.write_bytes(rune.read_bytes().replace(b"\n", b"\r\n"))
        original = rune.read_bytes()
        status, reply = letters(tree, "qsNo", "qsTea", -1)
        assert (status, reply["status"]) == (200, "applied")
        written = rune.read_bytes()
        assert written == original + b"kern:\r\n- {right: {family: qsTea}, by: -1}\r\n"
        assert (
            reply["values"]["runes"]["qsNo"]["sha256"] == hashlib.sha256(written[len(original) :]).hexdigest()
        )
        status, reply = facing(tree, LOOP_FULL, 1)
        assert status == 200 and rune.read_bytes().count(b"\r\n") == original.count(b"\r\n") + 3
        assert b"\n" not in rune.read_bytes().replace(b"\r\n", b"")
        assert letters(tree, "qsNo", "qsTea", None)[0] == 200
        assert facing(tree, LOOP_FULL, None)[0] == 200
        assert rune.read_bytes() == original


@pytest.mark.usefixtures("no_kernel", "minted")
class TestTheDrainNeverSticks:
    def test_a_queued_change_whose_check_raises_is_dropped_with_the_reason(self, tmp_path, monkeypatch):
        tree = Tree(tmp_path)
        with store_lock.hold_flock(tree.routes.pass_lock):
            facing(tree, LOOP_FULL, 1)
        tree.routes.values_payload()

        def broken(*_args):
            raise kern.KernInternalError("a join row breaks while a facing side is live")

        monkeypatch.setattr(kern_routes.emit_gpos, "kern_plan", broken)
        tree.routes.drain()
        journal = tree.routes.values_payload()["journal"]
        assert journal["pending"] == 0 and "KernInternalError" in journal["dropped"][0]["reason"]
        monkeypatch.undo()
        status, reply = facing(tree, LOOP_FULL, 1)
        assert (status, reply["status"]) == (200, "applied")

    def test_a_busy_journal_keeps_a_queued_change_for_a_bounded_number_of_tries(self, tmp_path, monkeypatch):
        tree = Tree(tmp_path)
        with store_lock.hold_flock(tree.routes.pass_lock):
            facing(tree, LOOP_FULL, 1)
        real = tree.routes.journal.append

        def busy(event):
            if event["event"] == "applied":
                raise kern_routes.kern_writer.JournalBusy("another process holds the journal's lock")
            return real(event)

        monkeypatch.setattr(tree.routes.journal, "append", busy)
        for _ in range(kern_routes.JOURNAL_BUSY_TRIES - 1):
            tree.routes.drain()
            assert tree.routes.journal.pending_count() == 1 and block(tree) == ""
        tree.routes.drain()
        assert tree.routes.journal.pending_count() == 0 and block(tree) == ""
        assert "stayed busy" in tree.routes.values_payload()["journal"]["dropped"][0]["reason"]

    def test_a_busy_journal_while_a_pass_runs_is_a_json_503(self, tmp_path, monkeypatch):
        tree = Tree(tmp_path)

        def busy(event):
            raise kern_routes.kern_writer.JournalBusy("another process holds the journal's lock")

        monkeypatch.setattr(tree.routes.journal, "append", busy)
        with store_lock.hold_flock(tree.routes.pass_lock):
            status, reply = facing(tree, LOOP_FULL, 1)
        assert (status, reply["status"]) == (503, "error") and "journal" in reply["message"]


def test_a_refusal_is_kept_only_when_it_is_the_parser_s_verdict(monkeypatch):
    kern_routes.block_refusal.cache_clear()
    calls: list[int] = []
    real = kern_routes.kern_writer.parse_block

    def flaky(block, file, newline="\n"):
        calls.append(1)
        if len(calls) == 1:
            raise KeyError("an emitter's state")
        return real(block, file, newline)

    monkeypatch.setattr(kern_routes.kern_writer, "parse_block", flaky)
    block_text = "kern:\n- {right: {family: qsTea}, by: -1}\n"
    with pytest.raises(KeyError):
        kern_routes.block_refusal(block_text, "f")
    assert kern_routes.block_refusal(block_text, "f") is None
    assert kern_routes.block_refusal(block_text, "f") is None and len(calls) == 2
    kern_routes.block_refusal.cache_clear()


def import_pairs(tree: Tree, *entries: tuple[str, str, int]) -> None:
    """Write an `imported.json` as rebuild/tools/import_kerning.py does, for the facing pairs given."""
    path = tree.routes.imported_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "format": "ams-kern-imported/1",
                "pairs": [
                    {
                        "lead": lead,
                        "follow": follow,
                        "by": by,
                        "rune": "qsNo",
                        "file": "f",
                        "scope": "facing",
                        "line": "- {}",
                    }
                    for lead, follow, by in entries
                ],
            }
        )
    )


@pytest.mark.usefixtures("no_kernel", "minted")
class TestCarriedOverMarks:
    def test_the_answer_lists_the_imported_pairs_and_the_marks(self, tmp_path):
        tree = Tree(tmp_path)
        assert tree.routes.values_payload()["carried"] == {
            "pairs": [],
            "reviewed": [],
            "stale": [],
            "error": None,
        }
        import_pairs(tree, (*LOOP_FULL, -1), (*FLIPPED_HALF_PAIR, -1))
        payload = tree.routes.values_payload()
        assert kern_routes.check_values_payload(payload) == []
        assert [(entry["lead"], entry["by"]) for entry in payload["carried"]["pairs"]] == [
            ("qsNo[loop]", -1),
            ("qsNo[flipped]", -1),
        ]

    def test_a_facing_pair_or_every_pair_of_two_letters_is_marked_and_unmarked(self, tmp_path):
        tree = Tree(tmp_path)
        import_pairs(tree, (*LOOP_FULL, -1), (*FLIPPED_HALF_PAIR, -1))
        status, reply = tree.routes.post_reviewed(
            {"scope": "facing", "lead": LOOP_FULL[0], "follow": LOOP_FULL[1], "reviewed": True}
        )
        assert (status, reply["status"]) == (200, "reviewed")
        assert reply["values"]["carried"]["reviewed"] == ["qsNo[loop]|qsTea[full]"]
        status, reply = tree.routes.post_reviewed(
            {"scope": "letters", "lead": "qsNo", "follow": "qsTea", "reviewed": True}
        )
        assert reply["values"]["carried"]["reviewed"] == [
            "qsNo[flipped]|qsTea[half]",
            "qsNo[loop]|qsTea[full]",
        ]
        status, reply = tree.routes.post_reviewed(
            {"scope": "letters", "lead": "qsNo", "follow": "qsTea", "reviewed": False}
        )
        assert (status, reply["values"]["carried"]["reviewed"]) == (200, [])
        assert json.loads(tree.routes.reviewed_path.read_text())["format"] == kern_routes.REVIEWED_FORMAT

    @pytest.mark.parametrize(
        "body",
        [
            None,
            {"scope": "facing", "lead": "qsNo[loop]", "follow": "qsTea[full]"},
            {"scope": "facing", "lead": "qsNo[exit=none]", "follow": "qsTea", "reviewed": True},
            {"scope": "letters", "lead": "qsNo[loop]", "follow": "qsTea", "reviewed": True},
            {"scope": "rune", "lead": "qsNo", "follow": "qsTea", "reviewed": True},
        ],
    )
    def test_a_body_that_is_not_a_mark_is_answered_400(self, tmp_path, body):
        tree = Tree(tmp_path)
        status, reply = tree.routes.post_reviewed(body)
        assert (status, reply["status"]) == (400, "invalid") and "values" in reply

    def test_changing_an_imported_value_in_the_editor_marks_it(self, tmp_path):
        tree = Tree(tmp_path)
        import_pairs(tree, (*LOOP_FULL, 0), (*FLIPPED_HALF_PAIR, 0))
        status, reply = facing(tree, LOOP_FULL, -1)
        assert status == 200
        assert reply["values"]["carried"]["reviewed"] == ["qsNo[loop]|qsTea[full]"]


@pytest.mark.usefixtures("no_kernel", "minted")
class TestMarksStayHonest:
    def test_undoing_a_change_takes_off_exactly_the_marks_it_made(self, tmp_path):
        tree = Tree(tmp_path)
        import_pairs(tree, (*LOOP_FULL, 0), (*FLIPPED_HALF_PAIR, 0))
        tree.routes.post_reviewed(
            {
                "scope": "facing",
                "lead": FLIPPED_HALF_PAIR[0],
                "follow": FLIPPED_HALF_PAIR[1],
                "reviewed": True,
            }
        )
        status, reply = letters(tree, "qsNo", "qsTea", -1)
        assert status == 200
        assert reply["values"]["carried"]["reviewed"] == [
            "qsNo[flipped]|qsTea[half]",
            "qsNo[loop]|qsTea[full]",
        ]
        status, reply = tree.routes.post_undo({"undoes": reply["seq"]})
        assert (status, reply["status"]) == (200, "undone")
        assert reply["values"]["carried"]["reviewed"] == ["qsNo[flipped]|qsTea[half]"]
        events = [json.loads(line) for line in tree.routes.journal.path.read_text().splitlines()]
        assert (events[0]["marked"], events[1]["unmarked"]) == (
            ["qsNo[loop]|qsTea[full]"],
            ["qsNo[loop]|qsTea[full]"],
        )

    def test_a_letters_mark_reaches_only_the_imported_pairs(self, tmp_path):
        tree = Tree(tmp_path)
        import_pairs(tree, (*LOOP_FULL, 0))
        status, reply = tree.routes.post_reviewed(
            {"scope": "letters", "lead": "qsNo", "follow": "qsTea", "reviewed": True}
        )
        assert (status, reply["pairs"]) == (200, ["qsNo[loop]|qsTea[full]"])
        assert json.loads(tree.routes.reviewed_path.read_text())["reviewed"] == ["qsNo[loop]|qsTea[full]"]

    @pytest.mark.parametrize(
        ("lead", "follow"),
        [("qsno[loop]", "qsTea[full]"), ("qsNo[loop]", "qsTea[half]"), ("qsNo[flipped]", "qsTea[half]")],
    )
    def test_a_facing_mark_of_a_pair_the_build_or_the_import_does_not_have_is_refused(
        self, tmp_path, lead, follow
    ):
        tree = Tree(tmp_path)
        import_pairs(tree, (*LOOP_FULL, 0))
        status, reply = tree.routes.post_reviewed(
            {"scope": "facing", "lead": lead, "follow": follow, "reviewed": True}
        )
        assert (status, reply["status"]) == (422, "refused")
        assert not tree.routes.reviewed_path.exists()

    def test_marks_that_cannot_be_read_are_reported_and_never_replaced(self, tmp_path):
        tree = Tree(tmp_path)
        import_pairs(tree, (*LOOP_FULL, 0))
        tree.routes.reviewed_path.write_text("[not json")
        assert "is not JSON" in tree.routes.values_payload()["carried"]["error"]
        status, reply = tree.routes.post_reviewed(
            {"scope": "facing", "lead": LOOP_FULL[0], "follow": LOOP_FULL[1], "reviewed": True}
        )
        assert (status, reply["status"]) == (409, "refused")
        assert facing(tree, LOOP_FULL, -1)[0] == 200
        assert tree.routes.reviewed_path.read_text() == "[not json"

    def test_imported_or_marked_pairs_whose_sides_the_build_lost_are_reported(self, tmp_path):
        tree = Tree(tmp_path)
        import_pairs(tree, (*LOOP_FULL, 0), ("qsNo", "qsTea", -1))
        assert tree.routes.values_payload()["carried"]["stale"] == ["qsNo|qsTea"]


@pytest.mark.usefixtures("no_kernel", "minted")
class TestApartMarks:
    def test_the_answer_lists_the_marks_and_a_mark_is_made_and_taken_off(self, tmp_path):
        tree = Tree(tmp_path)
        assert tree.routes.values_payload()["apart"] == {"pairs": [], "stale": [], "error": None}
        both = [{"lead": lead, "follow": follow} for lead, follow in (LOOP_FULL, FLIPPED_HALF_PAIR)]
        status, reply = tree.routes.post_apart({"pairs": both, "apart": True})
        assert (status, reply["status"]) == (200, "apart")
        assert reply["pairs"] == ["qsNo[flipped]|qsTea[half]", "qsNo[loop]|qsTea[full]"]
        assert reply["values"]["apart"]["pairs"] == reply["pairs"]
        assert kern_routes.check_values_payload(reply["values"]) == []
        assert json.loads(tree.routes.apart_path.read_text())["format"] == kern_routes.APART_FORMAT
        status, reply = tree.routes.post_apart({"pairs": both[:1], "apart": False})
        assert (status, reply["status"]) == (200, "together")
        assert reply["values"]["apart"]["pairs"] == ["qsNo[flipped]|qsTea[half]"]

    def test_a_mark_changes_no_rune_file_and_is_not_journaled(self, tmp_path):
        tree = Tree(tmp_path)
        before = block(tree)
        tree.routes.post_apart({"pairs": [{"lead": LOOP_FULL[0], "follow": LOOP_FULL[1]}], "apart": True})
        assert block(tree) == before
        assert not tree.routes.journal.path.exists()

    @pytest.mark.parametrize(
        "body",
        [
            None,
            {"pairs": [{"lead": "qsNo[loop]", "follow": "qsTea[full]"}]},
            {"pairs": [], "apart": True},
            {"pairs": [{"lead": "qsNo[loop]"}], "apart": True},
            {"pairs": "qsNo[loop]|qsTea[full]", "apart": True},
            {"lead": "qsNo[loop]", "follow": "qsTea[full]", "apart": True},
        ],
    )
    def test_a_body_that_is_not_a_mark_is_answered_400(self, tmp_path, body):
        tree = Tree(tmp_path)
        status, reply = tree.routes.post_apart(body)
        assert (status, reply["status"]) == (400, "invalid") and "values" in reply

    @pytest.mark.parametrize(("lead", "follow"), [("qsNo", "qsTea"), ("qsno[loop]", "qsTea[full]")])
    def test_a_mark_of_a_pair_the_build_does_not_have_refuses_the_whole_request(self, tmp_path, lead, follow):
        tree = Tree(tmp_path)
        status, reply = tree.routes.post_apart(
            {
                "pairs": [{"lead": LOOP_FULL[0], "follow": LOOP_FULL[1]}, {"lead": lead, "follow": follow}],
                "apart": True,
            }
        )
        assert (status, reply["status"]) == (422, "refused")
        assert not tree.routes.apart_path.exists()

    def test_marks_that_cannot_be_read_are_reported_and_never_replaced(self, tmp_path):
        tree = Tree(tmp_path)
        tree.routes.apart_path.parent.mkdir(parents=True, exist_ok=True)
        tree.routes.apart_path.write_text("[not json")
        assert "is not JSON" in tree.routes.values_payload()["apart"]["error"]
        status, reply = tree.routes.post_apart(
            {"pairs": [{"lead": LOOP_FULL[0], "follow": LOOP_FULL[1]}], "apart": True}
        )
        assert (status, reply["status"]) == (409, "refused")
        assert tree.routes.apart_path.read_text() == "[not json"

    def test_a_marked_pair_the_build_lost_is_reported(self, tmp_path):
        tree = Tree(tmp_path)
        tree.routes.apart_path.parent.mkdir(parents=True, exist_ok=True)
        tree.routes.apart_path.write_text(
            json.dumps(
                {"format": kern_routes.APART_FORMAT, "apart": ["qsNo|qsTea", "qsNo[loop]|qsTea[full]"]}
            )
        )
        assert tree.routes.values_payload()["apart"] == {
            "pairs": ["qsNo[loop]|qsTea[full]", "qsNo|qsTea"],
            "stale": ["qsNo|qsTea"],
            "error": None,
        }
