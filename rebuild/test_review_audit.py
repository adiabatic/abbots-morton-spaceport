"""Tests for the review corpus's shared vocabulary on the Python side (rebuild/review/audit.py): the review app's sections, the machine checks and which units take no verdict, the codepoint-string parse, and the triage key."""

from rebuild.review.audit import (
    MACHINE_CHECKS,
    SECTIONS,
    class_section,
    family_ranks,
    machine_approved,
    needs_no_verdict,
    parse_codepoints,
    triage_key,
)
from rebuild.review.enrich import LETTERS


def test_parse_codepoints():
    """Pins the codepoint-string parsing: a text's code points as hex, joined by colons, in the form a fragment's `codepoints` writes."""
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


def test_class_section_puts_a_class_with_nothing_to_judge_under_no_review_needed():
    """A class that takes no verdict, or whose every unit a machine check approves, needs no review; any other class is an open question."""
    assert SECTIONS == ("open-questions", "no-review-needed")
    assert class_section(True, 5) == "no-review-needed"
    assert class_section(False, 0) == "no-review-needed"
    assert class_section(True, 0) == "no-review-needed"
    assert class_section(False, 1) == "open-questions"


def test_a_unit_takes_no_verdict_when_a_machine_check_approves_it_or_its_class_takes_none():
    """`machine_approved` reads the flags in `MACHINE_CHECKS`, and `needs_no_verdict` adds the class's `no_verdict`. A flag must be `True` itself: a missing flag, as on an app-index row, reads as not set."""
    assert MACHINE_CHECKS == ("ink_identical", "picture_identical")
    for check in MACHINE_CHECKS:
        assert machine_approved({check: True}) and needs_no_verdict({check: True})
        assert not machine_approved({check: False}) and not needs_no_verdict({check: False})
    assert not machine_approved({"no_verdict": True}) and needs_no_verdict({"no_verdict": True})
    assert not needs_no_verdict({}) and not needs_no_verdict({"ink_identical": 1})
