"""Tests for the kern editor's block writer and journal, `engine/review/kern_writer.py`: the bytes before a block are never touched, a block the writer writes is one `tools/reflow_yaml.py` leaves as it is, every record form the schema allows reads back and writes back exactly, the blocks it cannot write back are refused with a message, the block goes when its last record does, the canonical order, the scopes, and the journal's fold."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest
import yaml
from reflow_yaml import reflow
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from engine.pipeline import kern
from engine.pipeline.model import Provenance
from engine.review import kern_writer
from engine.test_kern import load

REPO = Path(__file__).resolve().parent.parent
CODEPOINTS = {
    "qsTea": (0xE652,),
    "qsDay": (0xE653,),
    "qsDay_qsUtter": (0xE653, 0xE67A),
    "qsGay": (0xE655,),
    "qsNo": (0xE666,),
}


def order(rune: str) -> tuple[int, ...]:
    return CODEPOINTS.get(rune, (0x110000,))


# Every key a record and its follower may carry, and each scalar shape a `why` takes.
EVERY_FORM = [
    {"right": {"family": "qsTea"}, "by": -1},
    {"stance": "flipped", "right": {"family": "qsTea", "stance": "half", "exit": "x-height"}, "by": -2},
    {"entry": "none", "right": {"family": "qsTea"}, "by": 0},
    {"bitmap": "shortened-top", "right": {"family": "qsTea", "bitmap": "padded-left-1"}, "by": 1},
    {"right": {"family": ["qsTea", "qsDay"]}, "by": -1},
    {
        "right": {
            "class": "can-enter-at-baseline",
            "except": [{"family": "qsNo"}, {"class": "tall", "stance": "half"}],
        },
        "by": -1,
    },
    {"right": {"family": "qsGay", "except": [{"family": "qsGay", "exit": "none", "bitmap": "low"}]}, "by": 2},
    {"right": {"family": "qsDay_qsUtter"}, "by": -1, "why": "I'd rather: see ·No ~b~ ·Tea, #this {x}, y"},
    {"right": {"family": "qsDay"}, "by": -1, "why": "yes"},
    {"right": {"family": "qsDay"}, "by": -3, "why": "12"},
    {"right": {"family": "qsDay"}, "by": -4, "why": "- a dash first"},
    {"right": {"family": "qsDay"}, "by": -5, "why": "“curly” and plain"},
]


def node(data):
    """A ruamel record built from plain data as the writer builds one: flow-style mappings and lists, and each string as the writer writes a new one (`kern_writer._scalar`)."""
    if isinstance(data, dict):
        built = CommentedMap()
        for key, value in data.items():
            built[key] = node(value)
        built.fa.set_flow_style()
        return built
    if isinstance(data, list):
        items = CommentedSeq([node(value) for value in data])
        items.fa.set_flow_style()
        return items
    return kern_writer._scalar(data) if isinstance(data, str) else data


def every_form_block() -> str:
    return kern_writer.render_block([node(data) for data in EVERY_FORM])


def rune_text_before_its_block(rune: str) -> str:
    """Return a live rune file's text up to its `kern:` line, the whole file when it has no block."""
    text = (REPO / "glyph_data" / "runes" / f"{rune}.yaml").read_text(encoding="utf-8")
    return kern_writer.split_file(text)[0]


