"""Tests for rebuild.tools.verdict_notes.cap_markers: the note-collapse the carry/echo producers and the collapse_verdict_notes backfill apply to keep only the newest few provenance markers while preserving human prose."""

from rebuild.tools.verdict_notes import cap_markers

CARRIED_A = "[carried u-1@review-pre-aaa, verdicted 2026-07-18]"
CARRIED_B = "[carried u-2@review-pre-bbb, verdicted 2026-07-17]"
CARRIED_C = "[carried u-3@review-pre-ccc, verdicted 2026-07-16]"
ECHO = "[echo-fill from u-9]"


def test_keeps_the_two_newest_markers_of_any_kind():
    note = f"{CARRIED_A} {CARRIED_B} {ECHO} {CARRIED_C}"
    assert cap_markers(note) == f"{CARRIED_A} {CARRIED_B}"


def test_drops_an_interior_echo_fill_behind_two_carries():
    note = f"{CARRIED_A} {CARRIED_B} {ECHO}"
    assert cap_markers(note) == f"{CARRIED_A} {CARRIED_B}"


def test_keeps_an_echo_fill_when_it_is_among_the_two_newest():
    note = f"{ECHO} {CARRIED_A} {CARRIED_B}"
    assert cap_markers(note) == f"{ECHO} {CARRIED_A}"


def test_preserves_trailing_human_prose():
    note = f"{CARRIED_A} {CARRIED_B} {CARRIED_C} the old way is nicer to write by hand"
    assert cap_markers(note) == f"{CARRIED_A} {CARRIED_B} the old way is nicer to write by hand"


def test_a_two_marker_note_is_unchanged_and_idempotent():
    note = f"{CARRIED_A} {CARRIED_B} I prefer M1."
    once = cap_markers(note)
    assert once == note
    assert cap_markers(once) == once


def test_a_marker_less_note_is_untouched():
    assert cap_markers("I prefer M1.") == "I prefer M1."
    assert cap_markers("") == ""


def test_a_human_note_starting_with_a_non_marker_bracket_is_not_eaten():
    note = "[see attached] my thoughts on the join"
    assert cap_markers(note) == note


def test_a_pure_marker_note_collapses_to_its_two_newest():
    note = f"{CARRIED_A} {CARRIED_B} {CARRIED_C}"
    assert cap_markers(note) == f"{CARRIED_A} {CARRIED_B}"


def test_recognizes_echo_harmonize_and_bulk_markers():
    harmonize = "[echo-harmonize e-1007 — docket 2026-07-18T00:00:00Z]"
    bulk = "[bulk: qsNo baseline — docket 2026-07-18T00:00:00Z]"
    note = f"{harmonize} {bulk} {CARRIED_A} keep me"
    assert cap_markers(note) == f"{harmonize} {bulk} keep me"


def test_keep_parameter_is_honored():
    note = f"{CARRIED_A} {CARRIED_B} {CARRIED_C} prose"
    assert cap_markers(note, keep=1) == f"{CARRIED_A} prose"
    assert cap_markers(note, keep=3) == note
