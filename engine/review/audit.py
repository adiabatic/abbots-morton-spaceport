"""The vocabulary the review corpus build, its checker and the corpus's readers share (engine/REVIEW-PLAN.md §2.1, §2.3): the batch size, the review app's sections (`class_section`), the machine checks (`machine_approved`), which units take no verdict (`needs_no_verdict`), and the order a corpus pages its human units in (`triage_key`)."""

from __future__ import annotations

from collections.abc import Mapping

BATCH_SIZE = 300

# The review app's sections, in the order the manifest lists their classes (`class_section`).
OPEN_QUESTIONS = "open-questions"
NO_REVIEW_NEEDED = "no-review-needed"
SECTIONS = (OPEN_QUESTIONS, NO_REVIEW_NEEDED)


def class_section(no_verdict: bool, human_units: int) -> str:
    """Return the review app's section for a class. A class that takes no verdict (`no_verdict`), or whose every unit a machine check approves (`human_units` is zero), needs no review; any other is an open question."""
    if no_verdict or human_units == 0:
        return NO_REVIEW_NEEDED
    return OPEN_QUESTIONS


MACHINE_CHECKS = ("ink_identical", "picture_identical")


def machine_approved(fragment) -> bool:
    """Whether a unit's JSON fragment has any machine-approval flag set. The build tries the checks in `MACHINE_CHECKS` order (picture identity only where ink identity fails), so at most one is true."""
    return any(fragment.get(check_id) is True for check_id in MACHINE_CHECKS)


def needs_no_verdict(fragment) -> bool:
    """Whether a unit's JSON fragment takes no verdict: it is machine-approved, or its class takes none (`no_verdict`). Such a unit is outside the manifest's triage index, so it is never paged to a human; `needsNoVerdict` in engine/review/static/render.js is the app's form of the rule."""
    return machine_approved(fragment) or fragment.get("no_verdict") is True


def parse_codepoints(codepoints: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in codepoints.split(":"))


def family_ranks(family_of: Mapping[int, str]) -> dict[str, int]:
    """Map each family name to its code point, the key a group's two families sort by."""
    return {name: value for value, name in family_of.items()}


def triage_key(
    class_index: int,
    group: str,
    codepoint_values: tuple[int, ...],
    unit_id: str,
    family_rank: Mapping[str, int],
) -> tuple:
    """The sort key for the order a corpus pages its human units in (the manifest's `human_unit_ids`): the class's index in the manifest's class list, the group's families by code point, the window's length and codepoints, and last the unit's id. The id breaks ties between sibling units of one window by content. Every term depends only on the unit and its class, so the same units sort the same way on every corpus. `build.check_shards` checks every manifest's index against this key."""
    return (
        class_index,
        tuple(family_rank.get(name, 10**6) for name in group.split(":")),
        len(codepoint_values),
        codepoint_values,
        unit_id,
    )