class TestTheFile:
    def test_a_block_is_cut_at_its_kern_line_and_spliced_back_after_the_bytes_before_it(self):
        text = "rune: qsNo\npolicy: {}\nkern:\n- {right: {family: qsTea}, by: -1}\n"
        prefix, block = kern_writer.split_file(text)
        assert (prefix, block) == ("rune: qsNo\npolicy: {}\n", "kern:\n- {right: {family: qsTea}, by: -1}\n")
        assert kern_writer.splice(prefix, block, "f") == text
        assert kern_writer.split_file("rune: qsNo\n  kern: nested\n") == ("rune: qsNo\n  kern: nested\n", "")

    def test_the_bytes_before_the_block_are_untouched_and_the_last_record_takes_the_key_with_it(
        self, tmp_path
    ):
        """A real rune file gains a block, has it rewritten and loses it: every byte before the `kern:` line stays the file's own throughout, and the file ends byte for byte as it began. The test starts from the file's text before any `kern:` block, so it holds whatever the rune's own block says."""
        original = rune_text_before_its_block("qsNo")
        prefix, block = kern_writer.split_file(original)
        assert block == "" and prefix == original
        scope = kern_writer.facing_scope("qsNo[flipped]", "qsTea[half exit=x-height]")
        first = kern_writer.splice(prefix, kern_writer.render_block([scope.record(-1, None)]), "f")
        assert first.startswith(original) and first[len(original) :].startswith("kern:\n")
        records = kern_writer.parse_block(kern_writer.split_file(first)[1], "f")
        second = kern_writer.splice(
            kern_writer.split_file(first)[0],
            kern_writer.render_block([kern_writer.with_value(records[0], -2, "a note"), node(EVERY_FORM[0])]),
            "f",
        )
        assert second.startswith(original)
        assert (
            kern_writer.splice(kern_writer.split_file(second)[0], kern_writer.render_block([]), "f")
            == original
        )

    def test_a_file_that_does_not_end_on_a_newline_takes_no_block(self):
        with pytest.raises(kern_writer.BlockRefused, match="newline"):
            kern_writer.splice("rune: qsNo", "kern:\n- {right: {family: qsTea}, by: -1}\n", "f")
        assert kern_writer.splice("rune: qsNo", "", "f") == "rune: qsNo"

    def test_reflow_leaves_a_written_block_as_it_is(self, tmp_path):
        """The writer's form is the one `tools/reflow_yaml.py` keeps: a real rune file with every record form appended is a reflow no-op."""
        runes = tmp_path / "runes"
        runes.mkdir()
        path = runes / "qsNo.yaml"
        source = rune_text_before_its_block("qsNo")
        path.write_text(kern_writer.splice(source, every_form_block(), "f"), encoding="utf-8")
        written = path.read_bytes()
        assert reflow(path) is False
        assert path.read_bytes() == written


class TestRoundTrips:
    def test_every_record_form_reads_back_and_writes_back_exactly(self):
        block = every_form_block()
        records = kern_writer.parse_block(block, "f")
        assert [kern_writer.plain(record) for record in records] == EVERY_FORM
        assert kern_writer.render_block(records) == block
        assert yaml.safe_load(block)["kern"] == EVERY_FORM

    def test_a_string_yaml_1_1_reads_otherwise_is_quoted_and_a_plain_one_is_not(self):
        lines = every_form_block().splitlines()
        assert lines[9].endswith('why: "yes"}') and lines[10].endswith('why: "12"}')
        assert lines[4] == "- {bitmap: shortened-top, right: {family: qsTea, bitmap: padded-left-1}, by: 1}"

    def test_a_hand_written_record_keeps_its_key_order_and_quoting(self):
        block = "kern:\n- {by: -1, right: {family: 'qsTea'}, why: 'single quoted'}\n"
        records = kern_writer.parse_block(block, "f")
        assert kern_writer.render_block(records) == block
        changed = kern_writer.with_value(records[0], -2, None)
        assert (
            kern_writer.render_block([changed])
            == "kern:\n- {by: -2, right: {family: 'qsTea'}, why: 'single quoted'}\n"
        )

    def test_the_writer_builds_the_records_the_loader_builds(self, tmp_path):
        records = [
            {"right": {"family": "qsTea"}, "by": -1},
            {"stance": "flipped", "right": {"family": "qsTea", "stance": "half"}, "by": -2, "why": "note"},
        ]
        spec = load(tmp_path, {"qsNo": records})
        loaded = spec.runes["qsNo"].kern.records
        built = tuple(
            kern_writer.kern_record("qsNo", node(data), record.provenance or Provenance("", ""))
            for data, record in zip(records, loaded)
        )
        assert built == loaded


