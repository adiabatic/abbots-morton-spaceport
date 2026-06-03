"""Generate test/check.html — the side-by-side before/after rendering harness.

One program, one file out. Replaces the previous arrangement where a static ``test/check.html`` was mutated in place by two separate splicer tools.

The page contains:

* Standard page chrome (title, intro, workflow notes, footer).
* A "corpus render diffs" section: every multi-letter Quikscript run harvested from ``test/the-manual.html``, ``test/index.html``, and ``test/extra-senior-words.html`` whose Senior-Regular render differs between ``test/before/`` and the live build. Skipped with a notice when the snapshot is missing.
* An "isolation leaks" section: short sequences whose adjacent non-joining pair changes shape when the pair is shaped together vs. independently — the same invariant as ``_check_break_isolation`` in ``test/test_shaping.py``. These are the cases that need ``|?|`` (instead of ``|``) in ``data-expect``.
* A "failing tests" section: one row per assertion line from a currently-failing pytest test under ``test/``. The row renders the input families parsed out of the failure message so you can eyeball false positives.
* A copy-codepoints click handler so each row's ``U+E6XX`` strip can be copied as a prompt preamble.

Run (after ``make all`` and, optionally, ``make snapshot-before`` on the baseline branch)::

    uv run python tools/build_check_html.py

``--max-len`` controls how deep the isolation-leaks sweep goes (default 3, which catches every pair plus single-letter context on either side; bump to 4 for a slower deeper sweep).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import io
import itertools
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uharfbuzz as hb
import yaml
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
PS_NAMES_PATH = ROOT / "postscript_glyph_names.yaml"
CHECK_HTML_PATH = TEST_DIR / "check.html"
LEAK_SNAPSHOT_PATH = TEST_DIR / "isolation-leak-snapshot.txt"

# Reuse the test-suite shaping helpers so the isolation-leaks sweep matches what ``test_shaping.py`` enforces.
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from quikscript_shaping_helpers import (  # noqa: E402
    _char_map,
    _compiled_meta,
    _font,
    _pair_join_ys,
    _plain_quikscript_letters,
    _qs_text,
    _shape_qs,
)

# ---------------------------------------------------------------------------
# Isolation-leak detection (was tools/find_isolation_leaks.py).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Leak:
    left_chosen: str
    right_chosen: str
    isolated_left: str
    isolated_right: str

    @property
    def left_changed(self) -> bool:
        return self.left_chosen != self.isolated_left

    @property
    def right_changed(self) -> bool:
        return self.right_chosen != self.isolated_right


@dataclass(frozen=True)
class IsolationLeakExample:
    families: tuple[str, ...]
    break_index: int  # output-glyph position of the leaky break


def _is_letter_glyph(name: str) -> bool:
    """Whether *name* is a Quikscript letter glyph eligible for the isolation check. Mirrors ``_is_qs_letter`` in ``test/test_shaping.py``."""
    if not name.startswith("qs"):
        return False
    base = name.split(".", 1)[0]
    return base not in {"qsAngleParenLeft", "qsAngleParenRight"}


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
    """Return (break_index, Leak) pairs for every leaky non-join in *families*."""
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
                    isolated_left=split_left,
                    isolated_right=split_right,
                ),
            ),
        )
    return results


def find_leaks(max_len: int) -> dict[Leak, IsolationLeakExample]:
    """Enumerate sequences up to *max_len* and collect unique leaks."""
    letters = [name for name, _ in _plain_quikscript_letters()]
    leaks: dict[Leak, IsolationLeakExample] = {}
    for length in range(2, max_len + 1):
        for families in itertools.product(letters, repeat=length):
            for break_i, leak in _scan_sequence(families):
                if leak not in leaks:
                    leaks[leak] = IsolationLeakExample(families=families, break_index=break_i)
    return leaks


def _shaped_input_spans(example: IsolationLeakExample) -> tuple[tuple[int, int], tuple[int, int]]:
    full = _shape_qs(*example.families)
    spans = _input_spans(full)
    if spans is None:
        raise RuntimeError(f"could not map spans for {example.families!r}")
    l_end = spans[example.break_index][1]
    r_start = spans[example.break_index + 1][0]
    return (0, l_end), (r_start, len(example.families))


def _visual_signature(name: str) -> tuple:
    g = _compiled_meta()[name]
    return (tuple(g.bitmap), g.y_offset, g.advance_width)


def _abs_render_signature(parts: tuple[str, ...]) -> tuple[list[tuple], int, int]:
    """Shape *parts* and return per-glyph (visual, abs_x, abs_y) plus the sequence's total advance — the single-buffer equivalent of how the inline-block halves butt up in the rendered HTML. The `kern` feature is turned off because an isolation leak is about joining and contextual form selection, not spacing: a kern pair that legitimately tightens a non-joining break (e.g. ·Ye·It) would otherwise shift the in-context run but not the independently-shaped halves, flagging intended kerning as a leak even when the two forms are visually identical."""
    font = _font()
    buf = hb.Buffer()
    buf.add_str(_qs_text(*parts))
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": False})
    pen_x = 0
    pen_y = 0
    sigs: list[tuple] = []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = font.glyph_to_string(info.codepoint)
        sigs.append((_visual_signature(name), pen_x + pos.x_offset, pen_y + pos.y_offset))
        pen_x += pos.x_advance
        pen_y += pos.y_advance
    return sigs, pen_x, pen_y


def _visual_status(example: IsolationLeakExample) -> str:
    """Classify a leak as ``same`` or ``diff`` by comparing the in-context render of the example sequence against the concatenation of its two independently-shaped halves."""
    full_sigs, _, _ = _abs_render_signature(example.families)
    (l0, l1), (r0, r1) = _shaped_input_spans(example)
    left_sigs, left_x, left_y = _abs_render_signature(example.families[l0:l1])
    right_sigs, _, _ = _abs_render_signature(example.families[r0:r1])
    halves_sigs = left_sigs + [(sig, x + left_x, y + left_y) for sig, x, y in right_sigs]
    return "same" if full_sigs == halves_sigs else "diff"


# ---------------------------------------------------------------------------
# Render-diff detection (was tools/find_render_diffs.py).
# ---------------------------------------------------------------------------


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
QS_RUN_RE = re.compile("[\ue650-\ue67f\u200c]+")
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
    """Record pen calls so two glyphs with the same outline produce the same fingerprint regardless of how the font compiled them."""

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


