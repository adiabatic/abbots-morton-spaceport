"""Tests for the review corpus's audit and ledger vocabulary on the Python side (rebuild/review/audit.py): loading and refusing divergence ledgers, the review app's sections, the codepoint-string parse, the triage key, and the audit held open through its descriptors (`AuditParts`).

None of it reads the live audit. The live counts change with every migrated letter, so they are not asserted here. The corpus build's review facts report them, and the artifact cycle diffs them into rebuild/review-facts-pins.json.
"""

import os
from pathlib import Path

import pytest
import yaml

from rebuild.pipeline import oracle, row_delta
from rebuild.review import change_groups
from rebuild.review.audit import (
    AUDIT_HEADER,
    LEDGER_STATUSES,
    RULED_STATUSES,
    SECTIONS,
    AuditParts,
    class_section,
    family_ranks,
    load_ledger,
    parse_codepoints,
    triage_key,
)
from rebuild.review.enrich import LETTERS

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_parse_codepoints():
    """Pins the codepoint-string parsing: a window string is its code points as hex, joined by colons, in the form the audit's `codepoints` field and a fragment's `codepoints` write."""
    assert parse_codepoints("200C:E652:E679") == (0x200C, 0xE652, 0xE679)
    assert parse_codepoints("E650") == (0xE650,)


def test_the_triage_key_orders_by_class_then_group_then_window_then_id():
    """`triage_key` sorts the human units a corpus pages: the class's index first, then the group's families by code point (`family_ranks`), a family outside the alphabet after every family in it, then the window's length and code points, and last the unit's id, which breaks a tie between sibling units of one window by content."""
    rank = family_ranks(LETTERS)
    keys = [
        triage_key(1, "qsPea:qsMay", (0xE650, 0xE665), "u-1", rank),
        triage_key(0, "qsTea:qsIt", (0xE652, 0xE670), "u-2", rank),
        triage_key(0, "qsPea:qsMay", (0xE650, 0xE665, 0xE667), "u-3", rank),
        triage_key(0, "qsPea:qsMay", (0xE650, 0xE665), "u-5", rank),
        triage_key(0, "qsPea:qsMay", (0xE650, 0xE665), "u-4", rank),
        triage_key(0, "qsPea:qsUnknown", (0xE650,), "u-6", rank),
    ]
    assert [key[-1] for key in sorted(keys)] == ["u-4", "u-5", "u-3", "u-6", "u-2", "u-1"]


def _entry(identifier: str) -> str:
    return (
        f"- id: {identifier}\n  title: The {identifier} title\n  summary: The {identifier} summary.\n"
        "  status: by-design\n  match: {predicate: boundary_window, configs: all}\n  why: because\n"
    )


def _ledger(tmp_path, *ids: str, text: str = "") -> Path:
    path = tmp_path / "ledger.yaml"
    path.write_text("".join(_entry(identifier) for identifier in ids) + text, encoding="utf-8")
    return path


def test_a_ledger_declaring_one_class_twice_is_refused_at_load(tmp_path):
    """Code downstream indexes the ledger by id, so a repeated id would make one entry silently replace the other. `load_ledger` raises on it."""
    with pytest.raises(ValueError, match="longer-entry-join"):
        load_ledger(
            _ledger(
                tmp_path,
                "identical-unused-join-name",
                "longer-entry-join",
                "longer-entry-join",
            )
        )


@pytest.mark.parametrize("identifier", ["UNMATCHED", change_groups.CHANGE_GROUP_ORDER[0]])
def test_a_ledger_claiming_a_synthesized_class_is_refused_at_load(tmp_path, identifier):
    """The build creates the UNMATCHED catch-all and the change-group classes itself, so a ledger entry with one of those ids would be overridden by a class the ledger does not describe."""
    with pytest.raises(ValueError, match=identifier):
        load_ledger(_ledger(tmp_path, identifier))


