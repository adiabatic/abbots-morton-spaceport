"""Reflow the data YAML to the project's flow-vs-block style rules.

Two policies live here (see AGENTS.md → YAML files → Formatting). `glyph_data/quikscript.yaml` keeps the width rule: a mapping or list stays inline (flow style) while the whole line fits within 100 columns; past that, the outermost flow collection on the line breaks into block style and the rule re-applies to the resulting lines. The break is at the *outermost* collection because YAML forbids a block collection nested inside a flow one — once any child must be block, its whole map goes block too.

The rune files under `glyph_data/runes/` use a structural rule instead: every collection is block style, except three leaf shapes that stay flow — an empty collection (`{}` or `[]`), a single-key mapping whose value is a scalar (`{family: qsDay_qsUtter}`), and a pair of numbers (`[1, 1]`). Width never enters the structural rule, so an edit can't flip a neighboring collection's style and diffs stay local to the changed values.

Shared invariants under both policies: a collection whose subtree carries comments stays block regardless (flow style can't hold per-item line comments, and inlining a `bitmap:` list would destroy its `#` row markers), and a long scalar (a `ductus`, a paragraph-length `why:`) can't be broken and is left over-width. The pass is idempotent: running it on already-reflowed YAML is a no-op. It round-trips through ruamel, so comments, block scalars, and quoting survive.

Usage::

    uv run python tools/reflow_yaml.py                       # quikscript.yaml + every glyph_data/runes/*.yaml
    uv run python tools/reflow_yaml.py glyph_data/runes/qsMay.yaml [more.yaml ...]
"""

import sys
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

MAX_WIDTH = 100

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 1 << 30  # never let the emitter line-wrap scalars
_yaml.indent(mapping=2, sequence=2, offset=0)

_flow = YAML()
_flow.default_flow_style = True
_flow.width = 1 << 30


def _strip_to_plain(node):
    if isinstance(node, CommentedMap):
        return {k: _strip_to_plain(v) for k, v in node.items()}
    if isinstance(node, CommentedSeq):
        return [_strip_to_plain(v) for v in node]
    return node


def _inline_len(node):
    buf = StringIO()
    _flow.dump(_strip_to_plain(node), buf)
    return len(buf.getvalue().rstrip("\n"))


def _has_comments(node):
    if isinstance(node, (CommentedMap, CommentedSeq)):
        ca = node.ca
        if ca.comment or ca.items:
            return True
        children = node.values() if isinstance(node, CommentedMap) else node
        return any(_has_comments(child) for child in children)
    return False


def _decide_by_width(node, start_col, anchor):
    """Set node's flow/block style under the width rule. anchor is the column of node's own key/dash; start_col is where its inline content would begin."""
    if not isinstance(node, (CommentedMap, CommentedSeq)):
        return
    if not _has_comments(node) and start_col + _inline_len(node) <= MAX_WIDTH:
        node.fa.set_flow_style()
        return
    node.fa.set_block_style()
    if isinstance(node, CommentedMap):
        child_anchor = anchor + 2
        for key, value in node.items():
            _decide_by_width(value, child_anchor + len(str(key)) + 2, child_anchor)
    else:
        for item in node:
            _decide_by_width(item, anchor + 2, anchor + 2)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _stays_flow(node):
    """Under the structural rule, only three comment-free leaf shapes stay flow: an empty collection, a single-key mapping with a single-line scalar value, and a pair of numbers. A multi-line value (a `ductus` block scalar) keeps its mapping block so the scalar keeps its `|` style."""
    if _has_comments(node):
        return False
    if len(node) == 0:
        return True
    if isinstance(node, CommentedMap):
        if len(node) != 1:
            return False
        (value,) = node.values()
        if isinstance(value, (CommentedMap, CommentedSeq)):
            return False
        return not (isinstance(value, str) and "\n" in value)
    return len(node) == 2 and all(_is_number(item) for item in node)


def _decide_structurally(node):
    if not isinstance(node, (CommentedMap, CommentedSeq)):
        return
    pairs = list(node.items()) if isinstance(node, CommentedMap) else list(enumerate(node))
    for key, child in pairs:
        if isinstance(child, str) and "\n" in child and not isinstance(child, LiteralScalarString):
            node[key] = LiteralScalarString(child)
    if _stays_flow(node):
        node.fa.set_flow_style()
        return
    node.fa.set_block_style()
    for _, child in pairs:
        _decide_structurally(child)


def reflow(path):
    """Reflow one YAML file in place. Returns True when the file's bytes changed."""
    before = path.read_text()
    data = _yaml.load(before)
    structural = path.resolve().parent.name == "runes"
    if isinstance(data, CommentedMap):
        data.fa.set_block_style()
        for key, value in data.items():
            if structural:
                _decide_structurally(value)
            else:
                _decide_by_width(value, len(str(key)) + 2, 0)
    buf = StringIO()
    _yaml.dump(data, buf)
    after = buf.getvalue()
    if after != before:
        path.write_text(after)
    return after != before


def default_targets():
    root = Path(__file__).resolve().parent.parent
    yield root / "glyph_data" / "quikscript.yaml"
    yield from sorted((root / "glyph_data" / "runes").glob("*.yaml"))


def main(argv):
    paths = [Path(arg) for arg in argv] if argv else list(default_targets())
    changed = [path for path in paths if path.exists() and reflow(path)]
    for path in changed:
        print(f"reflowed {path}")
    print(f"{len(changed)} of {len(paths)} file(s) changed")


if __name__ == "__main__":
    main(sys.argv[1:])