class TestRefusals:
    @pytest.mark.parametrize(
        ("block", "says"),
        [
            ("kern:\n# a comment\n- {right: {family: qsTea}, by: -1}\n", "comment"),
            ("kern:\n- {right: {family: qsTea}, by: -1}  # a comment\n", "comment"),
            ("kern:\n- {right: {family: qsTea}, by: -1}\n\n", "blank line"),
            ("kern:\n- right: {family: qsTea}\n  by: -1\n  why: |\n    two\n    lines\n", "multi-line"),
            ("kern:\n- right: {family: qsTea}\n  by: -1\n", "one flow-style record per line"),
            ("kern:\n- {right: {family: qsTea},  by: -1}\n", "one flow-style record per line"),
            ("kern:\n- {right: {family: qsTea}, by: -1, why: yes}\n", "reads differently"),
            ("kern:\n- {right: {family: qsTea}, by: -1}\npolicy: {}\n", "last key"),
            ("kern: {right: {family: qsTea}}\n", "not a list"),
            ("kern:\n- {right: {family: qsTea}, by: -1\n", "does not parse"),
        ],
    )
    def test_a_block_the_writer_cannot_write_back_is_refused_with_what_to_fix(self, block, says):
        with pytest.raises(kern_writer.BlockRefused, match=says):
            kern_writer.parse_block(block, "glyph_data/runes/qsNo.yaml")

    def test_two_records_of_one_scope_are_refused(self):
        block = "kern:\n- {right: {family: qsTea}, by: -1}\n- {right: {family: qsTea}, by: -2}\n"
        records = kern_writer.parse_block(block, "f")
        with pytest.raises(kern_writer.BlockRefused, match="merge them"):
            kern_writer.scope_index(records, kern_writer.letters_scope("qsNo", "qsTea"), "f")

    def test_an_empty_block_has_no_records_and_writes_nothing(self):
        assert kern_writer.parse_block("", "f") == []
        assert kern_writer.parse_block("kern: []\n", "f") == []
        assert kern_writer.parse_block("kern:\n", "f") == []
        assert kern_writer.render_block([]) == ""


