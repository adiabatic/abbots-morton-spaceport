"""Find sequences where a non-joining pair leaks shape across the break.

Enumerates short sequences of plain Quikscript letters, shapes each through
HarfBuzz, and looks for adjacent pairs that:
  1. Do *not* cursive-attach (no shared exit/entry Y), and
  2. Choose a different glyph variant when the pair is shaped together than
     when each side is shaped alone (a backward/forward `before:` / `after:`
     selector reaching across the literal non-join).

A leak detected this way is exactly the situation where ``data-expect`` has
to be relaxed from ``|`` to ``|?|`` (see ``test/data-expect.md`` and the
break-isolation invariant in ``test/test_shaping.py``).

Output is one block per unique leak, in the format consumed by
``test/check.html``::

    <div class="row">
      <div class="label">
        ·Key ·Thaw  (qsKey | qsThaw.after-tall vs qsThaw)
        <code>U+E654 U+E656</code>
      </div>
      <div class="qs before">…</div>
      <div class="qs after">…</div>
    </div>

Each unique leak is represented by the shortest sequence that exhibits it,
so families that only leak in three-letter context (e.g., ligature
post-processing) still surface even though pure pair shaping would miss
them.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "test"
if str(TEST) not in sys.path:
    sys.path.insert(0, str(TEST))

import uharfbuzz as hb  # noqa: E402

from quikscript_shaping_helpers import (  # noqa: E402
    _char_map,
    _compiled_meta,
    _font,
    _pair_join_ys,
    _plain_quikscript_letters,
    _qs_text,
    _shape_qs,
)


def _visual_signature(name: str) -> tuple:
    """Pixel-rendering signature for one glyph: (bitmap, y_offset, advance_width)."""
    g = _compiled_meta()[name]
    return (tuple(g.bitmap), g.y_offset, g.advance_width)


def _abs_render_signature(parts: tuple[str, ...]) -> tuple[list[tuple], int, int]:
    """Shape *parts* and return per-glyph render signatures plus the
    sequence's total advance.

    Each entry is ``(visual_signature, abs_x, abs_y)``: the glyph's pixel
    pattern plus its absolute origin in font units (``cumulative x_advance
    + this glyph's x_offset``). This captures both glyph identity *and*
    GPOS positioning (cursive attachment, kerning), so two arrangements
    that pick different glyphs but land them at the same spots — or pick
    the same-name glyph but cursively shift its neighbor — are
    distinguishable. The trailing total advance lets the caller stitch
    two independent shapings into a single coordinate space, mirroring
    how the two ``display: inline-block`` halves butt up against each
    other in the rendered HTML.
    """
    font = _font()
    buf = hb.Buffer()
    buf.add_str(_qs_text(*parts))
    buf.guess_segment_properties()
    hb.shape(font, buf)
    pen_x = 0
    pen_y = 0
    sigs: list[tuple] = []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = font.glyph_to_string(info.codepoint)
        sigs.append((_visual_signature(name), pen_x + pos.x_offset, pen_y + pos.y_offset))
        pen_x += pos.x_advance
        pen_y += pos.y_advance
    return sigs, pen_x, pen_y


def _visual_status(witness: "Witness") -> str:
    """Classify a leak as visually ``same`` or ``diff`` by comparing the
    in-context render of the witness sequence to the concatenation of
    its two independently-shaped halves.

    Looking at glyph metadata alone misses cursive-positioning leaks —
    e.g., ``qsIt`` vs ``qsIt.exit-xheight`` have identical bitmaps but
    the latter's exit anchor pulls the next glyph leftward via GPOS
    ``curs``, visibly bunching the in-context render against the halves.
    """
    full_sigs, _, _ = _abs_render_signature(witness.families)
    (l0, l1), (r0, r1) = _shaped_input_spans(witness)
    left_sigs, left_x, left_y = _abs_render_signature(witness.families[l0:l1])
    right_sigs, _, _ = _abs_render_signature(witness.families[r0:r1])
    halves_sigs = left_sigs + [
        (sig, x + left_x, y + left_y) for sig, x, y in right_sigs
    ]
    return "same" if full_sigs == halves_sigs else "diff"


@dataclass(frozen=True)
class Leak:
    left_chosen: str
    right_chosen: str
    left_iso: str
    right_iso: str

    @property
    def left_changed(self) -> bool:
        return self.left_chosen != self.left_iso

    @property
    def right_changed(self) -> bool:
        return self.right_chosen != self.right_iso


def _is_letter_glyph(name: str) -> bool:
    """Whether *name* is a Quikscript letter glyph eligible for the
    isolation check. Mirrors ``_is_qs_letter`` in ``test/test_shaping.py``:
    ligatures (``qsX_qsY[.variant]``) count, the angled-parenthesis glyphs
    do not."""
    if not name.startswith("qs"):
        return False
    base = name.split(".", 1)[0]
    return base not in {"qsAngleParenLeft", "qsAngleParenRight"}


def _base_family(name: str) -> str:
    return name.split(".", 1)[0]


def _input_spans(full: list[str]) -> list[tuple[int, int]] | None:
    """Map each output glyph to its [start, end) input-family slice."""
    meta = _compiled_meta()
    consumed = 0
    spans: list[tuple[int, int]] = []
    for g in full:
        g_meta = meta.get(g)
        seq_len = len(g_meta.sequence) if g_meta and g_meta.sequence else 1
        spans.append((consumed, consumed + seq_len))
        consumed += seq_len
    return spans


def _scan_sequence(families: tuple[str, ...]) -> list[tuple[int, Leak]]:
    """Return (break_index, Leak) pairs for every leaky non-join in *families*.

    Mirrors the break-isolation check in ``test/test_shaping.py``: at each
    candidate break, re-shape the prefix and suffix as independent
    HarfBuzz buffers and compare the glyphs flanking the break against
    their counterparts in the full shaping.
    """
    full = _shape_qs(*families)
    if len(full) < 2:
        return []
    spans = _input_spans(full)
    if spans is None or spans[-1][1] != len(families):
        return []
    results: list[tuple[int, Leak]] = []
    for i in range(len(full) - 1):
        left = full[i]
        right = full[i + 1]
        if not (_is_letter_glyph(left) and _is_letter_glyph(right)):
            continue
        if _pair_join_ys(full, i):
            continue
        l_end = spans[i][1]
        r_start = spans[i + 1][0]
        if l_end != r_start:
            continue
        left_shaped = _shape_qs(*families[:l_end])
        right_shaped = _shape_qs(*families[r_start:])
        if not left_shaped or not right_shaped:
            continue
        split_left = left_shaped[-1]
        split_right = right_shaped[0]
        if left == split_left and right == split_right:
            continue
        results.append(
            (
                i,
                Leak(
                    left_chosen=left,
                    right_chosen=right,
                    left_iso=split_left,
                    right_iso=split_right,
                ),
            ),
        )
    return results


@dataclass(frozen=True)
class Witness:
    families: tuple[str, ...]
    break_index: int  # output-glyph position of the leaky break


def find_leaks(max_len: int) -> dict[Leak, Witness]:
    """Enumerate sequences up to *max_len* and collect unique leaks.

    Returns a mapping from each unique :class:`Leak` to a :class:`Witness`
    holding the shortest sequence that exhibits it plus the output-glyph
    index of the leaky break within that sequence.
    """
    letters = [name for name, _ in _plain_quikscript_letters()]
    leaks: dict[Leak, Witness] = {}
    for length in range(2, max_len + 1):
        for families in itertools.product(letters, repeat=length):
            for break_i, leak in _scan_sequence(families):
                if leak not in leaks:
                    leaks[leak] = Witness(families=families, break_index=break_i)
    return leaks


def _family_to_codepoint() -> dict[str, int]:
    chars = _char_map()
    return {name: ord(chars[name]) for name in chars if name.startswith("qs") and "_" not in name}


def _short_label(family: str) -> str:
    """``qsRoe`` -> ``·Roe``; ``qsIng`` -> ``·-ing`` (matches data-expect style)."""
    if family == "qsIng":
        return "·-ing"
    bare = family[2:]
    return "·" + bare


def _entity_for(codepoint: int) -> str:
    return f"&#x{codepoint:X};"


def _format_label(leak: Leak, witness: Witness) -> tuple[str, str]:
    cp_map = _family_to_codepoint()
    families = witness.families
    label_parts = list(_short_label(f) for f in families)
    # Visually mark the leaky break with `|` between the two affected names.
    label_parts.insert(witness.break_index + 1, "|")
    label = " ".join(label_parts)
    diff_parts = []
    if leak.left_changed:
        diff_parts.append(f"{leak.left_iso} → {leak.left_chosen}")
    if leak.right_changed:
        diff_parts.append(f"{leak.right_iso} → {leak.right_chosen}")
    diff = "; ".join(diff_parts)
    code = " ".join(f"U+{cp_map[f]:04X}" for f in families)
    return f"{label} ({diff})", code


def _shaped_input_spans(witness: Witness) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return ((left_start, left_end), (right_start, right_end)) input ranges.

    The break splits the input families at the boundary between the output
    glyph at ``witness.break_index`` and the next one. Left covers
    ``families[:left_end]``, right covers ``families[right_start:]``.
    """
    full = _shape_qs(*witness.families)
    spans = _input_spans(full)
    if spans is None:
        raise RuntimeError(f"could not map spans for {witness.families!r}")
    l_end = spans[witness.break_index][1]
    r_start = spans[witness.break_index + 1][0]
    return (0, l_end), (r_start, len(witness.families))


def _format_row(leak: Leak, witness: Witness) -> str:
    label, code = _format_label(leak, witness)
    cp_map = _family_to_codepoint()
    families = witness.families
    (l0, l1), (r0, r1) = _shaped_input_spans(witness)
    full_entities = "".join(_entity_for(cp_map[f]) for f in families)
    left_entities = "".join(_entity_for(cp_map[f]) for f in families[l0:l1])
    right_entities = "".join(_entity_for(cp_map[f]) for f in families[r0:r1])
    isolated = (
        f'<span class="half">{left_entities}</span>'
        f'<span class="half">{right_entities}</span>'
    )
    visual = _visual_status(witness)
    return (
        f'      <div class="row" data-visual="{visual}">\n'
        '        <div class="label">\n'
        f'          <span class="visual-tag">{visual}</span>{label}\n'
        f"          <code>{code}</code>\n"
        "        </div>\n"
        f'        <div class="qs in-context">{full_entities}</div>\n'
        f'        <div class="qs isolated">{isolated}</div>\n'
        "      </div>"
    )


def _sort_key(item: tuple[Leak, Witness]) -> tuple:
    leak, witness = item
    cp_map = _family_to_codepoint()
    return (
        len(witness.families),
        tuple(cp_map[f] for f in witness.families),
        leak.left_chosen,
        leak.right_chosen,
    )


SECTION_BEGIN = "<!-- BEGIN AUTO: isolation-leaks -->"
SECTION_END = "<!-- END AUTO: isolation-leaks -->"


def _build_section(items: list[tuple[Leak, Witness]], max_len: int) -> str:
    rows = "\n".join(_format_row(leak, witness) for leak, witness in items)
    return (
        f"{SECTION_BEGIN}\n"
        '    <section class="isolation-leaks">\n'
        "      <h2>Auto-generated: isolation leaks</h2>\n"
        "      <p>\n"
        "        Sequences whose adjacent pair does not cursive-attach but\n"
        "        whose chosen glyphs differ when the pair is shaped together\n"
        "        versus split into independent buffers. These are the\n"
        "        cases that currently require <code>|?|</code> in\n"
        "        <code>data-expect</code>; visually inspect each row to\n"
        "        decide whether the cross-break shape change is cosmetic\n"
        f"        or a real bug. Generated by <code>tools/find_isolation_leaks.py --max-len {max_len}</code>.\n"
        "      </p>\n"
        "      <p>\n"
        "        Columns: the middle column shapes the whole sequence as a\n"
        "        single buffer (what you get in real text); the right\n"
        "        column splits the sequence at the leaky break into two\n"
        "        <code>display: inline-block</code> halves so HarfBuzz\n"
        "        shapes each side independently. If the two columns look\n"
        "        identical, the leak is purely a glyph-name signature\n"
        "        change with no visible effect; if they differ, decide\n"
        "        whether the in-context shape is intended.\n"
        "      </p>\n"
        "      <p>\n"
        "        Each row is tagged <code>same</code> or <code>diff</code>\n"
        "        based on whether the in-context buffer and the\n"
        "        concatenation of the two independently-shaped halves\n"
        "        produce the same per-glyph (pixels, absolute origin)\n"
        "        sequence. Comparing absolute origins (not just pixels)\n"
        "        catches cursive-positioning leaks: e.g.\n"
        "        <code>qsIt</code> vs <code>qsIt.exit-xheight</code> have\n"
        "        identical bitmaps but the latter's exit anchor pulls the\n"
        "        next glyph leftward via GPOS <code>curs</code>. Reach for\n"
        "        the <code>diff</code> rows first when hunting real bugs;\n"
        "        <code>same</code> rows are typically\n"
        "        <code>after-tall</code>-style trims whose only effect is\n"
        "        the glyph-name signature.\n"
        "      </p>\n"
        '      <div class="col-headers">\n'
        "        <span>Sequence</span>\n"
        "        <span>In context</span>\n"
        "        <span>Halves shaped separately</span>\n"
        "      </div>\n"
        f"{rows}\n"
        "    </section>\n"
        f"    {SECTION_END}"
    )


def _first_time_section(items: list[tuple[Leak, Witness]], max_len: int) -> str:
    """Section text including a leading 4-space indent for the first insert."""
    return "    " + _build_section(items, max_len)


CSS_BLOCK = """
      .isolation-leaks .qs.in-context,
      .isolation-leaks .qs.isolated {
        font-family: var(--after-font);
        --grid-color: light-dark(rgba(0, 0, 0, 0.05), rgba(255, 255, 255, 0.06));
        background-image:
          linear-gradient(45deg, var(--grid-color) 25%, transparent 25%, transparent 75%, var(--grid-color) 75%),
          linear-gradient(45deg, var(--grid-color) 25%, transparent 25%, transparent 75%, var(--grid-color) 75%);
        background-size: 16px 16px;
        background-position: 0 5.6px, 8px 13.6px;
      }

      .isolation-leaks .qs.isolated .half {
        display: inline-block;
      }

      .isolation-leaks .row .visual-tag {
        display: inline-block;
        margin-right: .5em;
        padding: 0 .4em;
        border-radius: 3px;
        font-family: Menlo, Consolas, monospace;
        font-size: 11px;
        text-transform: uppercase;
        vertical-align: 1px;
      }

      .isolation-leaks .row[data-visual="diff"] .visual-tag {
        background: light-dark(#ffe0a8, #5a3a00);
        color: light-dark(#5a3a00, #ffe0a8);
      }

      .isolation-leaks .row[data-visual="same"] .visual-tag {
        background: light-dark(#e0e0e0, #444);
        color: light-dark(#666, #aaa);
      }
"""


def _ensure_css(check_html: str) -> str:
    if ".isolation-leaks" in check_html:
        return check_html
    sentinel = "      .footer {"
    if sentinel not in check_html:
        return check_html
    return check_html.replace(sentinel, CSS_BLOCK.lstrip("\n") + "\n" + sentinel, 1)


def _splice_section(
    check_html: str,
    items: list[tuple[Leak, Witness]],
    max_len: int,
) -> str:
    if SECTION_BEGIN in check_html and SECTION_END in check_html:
        # Re-splice between the existing markers, preserving whatever
        # whitespace already sits in front of BEGIN.
        before, _, rest = check_html.partition(SECTION_BEGIN)
        _, _, after = rest.partition(SECTION_END)
        return before + _build_section(items, max_len) + after
    # First-time insert: anchor on `<p class="footer">` so the section
    # lands after any existing scratch content.
    anchor = '    <p class="footer">'
    if anchor not in check_html:
        raise RuntimeError("could not find footer anchor in check.html")
    return check_html.replace(
        anchor,
        _first_time_section(items, max_len) + "\n\n" + anchor,
        1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-len",
        type=int,
        default=3,
        help=(
            "Maximum sequence length to enumerate (default 3 — covers every "
            "pair plus single-letter context on either side, which catches "
            "context-revealed leaks without combinatorial blowup). "
            "Increase to 4 for a slower (~30 s) but deeper sweep."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Update test/check.html in place: insert/refresh the auto-section "
            "between the BEGIN/END markers and ensure the supporting CSS "
            "exists. Without this flag the rows are only printed to stdout."
        ),
    )
    args = parser.parse_args()

    leaks = find_leaks(args.max_len)
    items = sorted(leaks.items(), key=_sort_key)
    if args.write:
        check_html_path = ROOT / "test" / "check.html"
        text = check_html_path.read_text()
        text = _ensure_css(text)
        text = _splice_section(text, items, args.max_len)
        check_html_path.write_text(text)
        print(
            f"Wrote {len(items)} leak rows to {check_html_path.relative_to(ROOT)}",
            file=sys.stderr,
        )
    else:
        for leak, witness in items:
            print(_format_row(leak, witness))
        print(f"\n<!-- {len(items)} unique isolation leaks -->", file=sys.stderr)


if __name__ == "__main__":
    main()
