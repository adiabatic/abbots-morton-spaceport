"""Find Senior sequences whose rendering differs between before/ and after.

Harvests every contiguous run of Quikscript codepoints (≥ 2 letters) from
``test/the-manual.html``, ``test/index.html``, and
``test/extra-senior-words.html``. For each unique sequence, shapes it
through both the snapshotted Senior font
(``test/before/AbbotsMortonSpaceportSansSenior-Regular.otf``) and the live
build (``test/AbbotsMortonSpaceportSansSenior-Regular.otf``) and records
every sequence whose render changed.

A "render" is the tuple, per output glyph, of:

    (glyph name, outline hash, x_advance, x_offset, y_offset)

Comparing all three pieces catches:

* GSUB changes — a different variant got chosen;
* GPOS changes — cursive attachment or kerning shifted positions;
* direct outline edits — same glyph name, different bitmap.

Output is one row per changed sequence, written into the auto-section of
``test/check.html`` so the before/after columns can be eyeballed
alongside the isolation-leaks rows already there. Run after
``make snapshot-before`` (on the baseline you want to compare against) and
``make all`` (to refresh the live build):

    uv run python tools/find_render_diffs.py --write

Each row uses the harvested word verbatim, so the difference is always
visible in real word context.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import uharfbuzz as hb
import yaml
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"

CORPUS_FILES: tuple[Path, ...] = (
    TEST_DIR / "the-manual.html",
    TEST_DIR / "index.html",
    TEST_DIR / "extra-senior-words.html",
)
SENIOR_FONT_NAME = "AbbotsMortonSpaceportSansSenior-Regular.otf"
BEFORE_FONT = TEST_DIR / "before" / SENIOR_FONT_NAME
AFTER_FONT = TEST_DIR / SENIOR_FONT_NAME

QS_FIRST = 0xE650
QS_LAST = 0xE67F
QS_RUN_RE = re.compile("[\uE650-\uE67F\u200C]+")
ENTITY_HEX_RE = re.compile(r"&#x([0-9A-Fa-f]+);")
ENTITY_DEC_RE = re.compile(r"&#(\d+);")


@dataclass(frozen=True)
class GlyphRender:
    name: str
    outline_hash: str
    x_advance: int
    x_offset: int
    y_offset: int


@dataclass(frozen=True)
class SequenceDiff:
    text: str
    before: tuple[GlyphRender, ...]
    after: tuple[GlyphRender, ...]

    @property
    def codepoints(self) -> tuple[int, ...]:
        return tuple(ord(c) for c in self.text)


class _OutlineHashPen(BasePen):
    """Record the pen calls a glyph makes; the tuple of calls is the
    outline's identity. Two glyphs with the same call sequence have the
    same outline regardless of how the font happened to compile them."""

    def __init__(self, glyph_set):
        super().__init__(glyph_set)
        self.calls: list[tuple] = []

    def _moveTo(self, pt):
        self.calls.append(("M", pt))

    def _lineTo(self, pt):
        self.calls.append(("L", pt))

    def _curveToOne(self, pt1, pt2, pt3):
        self.calls.append(("C", pt1, pt2, pt3))

    def _qCurveToOne(self, pt1, pt2):
        self.calls.append(("Q", pt1, pt2))

    def _closePath(self):
        self.calls.append(("Z",))

    def _endPath(self):
        self.calls.append(("E",))


def _outline_hashes(font_path: Path) -> dict[str, str]:
    tt = TTFont(str(font_path))
    glyph_set = tt.getGlyphSet()
    result: dict[str, str] = {}
    for name in tt.getGlyphOrder():
        pen = _OutlineHashPen(glyph_set)
        glyph_set[name].draw(pen)
        result[name] = hashlib.sha1(repr(pen.calls).encode()).hexdigest()[:12]
    tt.close()
    return result


def _hb_font(font_path: Path) -> hb.Font:
    blob = hb.Blob.from_file_path(str(font_path))
    return hb.Font(hb.Face(blob))


def _render(
    font: hb.Font, hashes: dict[str, str], text: str
) -> tuple[GlyphRender, ...]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    glyphs = []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = font.glyph_to_string(info.codepoint)
        glyphs.append(
            GlyphRender(
                name=name,
                outline_hash=hashes.get(name, "<missing>"),
                x_advance=pos.x_advance,
                x_offset=pos.x_offset,
                y_offset=pos.y_offset,
            )
        )
    return tuple(glyphs)


def _decode_entities(text: str) -> str:
    text = ENTITY_HEX_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = ENTITY_DEC_RE.sub(lambda m: chr(int(m.group(1))), text)
    return text


def _harvest_sequences(paths: tuple[Path, ...]) -> list[str]:
    seen: set[str] = set()
    for path in paths:
        text = _decode_entities(path.read_text())
        for run in QS_RUN_RE.findall(text):
            qs_letters = sum(1 for c in run if QS_FIRST <= ord(c) <= QS_LAST)
            if qs_letters >= 2:
                seen.add(run)
    return sorted(seen)


def find_diffs() -> list[SequenceDiff]:
    if not BEFORE_FONT.exists():
        raise SystemExit(
            f"Snapshot missing: {BEFORE_FONT.relative_to(ROOT)} not found.\n"
            "Run `make snapshot-before` on the baseline you want to compare against."
        )
    if not AFTER_FONT.exists():
        raise SystemExit(
            f"Live build missing: {AFTER_FONT.relative_to(ROOT)} not found.\n"
            "Run `make all` first."
        )
    sequences = _harvest_sequences(CORPUS_FILES)
    before_hashes = _outline_hashes(BEFORE_FONT)
    after_hashes = _outline_hashes(AFTER_FONT)
    before_font = _hb_font(BEFORE_FONT)
    after_font = _hb_font(AFTER_FONT)
    diffs: list[SequenceDiff] = []
    for seq in sequences:
        before = _render(before_font, before_hashes, seq)
        after = _render(after_font, after_hashes, seq)
        if before != after:
            diffs.append(SequenceDiff(text=seq, before=before, after=after))
    return diffs


# Reverse map from PUA codepoint → "qsX" family name, for human-readable
# labels. Mirrors the convention used elsewhere in tools/.
def _codepoint_to_family() -> dict[int, str]:
    with PS_NAMES_PATH.open() as f:
        ps_names = yaml.safe_load(f)
    return {
        codepoint: name
        for name, codepoint in ps_names.items()
        if name.startswith("qs") and "_" not in name
    }


_FAMILY_TO_LABEL = {"qsIng": "·-ing"}


def _short_label_for_codepoint(codepoint: int, cp_to_family: dict[int, str]) -> str:
    if codepoint == 0x200C:
        return "◊ZWNJ"
    family = cp_to_family.get(codepoint)
    if family is None:
        return f"U+{codepoint:04X}"
    if family in _FAMILY_TO_LABEL:
        return _FAMILY_TO_LABEL[family]
    return "·" + family[2:]


def _label_text(diff: SequenceDiff, cp_to_family: dict[int, str]) -> str:
    return " ".join(
        _short_label_for_codepoint(cp, cp_to_family) for cp in diff.codepoints
    )


def _summary_text(diff: SequenceDiff) -> str:
    """Compact one-line summary of *what* changed in this sequence's render."""
    before_names = tuple(g.name for g in diff.before)
    after_names = tuple(g.name for g in diff.after)
    parts: list[str] = []
    if before_names != after_names:
        parts.append(f"{' '.join(before_names)} → {' '.join(after_names)}")
        return "; ".join(parts)
    outline_changed = [
        b.name
        for b, a in zip(diff.before, diff.after)
        if b.outline_hash != a.outline_hash
    ]
    if outline_changed:
        parts.append(f"outline: {', '.join(outline_changed)}")
    position_changed = [
        b.name
        for b, a in zip(diff.before, diff.after)
        if b.outline_hash == a.outline_hash
        and (b.x_advance, b.x_offset, b.y_offset)
        != (a.x_advance, a.x_offset, a.y_offset)
    ]
    if position_changed:
        parts.append(f"positions: {', '.join(position_changed)}")
    return "; ".join(parts) if parts else "(unchanged glyphs, but tuple differs)"