class TestOrderAndScopes:
    def test_the_canonical_order_is_by_follower_then_broader_first(self):
        shuffled = [
            {"stance": "flipped", "right": {"family": "qsTea", "stance": "half"}, "by": -1},
            {"right": {"family": "qsDay_qsUtter"}, "by": -1},
            {"right": {"family": "qsGay"}, "by": -1},
            {"right": {"family": "qsDay"}, "by": -1},
            {"right": {"class": "can-enter-at-baseline"}, "by": -1},
            {"right": {"family": "qsTea"}, "by": -1},
            {"right": {"family": ["qsTea", "qsDay"]}, "by": -1},
            {"stance": "flipped", "right": {"family": "qsTea"}, "by": -1},
        ]
        ordered = [
            kern_writer.plain(record)
            for record in kern_writer.canonical([node(data) for data in shuffled], order)
        ]
        assert ordered == [
            {"right": {"class": "can-enter-at-baseline"}, "by": -1},
            {"right": {"family": ["qsTea", "qsDay"]}, "by": -1},
            {"right": {"family": "qsTea"}, "by": -1},
            {"stance": "flipped", "right": {"family": "qsTea"}, "by": -1},
            {"stance": "flipped", "right": {"family": "qsTea", "stance": "half"}, "by": -1},
            {"right": {"family": "qsDay"}, "by": -1},
            {"right": {"family": "qsDay_qsUtter"}, "by": -1},
            {"right": {"family": "qsGay"}, "by": -1},
        ]

    @pytest.mark.parametrize(
        ("lead", "follow", "record"),
        [
            ("qsNo", "qsTea", {"right": {"family": "qsTea"}, "by": -1}),
            (
                "qsNo[flipped]",
                "qsTea[half exit=x-height]",
                {
                    "stance": "flipped",
                    "right": {"family": "qsTea", "stance": "half", "exit": "x-height"},
                    "by": -1,
                },
            ),
            (
                "qsGay[sole entry=baseline]",
                "qsTea",
                {"stance": "sole", "entry": "baseline", "right": {"family": "qsTea"}, "by": -1},
            ),
            (
                "qsOut_qsTea[sole entry=baseline bitmap=low-bend]",
                "qsTea[half exit=x-height bitmap=padded-left-1]",
                {
                    "stance": "sole",
                    "entry": "baseline",
                    "bitmap": "low-bend",
                    "right": {
                        "family": "qsTea",
                        "stance": "half",
                        "exit": "x-height",
                        "bitmap": "padded-left-1",
                    },
                    "by": -1,
                },
            ),
        ],
    )
    def test_a_facing_pair_names_the_record_whose_keys_are_its_sides_keys(self, lead, follow, record):
        scope = kern_writer.facing_scope(lead, follow)
        written = scope.record(-1, None)
        assert kern_writer.plain(written) == record
        assert scope.label == f"{lead} | {follow}"
        assert kern_writer.record_sides(scope.rune, written) == [lead, follow]

    def test_a_record_with_a_class_a_family_list_or_an_except_is_no_scope_s(self):
        scope = kern_writer.letters_scope("qsNo", "qsTea")
        shapes = [
            {"right": {"family": ["qsTea"]}, "by": -1},
            {"right": {"family": "qsTea", "class": "can-enter-at-baseline"}, "by": -1},
            {"right": {"family": "qsTea", "except": [{"stance": "half"}]}, "by": -1},
            {"stance": "flipped", "right": {"family": "qsTea"}, "by": -1},
        ]
        for data in shapes:
            assert not scope.matches(node(data))
        assert [kern_writer.record_sides("qsNo", node(data)) for data in shapes[:3]] == [None, None, None]
        assert kern_writer.record_sides("qsNo", node(shapes[3])) == ["qsNo[flipped]", "qsTea"]
        assert scope.matches(node({"right": {"family": "qsTea"}, "by": 3, "why": "kept"}))

    @pytest.mark.parametrize(
        "name", ["qsNo[]", "qsNo[exit=none]", "qsNo[entry=none flipped]", "qs No", "qsNo[a b]"]
    )
    def test_a_name_that_is_not_a_leading_side_is_refused(self, name):
        with pytest.raises(ValueError):
            kern_writer.parse_side(name, kern.LEADING)

    def test_a_value_change_keeps_a_note_unless_it_brings_one(self):
        record = node({"right": {"family": "qsTea"}, "by": -1, "why": "kept"})
        assert kern_writer.plain(kern_writer.with_value(record, -2, None))["why"] == "kept"
        assert kern_writer.plain(kern_writer.with_value(record, -2, "new"))["why"] == "new"
        assert "why" not in kern_writer.plain(kern_writer.with_value(record, -2, ""))


