"""Tests for the change groups' prose file (rebuild/review/change-groups.yaml, `change_groups.load_change_group_prose`): it names every group of `CHANGE_GROUP_ORDER`, in that order, each with a title, a summary and a why, and a file in another order is refused.

The review crate assigns the groups. The review facts report how many windows each group holds, which the artifact cycle diffs into rebuild/review-facts-pins.json.
"""

import pytest

from rebuild.review.change_groups import CHANGE_GROUP_ORDER, load_change_group_prose


def test_the_prose_file_names_every_group_in_order(tmp_path):
    prose = load_change_group_prose()
    assert tuple(prose) == CHANGE_GROUP_ORDER
    assert all(entry.title and entry.summary and entry.why for entry in prose.values())
    shortened = tmp_path / "change-groups.yaml"
    shortened.write_text("- {id: join-dropped, title: T, summary: S, why: W}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CHANGE_GROUP_ORDER"):
        load_change_group_prose(shortened)