def _entities(text: str) -> str:
    return "".join(f"&#x{ord(c):X};" for c in text)


def _codepoints_text(diff: SequenceDiff) -> str:
    return " ".join(f"U+{cp:04X}" for cp in diff.codepoints)


_COPY_BUTTON_HTML = (
    '<button type="button" class="copy-codepoints"'
    ' title="Copy prompt preamble to clipboard"'
    ' aria-label="Copy prompt preamble to clipboard">'
    '<img src="icons/copy.svg" alt="" width="12" height="12">'
    '<span class="copied-toast">Copied!</span>'
    "</button>"
)


def _format_row(diff: SequenceDiff, cp_to_family: dict[int, str]) -> str:
    label = _label_text(diff, cp_to_family)
    summary = _summary_text(diff)
    code = _codepoints_text(diff)
    rendered = _entities(diff.text)
    return (
        '      <div class="row">\n'
        '        <div class="label">\n'
        f"          {label} ({summary})\n"
        f'          <div class="codepoints">{_COPY_BUTTON_HTML}<code>{code}</code></div>\n'
        "        </div>\n"
        f'        <div class="qs before">{rendered}</div>\n'
        f'        <div class="qs after">{rendered}</div>\n'
        "      </div>"
    )


def _sort_key(diff: SequenceDiff) -> tuple:
    return (len(diff.codepoints), diff.codepoints)