class TestTheJournal:
    def test_the_fold_follows_pending_requests_undos_cancels_and_drops(self, tmp_path):
        journal = kern_writer.Journal(tmp_path / "kern")
        first = journal.append({"event": "applied", "rune": "qsNo", "before": "", "after": "kern:\n"})
        second = journal.append(
            {"event": "applied", "rune": "qsNo", "before": "kern:\n", "after": "kern: []\n"}
        )
        queued = journal.append({"event": "pending", "request": {"kind": "undo", "undoes": second["seq"]}})
        other = journal.append({"event": "pending", "request": {"kind": "delta", "delta": {}}})
        third = journal.append({"event": "pending", "request": {"kind": "delta", "delta": {}}})
        assert [seq for seq, _event in journal.pending_items()] == [queued["seq"], other["seq"], third["seq"]]
        assert journal.undo_target() == second["seq"]
        journal.append({"event": "undo", "undoes": second["seq"], "pending": queued["seq"]})
        journal.append({"event": "dropped", "pending": other["seq"], "reason": "stale"})
        journal.append({"event": "cancelled", "pending": third["seq"]})
        summary = journal.summary()
        assert (summary["seq"], summary["pending"]) == (8, 0)
        assert [event["reason"] for event in summary["dropped"]] == ["stale"]
        assert journal.undo_target() == first["seq"]
        assert [
            (journal.settled(seq) or {}).get("event") for seq in (other["seq"], third["seq"], queued["seq"])
        ] == [
            "dropped",
            "cancelled",
            "undo",
        ]
        assert (journal.applied(first["seq"]) or {}).get("after") == "kern:\n"
        assert (journal.applied(second["seq"]) or {}).get("undone") is True
        assert journal.applied(queued["seq"]) is None

    def test_an_undo_of_an_older_change_takes_it_off_the_stack_and_leaves_the_newest_on_top(self, tmp_path):
        journal = kern_writer.Journal(tmp_path / "kern")
        older = journal.append({"event": "applied", "rune": "qsNo", "before": "", "after": ""})
        newer = journal.append({"event": "applied", "rune": "qsNo", "before": "", "after": ""})
        journal.append({"event": "undo", "undoes": older["seq"]})
        assert journal.undo_target() == newer["seq"]

    def test_a_torn_last_line_loses_only_itself(self, tmp_path):
        journal = kern_writer.Journal(tmp_path / "kern")
        journal.append({"event": "applied", "rune": "qsNo", "before": "", "after": ""})
        with journal.path.open("ab") as handle:
            handle.write(b'{"seq": 2, "event": "appl')
        assert journal.summary()["seq"] == 1
        written = journal.append({"event": "pending", "request": {"kind": "undo"}})
        assert written["seq"] == 2
        lines = journal.path.read_bytes().splitlines()
        assert len(lines) == 3 and json.loads(lines[-1])["seq"] == 2
        assert [seq for seq, _event in journal.pending_items()] == [2]

    def test_a_replaced_journal_is_read_from_its_start_under_another_id(self, tmp_path):
        journal = kern_writer.Journal(tmp_path / "kern")
        journal.append({"event": "pending", "request": {"kind": "undo"}})
        journal.append({"event": "applied", "rune": "qsNo", "before": "", "after": ""})
        before = journal.summary()
        assert journal.pending_count() == 1 and before["id"] is not None
        replacement = tmp_path / "other.ndjson"
        replacement.write_text("")
        shutil.move(replacement, journal.path)
        assert journal.pending_count() == 0 and journal.summary()["seq"] == 0
        journal.append({"event": "applied", "rune": "qsNo", "before": "", "after": ""})
        after = journal.summary()
        assert after["seq"] == 1 and after["id"] not in (None, before["id"])
        journal.path.unlink()
        assert journal.summary() == {"id": None, "seq": 0, "pending": 0, "dropped": []}


class TestLineEndings:
    def test_a_crlf_block_reads_back_and_a_written_one_keeps_the_file_s_line_ending(self):
        text = "rune: qsNo\r\npolicy: {}\r\n"
        assert kern_writer.line_ending(text) == "\r\n" and kern_writer.line_ending("rune: qsNo\n") == "\n"
        block = kern_writer.render_block([node(EVERY_FORM[0]), node(EVERY_FORM[1])], "\r\n")
        assert block.count("\r\n") == 3 and "\n" not in block.replace("\r\n", "")
        records = kern_writer.parse_block(block, "f", "\r\n")
        assert [kern_writer.plain(record) for record in records] == EVERY_FORM[:2]
        assert kern_writer.render_block(records, "\r\n") == block

    def test_a_block_whose_line_endings_differ_from_the_file_s_is_refused(self):
        mixed = "kern:\r\n- {right: {family: qsTea}, by: -1}\n"
        with pytest.raises(kern_writer.BlockRefused, match="line ending"):
            kern_writer.parse_block(mixed, "f", "\r\n")
        with pytest.raises(kern_writer.BlockRefused, match="line ending"):
            kern_writer.parse_block("kern:\r\n- {right: {family: qsTea}, by: -1}\r\n", "f", "\n")


def test_the_writer_is_safe_across_threads():
    """The server reads blocks on its executor's threads (`GET /kern`'s refusals) while the writes' thread parses and renders them; every call gives the one answer it gives alone."""
    block = every_form_block()
    lines = [kern_writer.record_line(record) for record in kern_writer.parse_block(block, "f")]
    failures: list[str] = []

    def work() -> None:
        for _ in range(60):
            try:
                records = kern_writer.parse_block(block, "f")
                if (
                    kern_writer.render_block(records) != block
                    or [kern_writer.record_line(one) for one in records] != lines
                ):
                    failures.append("a different answer")
            except Exception as error:
                failures.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
