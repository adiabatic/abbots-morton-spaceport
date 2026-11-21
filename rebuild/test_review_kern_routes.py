"""Tests for the review server's kerning routes, `rebuild/review/kern_routes.py`: `GET /kern`'s answer over rebuild/test_kern.py's small alphabet with tables the test hands it, the `kern:` block each rune file reports, the shape `check_values_payload` holds the answer and the app's fixture to, that answering never runs the kernel or cargo, and both routes on a real server (`rebuild/test_review_autosave.py`'s harness) on a port of its own, over a tree under `tmp_path`."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rebuild.pipeline import kern, kern_inventory, kernel_exec, spec_load
from rebuild.review import kern_routes, store_lock
from rebuild.test_kern import NO_FLIPPED, NO_LOOP, TEA_FULL, TEA_HALF, glyphs, load, tables
from rebuild.test_review_autosave import start_server

FIXTURES = Path(__file__).resolve().parent / "review" / "fixtures"
FLIPPED_HALF = [{"stance": "flipped", "right": {"family": "qsTea", "stance": "half"}, "by": -1}]
ROWS = {"default": [(NO_LOOP, TEA_FULL, "break"), (NO_FLIPPED, TEA_HALF, "break")]}


class Tree:
    """A repository tree under `tmp_path` holding the test alphabet's rune files with `kerns`, and the routes over it, handed the tables `ROWS` gives in place of reading them from disk."""

    def __init__(self, tmp_path: Path, kerns: dict | None = None) -> None:
        self.root = tmp_path
        self.spec = load(tmp_path, kerns)
        self.m1_out = tmp_path / "rebuild" / "out" / "m1"
        self.m1_out.mkdir(parents=True)
        self.routes = kern_routes.KernRoutes(tmp_path, self.m1_out, tables_inputs=lambda: "the-stamp")
        self.routes.runes_dir = tmp_path / "runes"
        self.routes.registry = tmp_path / "script.yaml"
        self.routes.schema_dir = spec_load.DEFAULT_SCHEMA_DIR
        self.routes.pass_lock = tmp_path / "var" / "cycle" / "pass.lock"
        built = tables(self.spec, ROWS)
        self.routes.tables = lambda: (built, "the-stamp", None)  # pyright: ignore[reportAttributeAccessIssue]
        self.glyphs = glyphs(self.spec, ROWS)

    def classes(self, values: list) -> None:
        (self.m1_out / kern.KERN_CLASSES_JSON).write_text(
            json.dumps({"format": kern.KERN_CLASSES_FORMAT, "leading": {}, "following": {}, "values": values})
        )


def mint(spec, built):
    return glyphs(spec, ROWS)


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
            }
        ]
        assert blocks["qsTea"] == {
            "file": "runes/qsTea.yaml",
            "sha256": hashlib.sha256(b"").hexdigest(),
            "text": "",
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