SECTION_BEGIN = "<!-- BEGIN AUTO: render-diffs -->"
SECTION_END = "<!-- END AUTO: render-diffs -->"


def _build_section(diffs: list[SequenceDiff], cp_to_family: dict[int, str]) -> str:
    rows = "\n".join(_format_row(d, cp_to_family) for d in diffs)
    if not rows:
        rows = (
            '      <p style="padding: 1rem 1.5rem; margin: 0; '
            'font-family: Seravek, Corbel, \'Avenir Next\', sans-serif; '
            "font-size: 14px;\">No differences found across the harvested corpus. "
            "Either the change is invisible at the Senior-Regular level, or you "
            "haven't refreshed <code>test/before/</code> recently.</p>"
        )
    return (
        f"{SECTION_BEGIN}\n"
        '    <section class="render-diffs">\n'
        "      <h2>Auto-generated: corpus render diffs</h2>\n"
        "      <p>\n"
        "        Every multi-letter Quikscript run harvested from\n"
        "        <code>test/the-manual.html</code>, <code>test/index.html</code>,\n"
        "        and <code>test/extra-senior-words.html</code> whose Senior-Regular\n"
        "        render differs between <code>test/before/</code> and the live\n"
        "        build. A render is the per-glyph tuple\n"
        "        <code>(glyph name, outline hash, x_advance, x_offset, y_offset)</code>,\n"
        "        so this surfaces GSUB changes (different variant chosen), GPOS\n"
        "        changes (cursive/kerning shifts), and outline edits to same-named\n"
        "        glyphs. Generated by <code>tools/find_render_diffs.py</code>.\n"
        "      </p>\n"
        "      <p>\n"
        "        Each row's label names the change in glyph terms (e.g.\n"
        "        <code>qsTea → qsTea.exit-xheight</code>). Scan for sequences you\n"
        "        weren't intending to touch — those are the regressions.\n"
        "      </p>\n"
        '      <div class="col-headers">\n'
        "        <span>Sequence (what changed)</span>\n"
        "        <span>Before (snapshot)</span>\n"
        "        <span>After (live build)</span>\n"
        "      </div>\n"
        f"{rows}\n"
        "    </section>\n"
        f"    {SECTION_END}"
    )


def _splice_section(check_html: str, body: str) -> str:
    if SECTION_BEGIN in check_html and SECTION_END in check_html:
        before, _, rest = check_html.partition(SECTION_BEGIN)
        _, _, after = rest.partition(SECTION_END)
        return before + body + after
    anchor = '    <p class="footer">'
    if anchor not in check_html:
        raise RuntimeError("could not find footer anchor in check.html")
    return check_html.replace(anchor, "    " + body + "\n\n" + anchor, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Update test/check.html in place: insert/refresh the auto-section "
            "between the BEGIN/END markers. Without this flag the diffs are "
            "only printed to stdout."
        ),
    )
    args = parser.parse_args()

    diffs = sorted(find_diffs(), key=_sort_key)
    cp_to_family = _codepoint_to_family()

    if args.write:
        check_html_path = TEST_DIR / "check.html"
        text = check_html_path.read_text()
        text = _splice_section(text, _build_section(diffs, cp_to_family))
        check_html_path.write_text(text)
        print(
            f"Wrote {len(diffs)} render-diff rows to {check_html_path.relative_to(ROOT)}",
            file=sys.stderr,
        )
        return

    if not diffs:
        print("No render diffs found across the harvested corpus.")
        return
    for diff in diffs:
        label = _label_text(diff, cp_to_family)
        summary = _summary_text(diff)
        code = _codepoints_text(diff)
        print(f"{label}  [{code}]  {summary}")
    print(f"\n{len(diffs)} sequences changed.", file=sys.stderr)


if __name__ == "__main__":
    main()
