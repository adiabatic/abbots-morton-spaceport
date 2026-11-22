"""Write the word lists of the Manual letter survey from `site/the-manual.html`.

The survey (`reference/manual-letters/`) records where every handwritten Quikscript letter sits on the scanned pages of `reference/Quikscript Manual.pdf`. This script supplies the part the HTML transcription already knows: for each PDF page, the words in reading order, their letters, the orthodox gloss where the markup gives one, and a first guess at each letter's form (full, half, alt) and at each join. The guess comes from shaping the word with the built Sans fonts, so it is the pixel font's opinion, and `src` says whether a `data-expect` attribute backs it (`expect`) or nothing does (`font`). The positions (`line`, `x`, `cuts`) start empty and are filled by eye against the scan; `tools/manual_letters.py` draws the views for that and builds the crops.

Run it from the repository root after `make all`, since it shapes with the fonts under `site/`:

    uv run python tools/manual_letters_prefill.py            # every page that has no word list yet
    uv run python tools/manual_letters_prefill.py --page 16  # one page
    uv run python tools/manual_letters_prefill.py --refresh  # rewrite the guesses of the words not yet placed
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
sys.path.insert(0, str(ROOT / "tools"))

import test_shaping as shaping  # noqa: E402

from manual_letters_words import load_page, page_path, save_page  # noqa: E402

MANUAL_HTML = ROOT / "site" / "the-manual.html"

BLOCK_TAGS = frozenset(
    "p td th tr table div section h1 h2 h3 h4 h5 h6 li ul ol blockquote dd dt dl hgroup aside br hr figure figcaption".split()
)
VOID_TAGS = frozenset("br hr img input meta link wbr".split())
JOIN_CODES = {0: "b", 5: "x", 6: "6", 8: "t"}
ZWNJ = "‌"


def _is_letter(ch: str) -> bool:
    return "" <= ch <= "" or "" <= ch <= ""


@dataclass
class _Char:
    ch: str
    font: str
    features: tuple[str, ...]
    expect: str | None
    cell: tuple[int, int, int] | None
    paragraph: int | None


@dataclass
class _Collector(HTMLParser):
    """Flatten the Manual's markup into per-page streams of characters, with a `None` wherever a block boundary ends a word."""

    pages: dict[int, list[_Char | None]] = field(default_factory=dict)
    cells: dict[tuple[int, int, int], str] = field(default_factory=dict)
    orthodox: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._page = 0
        self._stack: list[tuple[str, str | None, str | None, int | None, tuple[str, ...]]] = []
        self._table = 0
        self._row = 0
        self._col = 0
        self._cell: tuple[int, int, int] | None = None
        self._paragraphs = 0

    def _inherited(self, index: int) -> Any:
        for entry in reversed(self._stack):
            if entry[index] is not None:
                return entry[index]
        return None

    def _break(self) -> None:
        self.pages.setdefault(self._page, []).append(None)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        page = re.fullmatch(r"pdf-page-(\d+)", attr.get("id") or "")
        if page:
            self._page = int(page.group(1))
        if tag in BLOCK_TAGS:
            self._break()
        if tag == "table":
            self._table += 1
            self._row = 0
        elif tag == "tr":
            self._row += 1
            self._col = 0
        elif tag in ("td", "th"):
            self._col += 1
            self._cell = (self._table, self._row, self._col)
        if tag in VOID_TAGS:
            return
        classes = (attr.get("class") or "").split()
        font = "junior" if "force-junior" in classes else "senior" if "force-senior" in classes else None
        expect = attr.get("data-expect", attr.get("data-expect-noncanonically"))
        paragraph = None
        if attr.get("data-orthodox"):
            self._paragraphs += 1
            paragraph = self._paragraphs
            self.orthodox[paragraph] = attr["data-orthodox"] or ""
        sets = tuple(f"ss{number.zfill(2)}" for number in (attr.get("data-stylistic-set") or "").split())
        self._stack.append((tag, font, expect, paragraph, sets))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        if tag in ("td", "th"):
            self._cell = None
        if tag in BLOCK_TAGS:
            self._break()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self.cells[self._cell] = self.cells.get(self._cell, "") + data
        font = self._inherited(1) or "senior"
        expect = self._inherited(2)
        paragraph = self._inherited(3)
        features = tuple(sorted({name for entry in self._stack for name in entry[4]}))
        stream = self.pages.setdefault(self._page, [])
        for ch in data:
            stream.append(_Char(ch, font, features, expect, self._cell, paragraph))


def _split_words(stream: list[_Char | None]) -> list[list[_Char]]:
    words: list[list[_Char]] = []
    current: list[_Char] = []
    for item in [*stream, None]:
        if item is None or (item.ch.isspace() and item.ch != ZWNJ):
            if any(_is_letter(c.ch) for c in current):
                words.append(current)
            current = []
        else:
            current.append(item)
    return words


def _shape_run(text: str, variant: str, features: tuple[str, ...]) -> list[tuple[str, int]]:
    """Shape one single-font run and return each glyph's compiled name and cluster."""
    import uharfbuzz as hb

    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(_font(variant), buf, {name: True for name in features})
    return [(shaping.shaped_glyph_name(variant, info.codepoint), info.cluster) for info in buf.glyph_infos]