def test_a_ledger_entry_carries_its_copy_and_an_optional_note(tmp_path):
    """`load_ledger` copies each entry's title, summary and note, stripped, beside its why, and an entry without a note gets an empty one."""
    noted = _entry("noted").replace(
        "  why: because\n", '  note: "Look at the join into ·May."\n  why: because\n'
    )
    plain, with_note = load_ledger(_ledger(tmp_path, "plain", text=noted))
    assert (plain.title, plain.summary, plain.note, plain.why) == (
        "The plain title",
        "The plain summary.",
        "",
        "because",
    )
    assert (with_note.status, with_note.note) == ("by-design", "Look at the join into ·May.")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("status: by-design", "status: intended", "status 'intended'"),
        ("  status: by-design\n", "", "status None"),
        ("  title: The bad title\n", "", "nonempty string title"),
        ("summary: The bad summary.", "summary: ' '", "nonempty string summary"),
        ("why: because", 'note: ""\n  why: because', "nonempty string note"),
    ],
)
def test_a_ledger_entry_the_review_app_cannot_show_is_refused(tmp_path, old, new, message):
    """Every entry needs a status from `LEDGER_STATUSES`, which decides its section, and a nonempty title and summary, which the sidebar shows; a note, when present, must say something."""
    text = _entry("bad").replace(old, new)
    assert text != _entry("bad")
    with pytest.raises(ValueError, match=message):
        load_ledger(_ledger(tmp_path, "good", text=text))


def test_class_section_puts_exemption_and_machine_approval_ahead_of_the_status():
    """A no-verdict class, or one whose every unit a machine check approves, needs no review whatever its status. Otherwise a ruled status makes a spot-check and `per-unit`, or a table-diff bucket's null status, an open question. A status outside the vocabulary raises rather than landing in a section by default."""
    assert SECTIONS == ("open-questions", "spot-check", "no-review-needed")
    assert set(RULED_STATUSES) < set(LEDGER_STATUSES)
    for status in (*LEDGER_STATUSES, None):
        assert class_section(status, True, 5) == "no-review-needed"
        assert class_section(status, False, 0) == "no-review-needed"
    assert class_section("by-design", False, 1) == "spot-check"
    assert class_section("approved", False, 1) == "spot-check"
    assert class_section("per-unit", False, 1) == "open-questions"
    assert class_section(None, False, 1) == "open-questions"
    with pytest.raises(ValueError, match="reviewed-approved"):
        class_section("reviewed-approved", False, 1)


def test_the_live_ledger_loads_one_class_per_entry():
    """The checked-in ledger passes those checks, and every entry in the file loads as its own class."""
    path = REPO_ROOT / "rebuild" / "m1-divergences.yaml"
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    classes = load_ledger(path)
    assert [entry["id"] for entry in entries] == [entry.id for entry in classes]


def test_a_held_descriptor_keeps_reading_a_part_a_later_commit_deleted(tmp_path):
    """The build opens every part its head lists as it reads the head and reads them through those descriptors alone (`AuditParts`), so a commit during the build that deletes them, as a fold does, cannot reach what the build reads: the held base still reads as the head it read describes it, its header line and its rows."""
    header = "\t".join(AUDIT_HEADER)
    rows = [f"default\tE65{digit}\tcell\tclass-a\tqsPea\tqsPea/g" for digit in "0123"]
    out = tmp_path / "m1"
    with oracle.AuditWriter(out) as writer:
        writer.feed(("\n".join(rows) + "\n").encode("utf-8"))
    parts = AuditParts(out / oracle.AUDIT_NAME)
    try:
        assert parts.head is not None and len(parts.descriptors) == 1
        assert (parts.members, parts.compressed, parts.segment_rows) == ((None,), 0, 0)
        base = parts.head.base
        for name in [row_delta.head_name(oracle.AUDIT_NAME), base.name]:
            (out / name).unlink()
        held = os.pread(parts.descriptors[0], base.size, 0)
    finally:
        parts.close()
    assert held.decode("utf-8").splitlines() == [header, *rows]
    assert header == oracle.ORACLE_AUDIT_HEADER
