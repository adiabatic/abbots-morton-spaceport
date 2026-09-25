from pathlib import Path

import pytest

from reflow_yaml import MAX_WIDTH, reflow


def _list_item_mapping(width):
    prefix = "    kk: {a: "
    line = prefix + "x" * (width - len(prefix) - 1) + "}"
    return f"top:\n  lst:\n  - name: x\n{line}\n    # c\n"


def _list_in_a_list(width):
    prefix = "    - [a, "
    line = prefix + "x" * (width - len(prefix) - 1) + "]"
    return f"top:\n  lst:\n  - - a\n{line}\n"


@pytest.mark.parametrize("shape", [_list_item_mapping, _list_in_a_list])
@pytest.mark.parametrize("width", [MAX_WIDTH, MAX_WIDTH + 1])
def test_flow_line_under_a_list_breaks_only_past_max_width(tmp_path: Path, shape, width):
    text = shape(width)
    assert max(len(line) for line in text.splitlines()) == width
    path = tmp_path / "data.yaml"
    path.write_text(text)
    assert reflow(path) == (width > MAX_WIDTH)
    after = path.read_text()
    assert max(len(line) for line in after.splitlines()) <= MAX_WIDTH
    if width <= MAX_WIDTH:
        assert after == text
