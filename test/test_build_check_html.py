"""Tests for the failing-tests section of `tools/build_check_html.py`: which failure messages yield a rendered preview."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_check_html as check_html  # noqa: E402


@pytest.mark.parametrize(
    ("message", "expected_text"),
    [
        (
            "qsPea -> qsTea at y=0 (baseline): non-integer-pixel gap (12.5 units; context ·qsPea·qsPea·qsTea·qsTea)",
            "\ue650\ue650\ue652\ue652",
        ),
        (
            "qsPea -> qsTea at y=0 (baseline): gap=1px (context ·space·qsPea·qsTea·qsTea)",
            " \ue650\ue652\ue652",
        ),
        ("[space] / qsPea / qsTea / [∅]: bad", " \ue650\ue652"),
    ],
    ids=["non-integer-gap", "space-in-context", "space-in-label-bracket"],
)
def test_failure_row_text_covers_every_message_shape(message: str, expected_text: str) -> None:
    [row] = check_html.build_failure_rows([check_html.TestFailure(nodeid="test_x", sub_messages=[message])])
    assert row.text == expected_text


def test_failure_row_labels_a_space_as_open_box() -> None:
    row = check_html.FailureRow(nodeid="test_x", message="m", families=("space", "qsPea"), text=" \ue650")
    assert '<div class="sequence-label">␣ ·Pea</div>' in check_html._format_failure_row(
        row, check_html._codepoint_to_family()
    )