_FONTS: dict[str, Any] = {}


def _font(variant: str) -> Any:
    if variant not in _FONTS:
        _FONTS[variant] = shaping.load_font(variant)
    return _FONTS[variant]


def _predict(chars: list[_Char], names: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return the font's form for each letter (`qsPea.half`) and the join after each letter but the last (`b`, `x`, `t`, `6`, `+` inside a font ligature, `|` for none)."""
    letter_index = [i for i, c in enumerate(chars) if _is_letter(c.ch)]
    forms = [names[chars[i].ch] for i in letter_index]
    joins = ["|"] * (len(letter_index) - 1)
    position = {char_index: n for n, char_index in enumerate(letter_index)}

    start = 0
    while start < len(chars):
        end = start
        while end < len(chars) and (chars[end].font, chars[end].features) == (
            chars[start].font,
            chars[start].features,
        ):
            end += 1
        variant = chars[start].font
        anchor_map, _ = shaping.build_anchor_map(variant)
        glyphs = _shape_run("".join(c.ch for c in chars[start:end]), variant, chars[start].features)
        spans: list[tuple[str, list[int]]] = []
        for g, (name, cluster) in enumerate(glyphs):
            next_cluster = glyphs[g + 1][1] if g + 1 < len(glyphs) else end - start
            covered = [position[start + k] for k in range(cluster, next_cluster) if start + k in position]
            spans.append((name, covered))
        for g, (name, covered) in enumerate(spans):
            if not covered:
                continue
            meta = shaping._compiled_glyph_meta(name, variant)
            for trait in ("half", "alt"):
                if trait in meta.traits:
                    forms[covered[0]] += f".{trait}"
            for a in covered[:-1]:
                joins[a] = "+"
            if variant == "junior" or g + 1 >= len(spans) or not spans[g + 1][1]:
                continue
            exits = {a[1] for a in anchor_map.get(name, {}).get("exit", [])}
            entries = {a[1] for a in anchor_map.get(spans[g + 1][0], {}).get("entry", [])}
            common = sorted(exits & entries)
            if common:
                joins[covered[-1]] = JOIN_CODES.get(common[0], str(common[0]))
        start = end
    return forms, joins


def _pattern(chars: list[_Char]) -> str:
    return "".join("L" if _is_letter(c.ch) else "‌" if c.ch == ZWNJ else c.ch for c in chars).replace(
        "‌", "/"
    )


def build_pages() -> dict[int, list[dict[str, Any]]]:
    collector = _Collector()
    collector.feed(MANUAL_HTML.read_text(encoding="utf-8"))
    collector.close()
    names = shaping._build_char_to_glyph_name()

    by_paragraph: dict[int, list[dict[str, Any]]] = {}
    pages: dict[int, list[dict[str, Any]]] = {}
    for page, stream in sorted(collector.pages.items()):
        words = []
        for n, chars in enumerate(_split_words(stream), 1):
            forms, joins = _predict(chars, names)
            first = next(c for c in chars if _is_letter(c.ch))
            gloss = ""
            if first.cell is not None:
                table, row, col = first.cell
                above = collector.cells.get((table, row - 1, col), "")
                if above and not any(_is_letter(ch) for ch in above):
                    gloss = " ".join(above.split())
            word: dict[str, Any] = {
                "id": f"p{page:02d}-w{n:03d}",
                "gloss": gloss,
                "letters": forms,
                "joins": joins,
                "src": "expect" if first.expect else "font",
                "font": first.font,
                "pattern": _pattern(chars),
                "expect": first.expect or "",
                "line": None,
                "x": None,
                "cuts": [],
                "note": "",
            }
            words.append(word)
            if first.paragraph is not None:
                by_paragraph.setdefault(first.paragraph, []).append(word)
        if words:
            pages[page] = words

    for paragraph, words in by_paragraph.items():
        orthodox = collector.orthodox[paragraph].split()
        if len(orthodox) == len(words):
            for word, gloss in zip(words, orthodox):
                word["gloss"] = gloss
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the word lists of the Manual letter survey.")
    parser.add_argument(
        "--page", type=int, action="append", help="PDF page to write (repeatable); default every page"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="rewrite existing lists, keeping every word that has a position or a note",
    )
    args = parser.parse_args()

    for page, words in build_pages().items():
        if args.page and page not in args.page:
            continue
        path = page_path(page)
        lines = []
        if path.exists():
            if not args.refresh:
                print(f"{path.relative_to(ROOT)}: exists, skipped")
                continue
            old = load_page(page)
            lines = old["lines"]
            placed = {word["id"]: word for word in old["words"] if word["x"] is not None or word["note"]}
            words = [placed.get(word["id"], word) for word in words]
        save_page({"page": page, "lines": lines, "words": words})
        print(
            f"{path.relative_to(ROOT)}: {len(words)} words, {sum(len(w['letters']) for w in words)} letters"
        )


if __name__ == "__main__":
    main()