def _render(font: hb.Font, hashes: dict[str, str], text: str) -> tuple[GlyphRender, ...]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    glyphs: list[GlyphRender] = []
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
    """Compare snapshot vs. live Senior-Regular renders for every harvested multi-letter run. Caller is expected to confirm BEFORE_FONT exists."""
    if not AFTER_FONT.exists():
        raise SystemExit(
            f"Live build missing: {AFTER_FONT.relative_to(ROOT)} not found.\n" "Run `make all` first."
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


# ---------------------------------------------------------------------------
# Failing-tests collection.
# ---------------------------------------------------------------------------


@dataclass
class TestFailure:
    """One pytest test that failed, plus the individual `E   ` assertion lines parsed out of its long traceback."""

    nodeid: str
    sub_messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FailureRow:
    """One row in the failing-tests section: a single sub-failure line, optionally paired with the input families it shapes."""

    nodeid: str
    message: str
    families: tuple[str, ...]  # empty when the message didn't parse
    text: str  # actual characters to shape; "" when families is empty


class _FailureCollector:
    """Pytest plugin that captures longreprs of failing tests. Works under xdist because pytest forwards `pytest_runtest_logreport` to the controller after each worker finishes."""

    def __init__(self) -> None:
        self.failures: list[TestFailure] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.failed and report.when == "call":
            self.failures.append(
                TestFailure(
                    nodeid=report.nodeid,
                    sub_messages=_extract_assertion_lines(str(report.longrepr)),
                )
            )


_E_LINE_RE = re.compile(r"^E\s{2,}(.*)$")


def _extract_assertion_lines(longrepr: str) -> list[str]:
    """Pull the `E   …` assertion lines out of a pytest long traceback.

    The first such line is preceded by `AssertionError: ` (or whatever exception name); strip that prefix so each entry is a bare message. Continuation lines without the `E   ` prefix are joined onto the previous entry.
    """
    out: list[str] = []
    for raw in longrepr.splitlines():
        match = _E_LINE_RE.match(raw)
        if match is None:
            continue
        body = match.group(1)
        if body.startswith("AssertionError: "):
            body = body[len("AssertionError: ") :]
        elif not out and ": " in body:
            head, _, rest = body.partition(": ")
            if head.endswith("Error") or head.endswith("Exception"):
                body = rest
        out.append(body)
    return out


# `_collect_stranded_extension_joins` (and its siblings) format their
# failure messages as `[a·b] / qsX / qsY / [c·d]: <reason>`. `∅` marks an
# empty context slot. `ZWNJ` is a context token, not a family. Anything
# else we leave alone.
_FAILURE_LABEL_RE = re.compile(
    r"^\s*((?:\[[^\]]*\]|qs[A-Za-z0-9]+)(?:\s*/\s*(?:\[[^\]]*\]|qs[A-Za-z0-9]+))+)\s*:"
)
_FAMILY_TOKEN_RE = re.compile(r"qs[A-Za-z0-9]+|ZWNJ")

# `test_join_ink.py` reports gaps as
# `qsX.variant -> qsY.variant at y=N (kind): reason (context ·A·B·C·D)`.
# When the `[…] / qsX / qsY / […]:` shape doesn't match, fall back to the
# trailing `(context …)` clause and pull families out of that.
_CONTEXT_CLAUSE_RE = re.compile(r"\(context\s+((?:·(?:qs[A-Za-z0-9]+|ZWNJ))+)\s*\)")


def _parse_families_from_message(message: str) -> tuple[str, ...]:
    """Return the input-family sequence for messages that follow the `[…] / qsX / qsY / […]:` convention or carry a `(context ·X·Y·…)` trailer; empty tuple otherwise."""
    match = _FAILURE_LABEL_RE.match(message)
    if match is not None:
        families: list[str] = []
        for group in (g.strip() for g in match.group(1).split("/")):
            if group.startswith("[") and group.endswith("]"):
                inner = group[1:-1]
                if inner == "∅" or not inner:
                    continue
                families.extend(part.strip() for part in inner.split("·") if part.strip())
            else:
                families.append(group)
        return tuple(families)
    context_match = _CONTEXT_CLAUSE_RE.search(message)
    if context_match is not None:
        return tuple(part for part in context_match.group(1).split("·") if part)
    return ()


def _families_to_text(families: tuple[str, ...], cp_map: dict[str, int]) -> str:
    """Map a family/ZWNJ sequence to the literal characters that shape it. Returns "" if any token isn't recognized."""
    parts: list[str] = []
    for fam in families:
        if fam == "ZWNJ":
            parts.append("‌")
            continue
        codepoint = cp_map.get(fam)
        if codepoint is None:
            return ""
        parts.append(chr(codepoint))
    return "".join(parts)


def collect_test_failures() -> list[TestFailure]:
    """Run the full pytest suite in-process and return the captured failures. Output is silenced so it doesn't drown the check-html log."""
    print("Running pytest to harvest failing assertions…", file=sys.stderr, flush=True)
    collector = _FailureCollector()
    args = [
        "test/",
        "-q",
        "--no-header",
        "--tb=long",
        "-p",
        "no:cacheprovider",
        "-n",
        "auto",
        "--dist",
        "worksteal",
    ]
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        pytest.main(args, plugins=[collector])
    print(f"  pytest reported {len(collector.failures)} failing test(s)", file=sys.stderr, flush=True)
    return sorted(collector.failures, key=lambda f: f.nodeid)


def build_failure_rows(failures: list[TestFailure]) -> list[FailureRow]:
    cp_map = {fam: ord(ch) for fam, ch in _char_map().items() if fam.startswith("qs") and "_" not in fam}
    rows: list[FailureRow] = []
    for failure in failures:
        if not failure.sub_messages:
            rows.append(
                FailureRow(
                    nodeid=failure.nodeid, message="(no assertion text captured)", families=(), text=""
                )
            )
            continue
        for message in failure.sub_messages:
            families = _parse_families_from_message(message)
            text = _families_to_text(families, cp_map) if families else ""
            rows.append(FailureRow(nodeid=failure.nodeid, message=message, families=families, text=text))
    return rows


# ---------------------------------------------------------------------------
# Shared formatting helpers.
# ---------------------------------------------------------------------------


_FAMILY_TO_LABEL = {"qsIng": "·-ing"}

_FAMILY_TO_TABLES_NAME = {
    "qsJai": "J'ai",
    "qsIng": "-ing",
}


def _short_label(family: str) -> str:
    """``qsRoe`` -> ``·Roe``; ``qsIng`` -> ``·-ing`` (matches data-expect style)."""
    if family in _FAMILY_TO_LABEL:
        return _FAMILY_TO_LABEL[family]
    return "·" + family[2:]


def _short_label_for_codepoint(codepoint: int, cp_to_family: dict[int, str]) -> str:
    if codepoint == 0x200C:
        return "◊ZWNJ"
    family = cp_to_family.get(codepoint)
    if family is None:
        return f"U+{codepoint:04X}"
    return _short_label(family)


def _family_to_codepoint() -> dict[str, int]:
    chars = _char_map()
    return {name: ord(chars[name]) for name in chars if name.startswith("qs") and "_" not in name}


def _codepoint_to_family() -> dict[int, str]:
    with PS_NAMES_PATH.open() as f:
        ps_names = yaml.safe_load(f)
    return {
        codepoint: name for name, codepoint in ps_names.items() if name.startswith("qs") and "_" not in name
    }


def _entity_for(codepoint: int) -> str:
    return f"&#x{codepoint:X};"


def _entities(text: str) -> str:
    return "".join(f"&#x{ord(c):X};" for c in text)


def _tables_letter_name(family: str) -> str:
    return _FAMILY_TO_TABLES_NAME.get(family, family[2:])


# Standard "open in new window" icon, used for 3-letter rows that point at one specific cell.
_OPEN_IN_TABLES_ICON = '<img src="icons/open-in-new.svg" alt="" width="12" height="12">'

# Three cells with the leftmost two filled — points at a tables.html column strip where the pair appears as the first two letters of every cell.
_OPEN_IN_TABLES_FIRST_TWO_ICON = '<img src="icons/cells-fade-right.svg" alt="" width="12" height="12">'

# Mirror image — points at a tables.html row strip where the pair appears as the last two letters of every cell.
_OPEN_IN_TABLES_LAST_TWO_ICON = '<img src="icons/cells-fade-left.svg" alt="" width="12" height="12">'


def _tables_anchor(params: list[tuple[str, str]], label: str, icon: str) -> str:
    href = html.escape(f"tables.html#{urllib.parse.urlencode(params)}", quote=True)
    label_attr = html.escape(label, quote=True)
    return (
        f'<a class="open-in-tables" href="{href}" target="_blank" rel="noopener" '
        f'title="{label_attr}" aria-label="{label_attr}">'
        f"{icon}"
        "</a>"
    )


def _open_in_tables_link(families: tuple[str, ...]) -> str:
    """Anchor(s) that open tables.html targeting the cells matching *families*."""
    if len(families) == 2:
        first, second = (_tables_letter_name(f) for f in families)
        return _tables_anchor(
            [("letter", second), ("col", first)],
            f"Open ·{first}·{second}·… in tables.html",
            _OPEN_IN_TABLES_FIRST_TWO_ICON,
        ) + _tables_anchor(
            [("letter", first), ("row", second)],
            f"Open ·…·{first}·{second} in tables.html",
            _OPEN_IN_TABLES_LAST_TWO_ICON,
        )
    if len(families) != 3:
        return ""
    first, middle, third = (_tables_letter_name(f) for f in families)
    return _tables_anchor(
        [("letter", middle), ("col", first), ("row", third)],
        f"Open ·{first}·{middle}·{third} in tables.html",
        _OPEN_IN_TABLES_ICON,
    )


_COPY_BUTTON_HTML = (
    '<button type="button" class="copy-codepoints"'
    ' title="Copy prompt preamble to clipboard"'
    ' aria-label="Copy prompt preamble to clipboard">'
    '<img src="icons/copy.svg" alt="" width="12" height="12">'
    '<span class="copied-toast">Copied!</span>'
    "</button>"
)


# ---------------------------------------------------------------------------
# Row + section renderers.
# ---------------------------------------------------------------------------


def _format_leak_label(leak: Leak, example: IsolationLeakExample) -> tuple[str, str]:
    cp_map = _family_to_codepoint()
    families = example.families
    label_parts = [_short_label(f) for f in families]
    label_parts.insert(example.break_index + 1, "|")
    label = " ".join(label_parts)
    diff_parts: list[str] = []
    if leak.left_changed:
        diff_parts.append(f"{leak.isolated_left} → {leak.left_chosen}")
    if leak.right_changed:
        diff_parts.append(f"{leak.isolated_right} → {leak.right_chosen}")
    diff = "; ".join(diff_parts)
    code = " ".join(f"U+{cp_map[f]:04X}" for f in families)
    return f"{label} ({diff})", code


# The preset triage verdicts, as (button label, statement that lands in the textarea) pairs. The free-text input alongside them covers the "actually we should change something else" case (e.g. "these letters should never join; update the YAML").
_VERDICT_CHOICES: tuple[tuple[str, str], ...] = (
    ("broken", "in context is outright broken"),
    ("halves better", "in context is OK, but halves-shaped-separately is better"),
    ("in-context better", "in context is just better than halves-shaped-separately"),
)


def _verdict_controls(seq_label: str) -> str:
    seq_attr = html.escape(seq_label, quote=True)
    buttons = "".join(
        f'<button type="button" class="verdict-btn" data-verdict="{html.escape(statement, quote=True)}">'
        f"{html.escape(short)}</button>"
        for short, statement in _VERDICT_CHOICES
    )
    placeholder = "…or type your own (e.g. these letters should never join; update the YAML)"
    return (
        f'        <div class="verdicts" data-seq="{seq_attr}">\n'
        f"          {buttons}\n"
        f'          <input type="text" class="verdict-custom" placeholder="{html.escape(placeholder, quote=True)}" aria-label="Custom verdict for this sequence">\n'
        "        </div>\n"
    )


def _format_leak_row(
    leak: Leak,
    example: IsolationLeakExample,
    visual: str,
    with_verdicts: bool = False,
    verdict_seq: str = "",
) -> str:
    label, code = _format_leak_label(leak, example)
    cp_map = _family_to_codepoint()
    families = example.families
    (l0, l1), (r0, r1) = _shaped_input_spans(example)
    full_entities = "".join(_entity_for(cp_map[f]) for f in families)
    left_entities = "".join(_entity_for(cp_map[f]) for f in families[l0:l1])
    right_entities = "".join(_entity_for(cp_map[f]) for f in families[r0:r1])
    isolated = f'<span class="half">{left_entities}</span>' f'<span class="half">{right_entities}</span>'
    open_link = _open_in_tables_link(families)
    letters = html.escape("".join(_short_label(f) for f in families), quote=True)
    verdicts = _verdict_controls(verdict_seq or label) if with_verdicts else ""
    return (
        f'      <div class="row" data-visual="{visual}">\n'
        '        <div class="label">\n'
        f'          {open_link}<span class="visual-tag">{visual}</span>{label}\n'
        '          <div class="codepoints">'
        f'{_COPY_BUTTON_HTML}<code data-letters="{letters}">{code}</code></div>\n'
        "        </div>\n"
        f'        <div class="qs in-context">{full_entities}</div>\n'
        f'        <div class="qs isolated">{isolated}</div>\n'
        f"{verdicts}"
        "      </div>"
    )


def _leak_sort_key(item: tuple[Leak, IsolationLeakExample]) -> tuple:
    leak, example = item
    cp_map = _family_to_codepoint()
    return (
        len(example.families),
        tuple(cp_map[f] for f in example.families),
        leak.left_chosen,
        leak.right_chosen,
    )


def _isolation_leaks_section(items: list[tuple[Leak, IsolationLeakExample]], max_len: int) -> str:
    diff_items = [
        (leak, example, visual)
        for leak, example in items
        for visual in (_visual_status(example),)
        if visual == "diff"
    ]
    rows = "\n".join(_format_leak_row(leak, w, v) for leak, w, v in diff_items)
    return (
        '    <details class="collapsible isolation-leaks" open>\n'
        "      <summary><h2>Auto-generated: isolation leaks</h2></summary>\n"
        "      <p>\n"
        "        Sequences whose adjacent pair does not cursive-attach but\n"
        "        whose chosen glyphs differ when the pair is shaped together\n"
        "        versus split into independent buffers. These are the\n"
        "        cases that currently require <code>|?|</code> in\n"
        "        <code>data-expect</code>; visually inspect each row to\n"
        "        decide whether the cross-break shape change is cosmetic\n"
        "        or a real bug. Generated with"
        f" <code>--max-len {max_len}</code>.\n"
        "      </p>\n"
        "      <p>\n"
        "        Columns: the middle column shapes the whole sequence as a\n"
        "        single buffer (what you get in real text); the right\n"
        "        column splits the sequence at the leaky break into two\n"
        "        <code>display: inline-block</code> halves so HarfBuzz\n"
        "        shapes each side independently. Decide whether the\n"
        "        in-context shape is intended.\n"
        "      </p>\n"
        "      <p>\n"
        "        Only <code>diff</code> rows — where the in-context buffer\n"
        "        and the concatenation of the two independently-shaped\n"
        "        halves differ in their per-glyph (pixels, absolute origin)\n"
        "        sequence — are shown. Comparing absolute origins (not just\n"
        "        pixels) catches cursive-positioning leaks: e.g.\n"
        "        <code>qsIt</code> vs <code>qsIt.ex-y5</code> have\n"
        "        identical bitmaps but the latter's exit anchor pulls the\n"
        "        next glyph leftward via GPOS <code>curs</code>. Pure\n"
        "        glyph-name-signature changes with no visible effect (the\n"
        "        old <code>same</code> rows, typically\n"
        "        <code>after-tall</code>-style trims) are filtered out.\n"
        "      </p>\n"
        '      <div class="col-headers">\n'
        "        <span>Sequence</span>\n"
        "        <span>In context</span>\n"
        "        <span>Halves shaped separately</span>\n"
        "      </div>\n"
        f"{rows}\n"
        "    </details>"
    )


# ---------------------------------------------------------------------------
# Depth-4 leak-snapshot triage section.
# ---------------------------------------------------------------------------
#
# The everyday section above re-sweeps live at ``--max-len`` (3 by default). The depth-4 sweep that surfaces context-revealed leaks is too slow to re-run on every ``make check-html`` (~50 s), so its result is frozen in ``test/isolation-leak-snapshot.txt`` and gated by ``make test-leaks``. This section reads that committed file back and renders each approved leak in the same side-by-side layout, so the snapshot doubles as a visual triage list: every row is a known depth-4 leak, and a row whose two columns now match is one you've fixed and can re-bless out with ``make leak-snapshot``.

_SNAPSHOT_LABEL_RE = re.compile(r"^(.*?)\s*\[break\s+(\d+)\]$")


def _leak_from_snapshot_diff(diff: str) -> Leak:
    """Reconstruct a :class:`Leak` from a snapshot line's ``L a->b | R c->d`` diff. Mirrors ``tools/leak_snapshot._parse_diff``, but builds the dataclass directly."""
    isolated_left = left_chosen = isolated_right = right_chosen = ""
    for clause in diff.split(" | "):
        clause = clause.strip().lstrip("*").strip()
        if clause.startswith("L ") and "->" in clause:
            isolated_left, _, left_chosen = clause[2:].partition("->")
        elif clause.startswith("R ") and "->" in clause:
            isolated_right, _, right_chosen = clause[2:].partition("->")
    return Leak(
        left_chosen=left_chosen.strip(),
        right_chosen=right_chosen.strip(),
        isolated_left=isolated_left.strip(),
        isolated_right=isolated_right.strip(),
    )


def _example_from_snapshot_label(label: str) -> IsolationLeakExample:
    """``He Awe Thaw Ing [break 1]`` -> the families/break-index example. The label tokens are family names with the ``qs`` prefix stripped (see ``leak_snapshot._example_label``), so re-prefixing round-trips them; the swept families are always single letters, never ligatures."""
    match = _SNAPSHOT_LABEL_RE.match(label.strip())
    if not match:
        raise ValueError(f"unparseable snapshot label: {label!r}")
    families = tuple("qs" + token for token in match.group(1).split())
    return IsolationLeakExample(families=families, break_index=int(match.group(2)))


def parse_leak_snapshot(path: Path = LEAK_SNAPSHOT_PATH) -> list[tuple[Leak, IsolationLeakExample, str]]:
    """Each item is the reconstructed leak, its example, and the verbatim snapshot line. The verbatim line is what the triage UI emits as a verdict's identity, so a collected punch list `grep -F`s straight back to the signature it came from."""
    items: list[tuple[Leak, IsolationLeakExample, str]] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label, _, diff = line.partition(" :: ")
        items.append((_leak_from_snapshot_diff(diff), _example_from_snapshot_label(label), line))
    return items


def _leak_snapshot_section(items: list[tuple[Leak, IsolationLeakExample, str]]) -> str:
    rows_html: list[str] = []
    fixed = 0
    for leak, example, snapshot_line in sorted(items, key=lambda item: _leak_sort_key((item[0], item[1]))):
        # A drifted snapshot whose families no longer shape to that break would raise here; the test-leaks gate would already be red, so just skip the stale row rather than abort the whole page.
        try:
            visual = _visual_status(example)
        except RuntimeError as exc:
            print(f"skipping unreconstructable snapshot leak {example.families!r}: {exc}", file=sys.stderr)
            continue
        if visual != "diff":
            fixed += 1
        rows_html.append(
            _format_leak_row(leak, example, visual, with_verdicts=True, verdict_seq=snapshot_line)
        )
    rows = "\n".join(rows_html)
    fixed_note = (
        f" {fixed} of them now render identically across the break "
        "(<code>same</code>) — those are fixed and can be re-blessed out with "
        "<code>make leak-snapshot</code>."
        if fixed
        else ""
    )
    return (
        '    <details class="collapsible isolation-leaks leak-snapshot" open>\n'
        "      <summary><h2>Auto-generated: isolation-leak triage (depth-4 snapshot)</h2></summary>\n"
        "      <p>\n"
        "        The approved depth-4 leaks frozen in\n"
        "        <code>test/isolation-leak-snapshot.txt</code>, rendered in the\n"
        "        same side-by-side layout as the section above. This is the\n"
        "        list to triage: each row is a known leak that needs more\n"
        "        context than the everyday depth-3 sweep covers. Decide, per\n"
        "        row, whether the in-context shape is the one you want; if it\n"
        "        is, the leak is benign and the row stays approved.\n"
        f"       {fixed_note}\n"
        "      </p>\n"
        "      <p>\n"
        "        The file is regenerated with <code>make leak-snapshot</code>\n"
        "        and gated by <code>make test-leaks</code>; this section just\n"
        "        reads it back, so it costs nothing on the everyday\n"
        "        <code>make check-html</code> run and never re-runs the slow\n"
        "        sweep. Columns match the section above: middle shapes the\n"
        "        whole sequence as one buffer; right splits it at the leaky\n"
        "        break into two independently-shaped halves.\n"
        "      </p>\n"
        "      <p>\n"
        "        To triage: click a verdict button under each row (or type\n"
        "        your own in the box). Each choice adds one line — the\n"
        "        snapshot signature paired with your verdict — to the\n"
        "        collector below; <strong>Copy all verdicts</strong> puts the\n"
        "        whole list on your clipboard. Click an active button again to\n"
        "        retract it.\n"
        "      </p>\n"
        '      <div class="verdict-panel">\n'
        '        <div class="verdict-panel-controls">\n'
        '          <button type="button" class="verdict-copy-all">Copy all verdicts</button>\n'
        '          <span class="verdict-count">0 verdicts</span>\n'
        "        </div>\n"
        '        <textarea class="verdict-output" readonly rows="5"'
        ' placeholder="Click a verdict button on any row below — one line per sequence collects here, ready to copy."></textarea>\n'
        "      </div>\n"
        '      <div class="col-headers">\n'
        "        <span>Sequence</span>\n"
        "        <span>In context</span>\n"
        "        <span>Halves shaped separately</span>\n"
        "      </div>\n"
        f"{rows}\n"
        "    </details>"
    )


def _format_diff_label(diff: SequenceDiff, cp_to_family: dict[int, str]) -> str:
    return " ".join(_short_label_for_codepoint(cp, cp_to_family) for cp in diff.codepoints)


def _format_diff_summary(diff: SequenceDiff) -> str:
    """Compact one-line summary of *what* changed in this sequence's render."""
    before_names = tuple(g.name for g in diff.before)
    after_names = tuple(g.name for g in diff.after)
    parts: list[str] = []
    if before_names != after_names:
        parts.append(f"{' '.join(before_names)} → {' '.join(after_names)}")
        return "; ".join(parts)
    outline_changed = [b.name for b, a in zip(diff.before, diff.after) if b.outline_hash != a.outline_hash]
    if outline_changed:
        parts.append(f"outline: {', '.join(outline_changed)}")
    position_changed = [
        b.name
        for b, a in zip(diff.before, diff.after)
        if b.outline_hash == a.outline_hash
        and (b.x_advance, b.x_offset, b.y_offset) != (a.x_advance, a.x_offset, a.y_offset)
    ]
    if position_changed:
        parts.append(f"positions: {', '.join(position_changed)}")
    return "; ".join(parts) if parts else "(unchanged glyphs, but tuple differs)"


def _format_diff_codepoints(diff: SequenceDiff) -> str:
    return " ".join(f"U+{cp:04X}" for cp in diff.codepoints)


def _format_diff_row(diff: SequenceDiff, cp_to_family: dict[int, str]) -> str:
    label = _format_diff_label(diff, cp_to_family)
    summary = _format_diff_summary(diff)
    code = _format_diff_codepoints(diff)
    rendered = _entities(diff.text)
    letters = html.escape(
        "".join(_short_label_for_codepoint(cp, cp_to_family) for cp in diff.codepoints),
        quote=True,
    )
    return (
        '      <div class="row">\n'
        '        <div class="label">\n'
        f"          {label} ({summary})\n"
        '          <div class="codepoints">'
        f'{_COPY_BUTTON_HTML}<code data-letters="{letters}">{code}</code></div>\n'
        "        </div>\n"
        f'        <div class="qs before">{rendered}</div>\n'
        f'        <div class="qs after">{rendered}</div>\n'
        "      </div>"
    )


def _diff_sort_key(diff: SequenceDiff) -> tuple:
    return (len(diff.codepoints), diff.codepoints)


def _render_diffs_section(
    diffs: list[SequenceDiff] | None,
    cp_to_family: dict[int, str],
) -> str:
    if diffs is None:
        body = (
            '      <p class="snapshot-missing">No <code>test/before/</code>\n'
            "        snapshot present, so there's nothing to diff against.\n"
            "        Switch to your baseline branch, run\n"
            "        <code>make snapshot-before</code>, switch back, and rerun\n"
            "        <code>make check-html</code>.</p>"
        )
    elif not diffs:
        body = (
            '      <p class="snapshot-missing">No differences found across the\n'
            "        harvested corpus. Either the change is invisible at the\n"
            "        Senior-Regular level, or <code>test/before/</code> is\n"
            "        already in sync with the live build.</p>"
        )
    else:
        rows = "\n".join(_format_diff_row(d, cp_to_family) for d in diffs)
        body = (
            '      <div class="col-headers">\n'
            "        <span>Sequence (what changed)</span>\n"
            "        <span>Before (snapshot)</span>\n"
            "        <span>After (live build)</span>\n"
            "      </div>\n"
            f"{rows}"
        )
    return (
        '    <details class="collapsible render-diffs" open>\n'
        "      <summary><h2>Auto-generated: corpus render diffs</h2></summary>\n"
        "      <p>\n"
        "        Every multi-letter Quikscript run harvested from\n"
        "        <code>test/the-manual.html</code>, <code>test/index.html</code>,\n"
        "        and <code>test/extra-senior-words.html</code> whose Senior-Regular\n"
        "        render differs between <code>test/before/</code> and the live\n"
        "        build. A render is the per-glyph tuple\n"
        "        <code>(glyph name, outline hash, x_advance, x_offset, y_offset)</code>,\n"
        "        so this surfaces GSUB changes (different variant chosen), GPOS\n"
        "        changes (cursive/kerning shifts), and outline edits to same-named\n"
        "        glyphs.\n"
        "      </p>\n"
        "      <p>\n"
        "        Each row's label names the change in glyph terms (e.g.\n"
        "        <code>qsTea → qsTea.ex-y5</code>). Scan for sequences you\n"
        "        weren't intending to touch — those are the regressions.\n"
        "      </p>\n"
        f"{body}\n"
        "    </details>"
    )


def _format_failure_row(row: FailureRow, cp_to_family: dict[int, str]) -> str:
    if row.text:
        rendered_entities = "".join(_entity_for(ord(ch)) for ch in row.text)
        rendered_cell = f'<div class="qs after">{rendered_entities}</div>'
        codepoints_strip = " ".join(f"U+{ord(ch):04X}" for ch in row.text)
        label_text = " ".join(_short_label_for_codepoint(ord(ch), cp_to_family) for ch in row.text)
        letters_attr = html.escape(label_text, quote=True)
        codepoints_html = (
            '          <div class="codepoints">'
            f'{_COPY_BUTTON_HTML}<code data-letters="{letters_attr}">{codepoints_strip}</code></div>\n'
        )
        label_line = f'          <div class="sequence-label">{html.escape(label_text)}</div>\n'
    else:
        rendered_cell = '<div class="qs after no-render">(could not parse a Quikscript sequence)</div>'
        codepoints_html = ""
        label_line = ""
    nodeid = html.escape(row.nodeid)
    message = html.escape(row.message)
    return (
        '      <div class="row">\n'
        '        <div class="label">\n'
        f"{label_line}"
        f'          <div class="nodeid"><code>{nodeid}</code></div>\n'
        f"{codepoints_html}"
        "        </div>\n"
        f"        {rendered_cell}\n"
        f'        <div class="message"><code>{message}</code></div>\n'
        "      </div>"
    )


def _render_failing_tests_section(rows: list[FailureRow], cp_to_family: dict[int, str]) -> str:
    if not rows:
        body = (
            '      <p class="snapshot-missing">No failing tests. <code>make test</code>\n'
            "        is green, so there's nothing here to eyeball.</p>"
        )
    else:
        formatted = "\n".join(_format_failure_row(r, cp_to_family) for r in rows)
        body = (
            '      <div class="col-headers">\n'
            "        <span>Test &amp; sequence</span>\n"
            "        <span>Rendered (live build)</span>\n"
            "        <span>Assertion message</span>\n"
            "      </div>\n"
            f"{formatted}"
        )
    return (
        '    <details class="collapsible failing-tests" open>\n'
        "      <summary><h2>Auto-generated: failing tests</h2></summary>\n"
        "      <p>\n"
        "        Every assertion line from a currently-failing pytest test in\n"
        "        <code>test/</code>. Each row renders the input families parsed\n"
        "        out of the failure message with the live build's Senior-Regular,\n"
        "        so you can eyeball whether the failure is a real regression or a\n"
        "        false positive in the test's expectations.\n"
        "      </p>\n"
        "      <p>\n"
        "        Many tests emit several sub-failures per parametrized run; each\n"
        "        of those gets its own row. Tests whose failure messages don't fit\n"
        "        the <code>[…] / qsX / qsY / […]:</code> shape still appear, but\n"
        "        without a rendered preview — the assertion text is on the right\n"
        "        regardless.\n"
        "      </p>\n"
        f"{body}\n"
        "    </details>"
    )


# ---------------------------------------------------------------------------
# Page chrome (CSS, JS, top-level template).
# ---------------------------------------------------------------------------


_PAGE_CSS = """      /*
        Pre-change snapshot. Run `make snapshot-before` on the master
        branch (or any baseline you want to compare against) to refresh
        these — the target builds the fonts and copies all six OTFs into
        test/before/, which is gitignored.
      */
      @font-face {
        font-family: "AMS Sans Senior — Before";
        src: url("before/AbbotsMortonSpaceportSansSenior-Regular.otf") format("opentype");
        font-weight: 400;
        font-style: normal;
      }
      @font-face {
        font-family: "AMS Sans Junior — Before";
        src: url("before/AbbotsMortonSpaceportSansJunior-Regular.otf") format("opentype");
        font-weight: 400;
        font-style: normal;
      }
      @font-face {
        font-family: "AMS Mono — Before";
        src: url("before/AbbotsMortonSpaceportMono-Regular.otf") format("opentype");
        font-weight: 400;
        font-style: normal;
      }

      /*
        Live-rebuild copy. `make all` rewrites these every time the
        glyph data or compiler changes.
      */
      @font-face {
        font-family: "AMS Sans Senior — After";
        src: url("AbbotsMortonSpaceportSansSenior-Regular.otf") format("opentype");
        font-weight: 400;
        font-style: normal;
      }
      @font-face {
        font-family: "AMS Sans Junior — After";
        src: url("AbbotsMortonSpaceportSansJunior-Regular.otf") format("opentype");
        font-weight: 400;
        font-style: normal;
      }
      @font-face {
        font-family: "AMS Mono — After";
        src: url("AbbotsMortonSpaceportMono-Regular.otf") format("opentype");
        font-weight: 400;
        font-style: normal;
      }

      :root {
        --font-size: 88px;
        --before-font: "AMS Sans Senior — Before", "Departure Mono", monospace;
        --after-font: "AMS Sans Senior — After", "Departure Mono", monospace;
      }

      html {
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: calc((22px + 11px) / 2);
        -webkit-font-smoothing: subpixel-antialiased;
        -moz-osx-font-smoothing: auto;
        font-smooth: auto;
      }

      body {
        max-width: 1400px;
      }

      .row {
        display: grid;
        grid-template-columns: 32ch 1fr 1fr;
        gap: 1rem;
        align-items: baseline;
        padding: 1rem 1.5rem;
        border-bottom: 1px solid light-dark(#ccc, #444);

        &:last-child {
          border-bottom: none;
        }

        .label {
          font-family: Seravek, Corbel, "Avenir Next", sans-serif;
          font-size: 16px;
          color: light-dark(#444, #ccc);
          -webkit-font-smoothing: subpixel-antialiased;
          -moz-osx-font-smoothing: auto;
          font-smooth: auto;

          code {
            font-family: Menlo, Consolas, monospace;
            font-size: 13px;
            color: light-dark(#666, #aaa);
            display: block;
            margin-top: .25rem;
          }
        }

        .qs {
          font-size: var(--font-size);
          line-height: 1.4;
          -webkit-font-smoothing: none;
          font-smooth: never;

          &.before {
            font-family: var(--before-font);
          }

          &.after {
            font-family: var(--after-font);
          }
        }
      }

      .col-headers {
        display: grid;
        grid-template-columns: 32ch 1fr 1fr;
        gap: 1rem;
        padding: .75rem 1.5rem;
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 14px;
        font-weight: 600;
        color: light-dark(#222, #eee);
        border-bottom: 2px solid light-dark(#888, #666);
        position: sticky;
        top: 0;
        background: light-dark(#fff, #2a2a2a);
      }

      details.collapsible {
        background: light-dark(#fff, #2a2a2a);
        border: 1px solid light-dark(#ccc, #444);
        border-radius: 4px;
        margin: 1.5rem 0;
      }

      details.collapsible > summary {
        cursor: pointer;
        padding: 1rem 1.5rem;
        font-size: 22px;
        font-weight: 600;
        user-select: none;
      }

      details.collapsible[open] > summary {
        border-bottom: 1px solid light-dark(#ccc, #444);
      }

      details.collapsible > summary > h2 {
        display: inline;
        margin: 0;
        font: inherit;
      }

      details.collapsible > p {
        margin: 0;
        padding: .75rem 1.5rem;
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 14px;
        color: light-dark(#444, #ccc);
        border-bottom: 1px solid light-dark(#ccc, #444);
        -webkit-font-smoothing: subpixel-antialiased;
        -moz-osx-font-smoothing: auto;
        font-smooth: auto;
      }

      .render-diffs .snapshot-missing,
      .failing-tests .snapshot-missing {
        padding: 1rem 1.5rem;
        margin: 0;
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 14px;
      }

      .isolation-leaks .qs.in-context,
      .isolation-leaks .qs.isolated,
      .render-diffs .qs.before,
      .render-diffs .qs.after,
      .failing-tests .qs.after {
        --grid-color: light-dark(rgba(0, 0, 0, 0.05), rgba(255, 255, 255, 0.06));
        background-image:
          linear-gradient(45deg, var(--grid-color) 25%, transparent 25%, transparent 75%, var(--grid-color) 75%),
          linear-gradient(45deg, var(--grid-color) 25%, transparent 25%, transparent 75%, var(--grid-color) 75%);
        background-size: 16px 16px;
        background-position: 0 5.6px, 8px 13.6px;
      }

      .failing-tests .qs.after {
        font-family: var(--after-font);
      }

      .failing-tests .qs.after.no-render {
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 14px;
        color: light-dark(#888, #888);
        background-image: none;
        font-style: italic;
        -webkit-font-smoothing: subpixel-antialiased;
      }

      .failing-tests .row .sequence-label {
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 16px;
      }

      .failing-tests .row .nodeid {
        margin-top: .25rem;
      }

      .failing-tests .row .nodeid code {
        font-family: Menlo, Consolas, monospace;
        font-size: 12px;
        color: light-dark(#666, #aaa);
        word-break: break-all;
      }

      .failing-tests .row .message {
        font-family: Menlo, Consolas, monospace;
        font-size: 12px;
        line-height: 1.45;
        color: light-dark(#444, #ccc);
        white-space: normal;
        overflow-wrap: anywhere;
        -webkit-font-smoothing: subpixel-antialiased;
      }

      .failing-tests .row .message code {
        font-family: inherit;
        font-size: inherit;
        color: inherit;
      }

      .failing-tests .row,
      .failing-tests .col-headers {
        grid-template-columns: 26ch minmax(0, 1fr) minmax(0, 1.5fr);
      }

      body:has(.failing-tests .row) {
        max-width: 1700px;
      }

      .isolation-leaks .qs.in-context,
      .isolation-leaks .qs.isolated {
        font-family: var(--after-font);
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

      .isolation-leaks .row .label .open-in-tables {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-right: .5em;
        padding: 2px 5px;
        font: inherit;
        color: light-dark(#444, #ccc);
        background: light-dark(#fafafa, #1e1e1e);
        border: 1px solid light-dark(#bbb, #555);
        border-radius: 3px;
        cursor: pointer;
        vertical-align: 1px;
        line-height: 1;
        text-decoration: none;
      }

      .isolation-leaks .row .label .open-in-tables:hover {
        background: light-dark(#eee, #333);
        border-color: light-dark(#888, #888);
      }

      .isolation-leaks .row .label .open-in-tables img {
        display: block;
        width: 12px;
        height: 12px;
      }

      .row .label .codepoints {
        display: flex;
        align-items: center;
        margin-top: .25rem;
      }

      .row .label .codepoints code {
        display: inline;
        margin-top: 0;
      }

      .row .label .copy-codepoints {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-right: .35em;
        padding: 2px 5px;
        font: inherit;
        color: light-dark(#444, #ccc);
        background: light-dark(#fafafa, #1e1e1e);
        border: 1px solid light-dark(#bbb, #555);
        border-radius: 3px;
        cursor: pointer;
        vertical-align: 1px;
        line-height: 1;
        position: relative;
      }

      .row .label .copy-codepoints:hover {
        background: light-dark(#eee, #333);
        border-color: light-dark(#888, #888);
      }

      .row .label .copy-codepoints img {
        display: block;
        width: 12px;
        height: 12px;
      }

      .row .label .copy-codepoints .copied-toast {
        display: none;
        position: absolute;
        left: 50%;
        bottom: calc(100% + 4px);
        transform: translateX(-50%);
        padding: 2px 6px;
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 11px;
        line-height: 1;
        white-space: nowrap;
        color: light-dark(#fff, #111);
        background: light-dark(#222, #ddd);
        border-radius: 3px;
        pointer-events: none;
      }

      .row .label .copy-codepoints.copied .copied-toast {
        display: block;
      }

      /* Depth-4 triage verdict UI: a sticky collector panel plus per-row verdict buttons. */
      .leak-snapshot .col-headers {
        position: static;
      }

      .leak-snapshot .verdict-panel {
        position: sticky;
        top: 0;
        z-index: 2;
        padding: .75rem 1.5rem;
        background: light-dark(#fff, #2a2a2a);
        border-bottom: 1px solid light-dark(#ccc, #444);

        .verdict-panel-controls {
          display: flex;
          align-items: center;
          gap: .75rem;
          margin-bottom: .5rem;
        }

        .verdict-count {
          font-family: Seravek, Corbel, "Avenir Next", sans-serif;
          font-size: 13px;
          color: light-dark(#666, #aaa);
        }

        textarea.verdict-output {
          width: 100%;
          box-sizing: border-box;
          font-family: Menlo, Consolas, monospace;
          font-size: 12px;
          line-height: 1.45;
          color: light-dark(#222, #eee);
          background: light-dark(#fafafa, #1e1e1e);
          border: 1px solid light-dark(#bbb, #555);
          border-radius: 4px;
          padding: .5rem;
          resize: vertical;
        }
      }

      .leak-snapshot .verdict-copy-all {
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 13px;
        padding: 4px 12px;
        color: light-dark(#444, #ccc);
        background: light-dark(#fafafa, #1e1e1e);
        border: 1px solid light-dark(#bbb, #555);
        border-radius: 4px;
        cursor: pointer;

        &:hover {
          background: light-dark(#eee, #333);
          border-color: light-dark(#888, #888);
        }
      }

      .leak-snapshot .verdict-copy-all.copied {
        color: light-dark(#fff, #062e16);
        background: light-dark(#16a34a, #4ade80);
        border-color: light-dark(#16a34a, #4ade80);
      }

      .leak-snapshot .row .verdicts {
        grid-column: 1 / -1;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: .4rem;
        margin-top: .5rem;
        padding-top: .5rem;
        border-top: 1px dashed light-dark(#ddd, #3a3a3a);
      }

      .leak-snapshot .verdict-btn {
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 13px;
        padding: 3px 10px;
        color: light-dark(#444, #ccc);
        background: light-dark(#fafafa, #1e1e1e);
        border: 1px solid light-dark(#bbb, #555);
        border-radius: 999px;
        cursor: pointer;

        &:hover {
          background: light-dark(#eee, #333);
          border-color: light-dark(#888, #888);
        }

        &.active {
          color: light-dark(#fff, #06122e);
          background: light-dark(#2563eb, #6ea8ff);
          border-color: light-dark(#2563eb, #6ea8ff);
        }
      }

      .leak-snapshot .verdict-custom {
        flex: 1 1 22ch;
        min-width: 18ch;
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 13px;
        padding: 3px 8px;
        color: light-dark(#222, #eee);
        background: light-dark(#fff, #1e1e1e);
        border: 1px solid light-dark(#bbb, #555);
        border-radius: 6px;

        &.active {
          border-color: light-dark(#2563eb, #6ea8ff);
          box-shadow: 0 0 0 1px light-dark(#2563eb, #6ea8ff);
        }
      }

      .leak-snapshot .row.has-verdict {
        background: light-dark(#eef4ff, #1b2740);
      }

      .footer {
        margin-top: 2rem;
        padding: 1rem 1.5rem;
        font-family: Seravek, Corbel, "Avenir Next", sans-serif;
        font-size: 14px;
        color: light-dark(#444, #ccc);
        border-top: 1px solid light-dark(#ccc, #444);
        -webkit-font-smoothing: subpixel-antialiased;

        code {
          font-family: Menlo, Consolas, monospace;
          font-size: 13px;
        }
      }"""


_COPY_CODEPOINTS_SCRIPT = """    <script>
      function copyCodepointQuote(button) {
        const label = button.closest('.label');
        if (!label) return;
        const code = label.querySelector('code');
        if (!code) return;
        const codepoints = code.textContent.trim();
        const letters = (code.dataset.letters || '').trim();
        const suffix = letters ? ` (${letters})` : '';
        const quote = `I'm looking at test/check.html \\u2014 specifically, ${codepoints}${suffix}. `;
        const flash = () => {
          button.classList.add('copied');
          setTimeout(() => button.classList.remove('copied'), 1200);
        };
        try {
          const result = navigator.clipboard && navigator.clipboard.writeText(quote);
          if (result && typeof result.then === 'function') {
            result.then(flash).catch((err) => console.warn('clipboard write failed', err));
          } else {
            flash();
          }
        } catch (err) {
          console.warn('clipboard write failed', err);
        }
      }
      document.addEventListener('click', (event) => {
        const button = event.target.closest('.copy-codepoints');
        if (button) copyCodepointQuote(button);
      });
    </script>"""


_VERDICT_SCRIPT = """    <script>
      (() => {
        const section = document.querySelector('.leak-snapshot');
        if (!section) return;
        const groups = Array.from(section.querySelectorAll('.verdicts'));
        const output = section.querySelector('.verdict-output');
        const count = section.querySelector('.verdict-count');
        const verdicts = new Map();

        function rebuild() {
          const lines = [];
          for (const group of groups) {
            const verdict = verdicts.get(group);
            if (verdict) lines.push(`${group.dataset.seq} => ${verdict}`);
          }
          output.value = lines.join('\\n');
          count.textContent = lines.length === 1 ? '1 verdict' : `${lines.length} verdicts`;
        }

        function apply(group, verdict, source) {
          if (verdict) verdicts.set(group, verdict);
          else verdicts.delete(group);
          for (const btn of group.querySelectorAll('.verdict-btn')) {
            btn.classList.toggle('active', btn === source);
          }
          const input = group.querySelector('.verdict-custom');
          input.classList.toggle('active', input === source && Boolean(verdict));
          const row = group.closest('.row');
          if (row) row.classList.toggle('has-verdict', verdicts.has(group));
          rebuild();
        }

        function copyText(text, button) {
          const original = button.dataset.label || button.textContent;
          button.dataset.label = original;
          const flash = () => {
            button.classList.add('copied');
            button.textContent = 'Copied!';
            setTimeout(() => {
              button.classList.remove('copied');
              button.textContent = original;
            }, 1200);
          };
          try {
            const result = navigator.clipboard && navigator.clipboard.writeText(text);
            if (result && typeof result.then === 'function') {
              result.then(flash).catch((err) => console.warn('clipboard write failed', err));
            } else {
              flash();
            }
          } catch (err) {
            console.warn('clipboard write failed', err);
          }
        }

        section.addEventListener('click', (event) => {
          const btn = event.target.closest('.verdict-btn');
          if (btn) {
            const group = btn.closest('.verdicts');
            const wasActive = btn.classList.contains('active');
            apply(group, wasActive ? '' : btn.dataset.verdict, wasActive ? null : btn);
            return;
          }
          const copyAll = event.target.closest('.verdict-copy-all');
          if (copyAll) copyText(output.value, copyAll);
        });

        section.addEventListener('input', (event) => {
          const input = event.target.closest('.verdict-custom');
          if (!input) return;
          apply(input.closest('.verdicts'), input.value.trim(), input);
        });
      })();
    </script>"""


def _render_page(
    diffs_section: str,
    leaks_section: str,
    snapshot_section: str,
    failures_section: str,
) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Check &mdash; before/after</title>
    <link rel="stylesheet" href="shared.css">
    <style>
{_PAGE_CSS}
    </style>
  </head>
  <body>
    <h1>Check &mdash; before/after</h1>
    <p>
      Side-by-side rendering harness. Auto-generated sections cover most of
      the page: one renders every assertion line from currently-failing
      pytest tests so you can eyeball false positives; one diffs every
      multi-letter Quikscript run in the test corpus between the snapshot
      under <code>test/before/</code> and the live build under
      <code>test/</code>; one lists every short sequence whose adjacent
      non-joining pair changes shape between single-buffer and split
      shaping; and one reads back the approved depth-4 leak snapshot as a
      visual triage list.
    </p>
    <p>
      Workflow:
    </p>
    <ol>
      <li>
        On the baseline you want to compare against (typically
        <code>master</code>), run <code>make snapshot-before</code>. It
        builds the fonts and copies all six OTFs into <code>test/before/</code>
        (gitignored).
      </li>
      <li>
        Make your code or YAML changes on a branch.
      </li>
      <li>
        <code>make check-html</code> rebuilds the live OTFs, runs the
        pytest suite to gather failing assertions, and regenerates this
        file end-to-end via <code>tools/build_check_html.py</code>.
      </li>
      <li>
        Reload this page. Codepoint references live in
        <code>reference/csur/index.html</code>; the family-to-codepoint
        map is in <code>postscript_glyph_names.yaml</code>.
      </li>
    </ol>

{failures_section}

{diffs_section}

{leaks_section}

{snapshot_section}

    <p class="footer">
      Snapshot stale? Switch to the baseline branch, run
      <code>make snapshot-before</code>, switch back, then run
      <code>make check-html</code>.
    </p>
{_COPY_CODEPOINTS_SCRIPT}
{_VERDICT_SCRIPT}
  </body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def build(max_len: int) -> str:
    leaks = find_leaks(max_len)
    leak_items = sorted(leaks.items(), key=_leak_sort_key)
    leaks_section = _isolation_leaks_section(leak_items, max_len)

    if LEAK_SNAPSHOT_PATH.exists():
        snapshot_section = _leak_snapshot_section(parse_leak_snapshot())
    else:
        snapshot_section = ""

    cp_to_family = _codepoint_to_family()
    if BEFORE_FONT.exists():
        diffs = sorted(find_diffs(), key=_diff_sort_key)
        diffs_section = _render_diffs_section(diffs, cp_to_family)
    else:
        diffs_section = _render_diffs_section(None, cp_to_family)

    failures = collect_test_failures()
    failure_rows = build_failure_rows(failures)
    failures_section = _render_failing_tests_section(failure_rows, cp_to_family)

    return _render_page(diffs_section, leaks_section, snapshot_section, failures_section)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-len",
        type=int,
        default=3,
        help=(
            "Maximum sequence length to enumerate for the isolation-leaks "
            "sweep (default 3 — covers every pair plus single-letter "
            "context on either side, which catches context-revealed leaks "
            "without combinatorial blowup). Increase to 4 for a slower "
            "(~30 s) but deeper sweep."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=CHECK_HTML_PATH,
        help="Output path (default test/check.html).",
    )
    args = parser.parse_args()

    output = build(args.max_len)
    args.out.write_text(output)
    out = args.out.resolve()
    try:
        rel = out.relative_to(ROOT)
        display = str(rel)
    except ValueError:
        display = str(out)
    print(f"Wrote {display}", file=sys.stderr)


if __name__ == "__main__":
    main()
