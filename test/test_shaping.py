"""HarfBuzz shaping regression tests for the Senior Sans font.

Parses data-expect attributes from test/index.html and verifies that
HarfBuzz produces the expected glyph sequence and cursive connections.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
import uharfbuzz as hb

ROOT = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT / "test" / "AbbotsMortonSpaceportSansSenior.otf"
GLYPH_DATA_DIR = ROOT / "glyph_data"
HTML_PATH = ROOT / "test" / "index.html"

import sys
sys.path.insert(0, str(ROOT))
from build_font import (
    _normalize_anchors,
    generate_noentry_variants,
    load_glyph_data,
    prepare_proportional_glyphs,
)


# ---------------------------------------------------------------------------
# data-expect parser
# ---------------------------------------------------------------------------

TOKEN_RE = re.compile(
    r"""
    ·(-ing|[A-Z][a-z]*)        # letter name (·Bay, ·-ing)
    (?:\+([A-Z][a-z]*))?       # optional ligature partner (+Utter)
    ((?:\.[a-z]+)*)            # optional variant assertions (.half.extended)
    """,
    re.VERBOSE,
)

CONN_RE = re.compile(r"\s*~([xbt6])~\s*|\s*\|\s*")


def _letter_to_qs(name):
    if name == "-ing":
        return "qsIng"
    return "qs" + name


def parse_expect(raw):
    """Parse a data-expect string into (tokens, connections).

    tokens:  list of dicts with keys:
        base      – e.g. "qsBay"
        lig_base  – e.g. "qsUtter" if ligature, else None
        variants  – list of variant assertion strings, e.g. ["half"]
    connections: list of dicts (len = len(tokens) - 1) with keys:
        kind      – "join", "break", or "height"
        y         – int or None (only for "height")
    """
    HEIGHT_MAP = {"x": 5, "b": 0, "t": 8, "6": 6}

    tokens = []
    connections = []
    pos = 0
    first = True

    while pos < len(raw):
        remaining = raw[pos:]

        if first:
            remaining = remaining.lstrip()
            first = False
        else:
            conn_m = CONN_RE.match(remaining)
            if conn_m is None:
                ws = re.match(r"\s+", remaining)
                if ws:
                    connections.append({"kind": "join", "y": None})
                    pos += ws.end()
                    remaining = raw[pos:]
                else:
                    raise ValueError(
                        f"Expected connection operator at pos {pos}: {remaining!r}"
                    )
            else:
                if conn_m.group(1):
                    h = conn_m.group(1)
                    connections.append({"kind": "height", "y": HEIGHT_MAP[h]})
                else:
                    connections.append({"kind": "break", "y": None})
                pos += conn_m.end()
                remaining = raw[pos:]

        remaining = remaining.lstrip()
        pos = len(raw) - len(remaining)

        tok_m = TOKEN_RE.match(remaining)
        if tok_m is None:
            raise ValueError(f"Expected glyph token at pos {pos}: {remaining!r}")

        letter = tok_m.group(1)
        lig_partner = tok_m.group(2)
        variant_str = tok_m.group(3)

        variants = [v for v in variant_str.split(".") if v] if variant_str else []

        tokens.append({
            "base": _letter_to_qs(letter),
            "lig_base": _letter_to_qs(lig_partner) if lig_partner else None,
            "variants": variants,
        })
        pos += tok_m.end()

    return tokens, connections


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def senior_font():
    if not FONT_PATH.exists():
        pytest.skip(f"Font not built: {FONT_PATH}")
    blob = hb.Blob.from_file_path(str(FONT_PATH))
    face = hb.Face(blob)
    font = hb.Font(face)
    return font


@pytest.fixture(scope="session")
def anchor_map():
    """Map glyph names to their cursive_entry/cursive_exit anchors.

    Uses the same pipeline as build_font (prepare_proportional_glyphs +
    generate_noentry_variants) so names match what's in the Senior font.
    """
    data = load_glyph_data(GLYPH_DATA_DIR)
    glyphs = data["glyphs"]
    glyphs = {k: v for k, v in glyphs.items() if ".unused" not in k}
    glyphs = prepare_proportional_glyphs(glyphs)
    glyphs.update(generate_noentry_variants(glyphs))

    result = {}
    for name, gdef in glyphs.items():
        if gdef is None:
            continue
        entry = _normalize_anchors(gdef.get("cursive_entry"))
        exit_ = _normalize_anchors(gdef.get("cursive_exit"))
        if entry or exit_:
            result[name] = {"entry": entry, "exit": exit_}
    return result


# ---------------------------------------------------------------------------
# Test collection from HTML
# ---------------------------------------------------------------------------

class _DataExpectCollector(HTMLParser):
    """Collect <td data-expect="..."> cells from HTML."""

    def __init__(self):
        super().__init__()
        self.cells = []
        self._in_td = False
        self._expect = None
        self._text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            attr_dict = dict(attrs)
            if "data-expect" in attr_dict:
                self._in_td = True
                self._expect = attr_dict["data-expect"]
                self._text_parts = []

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td:
            text = "".join(self._text_parts).strip()
            self.cells.append((text, self._expect))
            self._in_td = False
            self._expect = None

    def handle_data(self, data):
        if self._in_td:
            self._text_parts.append(data)


def _collect_cases():
    if not HTML_PATH.exists():
        return []
    raw = HTML_PATH.read_text(encoding="utf-8")
    collector = _DataExpectCollector()
    collector.feed(raw)

    seen_ids = {}
    results = []
    for text, expect in collector.cells:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:40]
        if not slug:
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", expect).strip("_")[:40]
        if slug in seen_ids:
            seen_ids[slug] += 1
            slug = f"{slug}_{seen_ids[slug]}"
        else:
            seen_ids[slug] = 0
        results.append((slug, text, expect))
    return results


_CASES = _collect_cases()


@pytest.mark.parametrize(
    "test_id,text,expect_str",
    [pytest.param(tid, txt, exp, id=tid) for tid, txt, exp in _CASES],
)
def test_shaping(senior_font, anchor_map, test_id, text, expect_str):
    tokens, connections = parse_expect(expect_str)

    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(senior_font, buf)

    infos = buf.glyph_infos
    glyph_names = []
    for info in infos:
        name = senior_font.glyph_to_string(info.codepoint)
        glyph_names.append(name)

    assert len(glyph_names) == len(tokens), (
        f"Glyph count mismatch: got {glyph_names}, expected {len(tokens)} tokens"
    )

    for i, (gname, tok) in enumerate(zip(glyph_names, tokens)):
        base = tok["base"]
        lig = tok["lig_base"]

        if lig:
            assert base in gname and lig in gname, (
                f"Glyph {i}: expected ligature {base}+{lig}, got {gname!r}"
            )
        else:
            glyph_base = gname.split(".")[0].split("_")[0]
            assert glyph_base == base, (
                f"Glyph {i}: expected base {base}, got {gname!r}"
            )

        for v in tok["variants"]:
            assert v in gname, (
                f"Glyph {i}: expected variant '{v}' in {gname!r}"
            )

    for i, conn in enumerate(connections):
        left = glyph_names[i]
        right = glyph_names[i + 1]
        left_anchors = anchor_map.get(left, {})
        right_anchors = anchor_map.get(right, {})
        left_exits = {a[1] for a in left_anchors.get("exit", [])}
        right_entries = {a[1] for a in right_anchors.get("entry", [])}
        common_ys = left_exits & right_entries

        if conn["kind"] == "break":
            assert not common_ys, (
                f"Connection {i}: expected break between {left} and {right}, "
                f"but found common Y values {common_ys}"
            )
        elif conn["kind"] == "join":
            assert common_ys, (
                f"Connection {i}: expected join between {left} and {right}, "
                f"but no common Y values (exits={left_exits}, entries={right_entries})"
            )
        elif conn["kind"] == "height":
            expected_y = conn["y"]
            assert expected_y in common_ys, (
                f"Connection {i}: expected join at y={expected_y} between "
                f"{left} and {right}, common Ys={common_ys}"
            )
