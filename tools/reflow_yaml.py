"""Reflow the rune files (`glyph_data/runes/*.yaml`) to the project's flow-vs-block style rule.

Every collection is block style except three leaf shapes that stay flow: an empty collection (`{}` or `[]`), a single-key mapping whose value is a single-line scalar (`{family: qsDay_qsUtter}`), and a pair of numbers (`[1, 1]`). Width plays no part, so an edit cannot change a neighboring collection's style, and diffs stay limited to the changed values. The pass also rewrites every multi-line string that is not already a `|` block scalar as one. A rune's top-level `kern:` list is the one exception: it stays a block list with each record in flow style on its own line (`- {stance: flipped, right: {family: qsTea, stance: half}, by: -1}`), the form the kern editor writes.

A collection with a comment anywhere in its subtree stays block, because flow style cannot hold per-item comments and inlining a `bitmap:` list would drop its `#` row markers. A long scalar (a `ductus`, a paragraph-length `why:`) is never wrapped. Running the pass on already-reflowed YAML changes nothing. The file is round-tripped through ruamel, so comments, block scalars, and quoting are kept. The pass refuses a file outside a directory named `runes`, since no other YAML is kept in this form.

Usage::

    uv run python tools/reflow_yaml.py                       # every glyph_data/runes/*.yaml
    uv run python tools/reflow_yaml.py glyph_data/runes/qsMay.yaml [more.yaml ...]
"""

import sys
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 1 << 30  # never let the emitter line-wrap scalars
_yaml.indent(mapping=2, sequence=2, offset=0)


def _has_comments(node):
    if isinstance(node, (CommentedMap, CommentedSeq)):
        ca = node.ca
        if ca.comment or ca.items:
            return True
        children = node.values() if isinstance(node, CommentedMap) else node
        return any(_has_comments(child) for child in children)
    return False


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _stays_flow(node):
    """Return whether `node` is one of the structural rule's three comment-free flow shapes: an empty collection, a single-key mapping with a single-line scalar value, or a pair of numbers. A mapping with a multi-line value (a `ductus` block scalar) stays block so the scalar keeps its `|` style."""
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


def _multiline(node):
    if isinstance(node, CommentedMap):
        return any(_multiline(value) for value in node.values())
    if isinstance(node, CommentedSeq):
        return any(_multiline(item) for item in node)
    return isinstance(node, str) and "\n" in node


def _decide_kern(node):
    """Set a rune's `kern:` block to one flow-style record per line, the form the kern editor writes (engine/KERN-PLAN.md section 6). A record with a comment or a multi-line string falls back to the structural rule."""
    if not isinstance(node, CommentedSeq):
        _decide_structurally(node)
        return
    node.fa.set_block_style()
    for item in node:
        if isinstance(item, CommentedMap) and not _has_comments(item) and not _multiline(item):
            item.fa.set_flow_style()
        else:
            _decide_structurally(item)


def reflow(path):
    """Reflow one rune file in place. Returns True when the file's bytes changed, and raises ValueError for a file outside a directory named `runes`."""
    if path.resolve().parent.name != "runes":
        raise ValueError(
            f"{path} is not a rune file; only the files under glyph_data/runes/ are kept reflowed"
        )
    before = path.read_text()
    data = _yaml.load(before)
    if isinstance(data, CommentedMap):
        data.fa.set_block_style()
        for key, value in data.items():
            if key == "kern":
                _decide_kern(value)
            else:
                _decide_structurally(value)
    buf = StringIO()
    _yaml.dump(data, buf)
    after = buf.getvalue()
    if after != before:
        path.write_text(after)
    return after != before


def default_targets():
    root = Path(__file__).resolve().parent.parent
    yield from sorted((root / "glyph_data" / "runes").glob("*.yaml"))


def main(argv):
    paths = [Path(arg) for arg in argv] if argv else list(default_targets())
    changed = [path for path in paths if path.exists() and reflow(path)]
    for path in changed:
        print(f"reflowed {path}")
    print(f"{len(changed)} of {len(paths)} file(s) changed")


if __name__ == "__main__":
    main(sys.argv[1:])
