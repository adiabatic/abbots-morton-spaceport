"""Reflow the data YAML to the project's flow-vs-block width rule.

The rule (see AGENTS.md → YAML files → Formatting): keep a mapping or list inline (flow style) as long as the whole line stays within 100 columns; when it doesn't, break the outermost flow collection on that line into block style and re-apply the rule to the resulting lines.

Why the break is at the *outermost* collection: YAML forbids a block collection nested inside a flow one, so once any child must be block, its whole map goes block too. Two things the pass deliberately leaves alone: a collection whose subtree carries comments stays block regardless of width (flow style can't hold per-item line comments, and inlining a `bitmap:` list would destroy its `#` row markers), and a long scalar (a `ductus`, a paragraph-length `why:`) can't be broken and is left over-width.

The pass is idempotent: running it on already-reflowed YAML is a no-op. It round-trips through ruamel, so comments, block scalars, and quoting survive.

Usage::

    uv run python tools/reflow_yaml.py                       # quikscript.yaml + every glyph_data/runes/*.yaml
    uv run python tools/reflow_yaml.py glyph_data/runes/qsMay.yaml [more.yaml ...]
"""

import sys
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

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


def _decide(node, start_col, anchor):
    """Set node's flow/block style. anchor is the column of node's own key/dash; start_col is where its inline content would begin."""
    if not isinstance(node, (CommentedMap, CommentedSeq)):
        return
    if not _has_comments(node) and start_col + _inline_len(node) <= MAX_WIDTH:
        node.fa.set_flow_style()
        return
    node.fa.set_block_style()
    if isinstance(node, CommentedMap):
        child_anchor = anchor + 2
        for key, value in node.items():
            _decide(value, child_anchor + len(str(key)) + 2, child_anchor)
    else:
        for item in node:
            _decide(item, anchor + 2, anchor + 2)


def reflow(path):
    """Reflow one YAML file in place. Returns True when the file's bytes changed."""
    before = path.read_text()
    data = _yaml.load(before)
    if isinstance(data, CommentedMap):
        data.fa.set_block_style()
        for key, value in data.items():
            _decide(value, len(str(key)) + 2, 0)
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
