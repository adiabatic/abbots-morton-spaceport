"""The `data-expect` corpus: the pages that carry pins (`PIN_PAGES`), the HTML collector that reads each pin with its text split into runs (`DataExpectCollector`), the split of a pin's tokens among its runs (`partition_by_runs`), and the pins of a page as records (`collect_pins`).

`doc/data-expect.md` says what a pin asserts and how a page marks one pending (`PENDING_ATTRIBUTE`). The checker (`engine/validation/check.py`) checks what this module collects, for the root conftest's pin items and for the build's pin gate (`engine/pipeline/manual_pins.py`). It is a leaf: it imports nothing from `engine/pipeline/`, `engine/review/`, `tools/` or `test/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple, TypedDict

from .expect import (
    Connection,
    ExpectToken,
    PartitionedRun,
    Run,
    is_modifier_char,
    parse_expect,
    token_input_char_count,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_PAGES = ("site/index.html", "site/the-manual.html", "site/extra-senior-words.html")
EXPECT_ATTRIBUTES = ("data-expect", "data-expect-noncanonically")
# A pin the rebuild disagrees with keeps the author's text and carries this attribute, whose value says why in a few words. The checker reports such a pin as an expected failure, and as a failure once it passes.
PENDING_ATTRIBUTE = "data-expect-pending"


class _CellInfo(TypedDict):
    expect: str | None
    line: int
    stylistic_set: str | None
    pending: str | None


class PinCell(NamedTuple):
    """One element that carries an expect attribute: its whole text, the attribute's value, its line, its own `data-stylistic-set`, its runs, and its `PENDING_ATTRIBUTE` value, or None when it is not pending."""

    text: str
    expect: str | None
    line: int
    stylistic_set: str | None
    runs: list[Run]
    pending: str | None


class DataExpectCollector(HTMLParser):
    """Collect the data-expect or data-expect-noncanonically attribute of each <td>, <span>, and <dd> element, with the element's text split into runs.

    A run is a stretch of text shaped with one font and one feature set. A `force-junior` descendant starts a Junior run, and a descendant with `data-stylistic-set` starts a run with those stylistic sets. Each run carries the union of the stylistic sets of every descendant still open around it, so text inside a `force-junior` span, or after an inner set closes, keeps an enclosing descendant's sets, and an element with both `force-junior` and `data-stylistic-set` starts a Junior run with its sets. Text outside every `force-junior` descendant is Senior. Only <td>, <span>, and <dd> descendants are tracked, and a data-expect element inside another one is not collected separately.
    """

    _TAGS = {"td", "span", "dd"}

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[PinCell] = []
        self._open_tags: list[tuple[str, bool, bool, str | None]] = []
        self._cell_active = False
        self._cell_info: _CellInfo | None = None
        self._runs: list[Run] = []

    def _current_font(self) -> str:
        for _tag, _is_cell, is_junior, _inner_ss in reversed(self._open_tags):
            if is_junior:
                return "junior"
        return "senior"

    def _current_features(self) -> dict[str, bool]:
        features: dict[str, bool] = {}
        for _tag, _is_cell, _is_junior, inner_ss in self._open_tags:
            if inner_ss:
                features.update({f"ss{ss.zfill(2)}": True for ss in inner_ss.split()})
        return features

    def _start_run(self) -> None:
        run: Run = {"font": self._current_font(), "text": ""}
        features = self._current_features()
        if features:
            run["features"] = features
        self._runs.append(run)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._TAGS:
            return
        attr_dict = dict(attrs)
        expect = attr_dict.get("data-expect", attr_dict.get("data-expect-noncanonically"))
        css_class = attr_dict.get("class", "") or ""
        is_force_junior = "force-junior" in css_class.split()

        if expect is not None and not self._cell_active:
            self._cell_active = True
            self._cell_info = {
                "expect": expect,
                "line": self.getpos()[0],
                "stylistic_set": attr_dict.get("data-stylistic-set"),
                "pending": attr_dict.get(PENDING_ATTRIBUTE),
            }
            self._runs = [{"font": "senior", "text": ""}]
            self._open_tags.append((tag, True, False, None))
            return

        if self._cell_active:
            inner_ss = attr_dict.get("data-stylistic-set")
            self._open_tags.append((tag, False, is_force_junior, inner_ss))
            if is_force_junior or inner_ss:
                self._start_run()

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._TAGS or not self._open_tags:
            return
        open_tag, is_cell_start, was_force_junior, was_inner_ss = self._open_tags[-1]
        if open_tag != tag:
            return
        self._open_tags.pop()

        if not self._cell_active:
            return

        if is_cell_start:
            non_empty: list[Run] = []
            for r in self._runs:
                text = r["text"].strip()
                if not text:
                    continue
                entry: Run = {"font": r["font"], "text": text}
                features = r.get("features")
                if features:
                    entry["features"] = features
                non_empty.append(entry)
            if not non_empty:
                non_empty = [{"font": "senior", "text": ""}]
            full_text = "".join(r["text"] for r in self._runs).strip()
            assert self._cell_info is not None
            self.cells.append(
                PinCell(
                    full_text,
                    self._cell_info["expect"],
                    self._cell_info["line"],
                    self._cell_info["stylistic_set"],
                    non_empty,
                    self._cell_info["pending"],
                )
            )
            self._cell_active = False
            self._cell_info = None
            self._runs = []
        elif was_force_junior or was_inner_ss:
            self._start_run()

    def handle_data(self, data: str) -> None:
        if self._cell_active:
            self._runs[-1]["text"] += data


def partition_by_runs(
    runs: list[Run], tokens: list[ExpectToken], connections: list[Connection]
) -> list[PartitionedRun]:
    """Split `tokens` and `connections` among `runs`, returning one PartitionedRun per run.

    Each token goes to the run that holds its characters, and a variation selector goes with the token before it. A connection between tokens in different runs is dropped, because each run is shaped separately. Raises ValueError when a token straddles two runs, a run's tokens are not contiguous, or the characters and tokens don't line up.
    """
    full_text = ""
    run_for_char = []
    for run_idx, run in enumerate(runs):
        for _ in run["text"]:
            run_for_char.append(run_idx)
        full_text += run["text"]

    token_to_run = []
    char_idx = 0
    for tok in tokens:
        base = token_input_char_count(tok)
        while char_idx < len(full_text) and is_modifier_char(full_text[char_idx]):
            char_idx += 1
        if char_idx >= len(full_text):
            raise ValueError(f"Not enough input chars for token {tok!r} at char {char_idx}")
        token_run = run_for_char[char_idx]
        consumed = 0
        while consumed < base:
            if char_idx >= len(full_text):
                raise ValueError(f"Token {tok!r} overruns text at char {char_idx}")
            c = full_text[char_idx]
            if is_modifier_char(c):
                char_idx += 1
                continue
            if run_for_char[char_idx] != token_run:
                raise ValueError(f"Token {tok!r} straddles run boundary at char {char_idx}")
            char_idx += 1
            consumed += 1
        while char_idx < len(full_text) and is_modifier_char(full_text[char_idx]):
            char_idx += 1
        token_to_run.append(token_run)

    if char_idx != len(full_text):
        raise ValueError(f"Run text not fully consumed: used {char_idx} of " f"{len(full_text)} chars")

    slices: list[PartitionedRun] = []
    for run_idx, run in enumerate(runs):
        tok_indices = [i for i, r in enumerate(token_to_run) if r == run_idx]
        if not tok_indices:
            run_tokens: list[ExpectToken] = []
            run_conns: list[Connection] = []
        else:
            run_tokens = [tokens[i] for i in tok_indices]
            run_conns = []
            for i in range(len(tok_indices) - 1):
                if tok_indices[i + 1] != tok_indices[i] + 1:
                    raise ValueError(
                        f"Non-contiguous tokens in run {run_idx}: " f"{tok_indices[i]} -> {tok_indices[i+1]}"
                    )
                run_conns.append(connections[tok_indices[i]])
        sl: PartitionedRun = {
            "font": run["font"],
            "text": run["text"],
            "tokens": run_tokens,
            "connections": run_conns,
        }
        run_features = run.get("features")
        if run_features:
            sl["features"] = run_features
        slices.append(sl)
    return slices


def stylistic_set_features(stylistic_set: str | None) -> dict[str, bool]:
    """Return the features a `data-stylistic-set` value turns on: `"2 10"` gives ss02 and ss10."""
    if not stylistic_set:
        return {}
    return {f"ss{ss.zfill(2)}": True for ss in stylistic_set.split()}


@dataclass(frozen=True)
class Pin:
    """One pin as the checker reads it: where it is (`page`, `line`), its text and expect string, the features its own `data-stylistic-set` turns on, its runs, and its pending reason, or None."""

    page: str
    line: int
    text: str
    expect: str
    features: dict[str, bool]
    runs: tuple[Run, ...]
    pending: str | None

    @property
    def source(self) -> str:
        return f"{self.page}:{self.line}"


def collect_pins(page: str, html: str) -> list[Pin]:
    """Return the pins of one page's HTML, `page` being its repo-relative name, in document order, leaving out each element whose expect attribute is empty."""
    collector = DataExpectCollector()
    collector.feed(html)
    collector.close()
    pins: list[Pin] = []
    for cell in collector.cells:
        if not cell.expect or not cell.expect.strip():
            continue
        pins.append(
            Pin(
                page=page,
                line=cell.line,
                text=cell.text,
                expect=cell.expect,
                features=stylistic_set_features(cell.stylistic_set),
                runs=tuple(cell.runs),
                pending=cell.pending,
            )
        )
    return pins


def collect_pin_runs(pin: Pin) -> list[PartitionedRun]:
    """Return the pin's runs with its tokens and connections split among them (`partition_by_runs`), each run's features merged over the pin's own. Raises ValueError as `parse_expect` and `partition_by_runs` do."""
    tokens, connections = parse_expect(pin.expect)
    slices = partition_by_runs(list(pin.runs), tokens, connections)
    for sl in slices:
        merged = {**pin.features, **(sl.get("features") or {})}
        if merged:
            sl["features"] = merged
        else:
            sl.pop("features", None)
    return slices


def read_pins(root: Path = REPO_ROOT, pages: tuple[str, ...] = PIN_PAGES) -> list[Pin]:
    """Return the pins of every page in `pages` under `root`, page by page."""
    pins: list[Pin] = []
    for page in pages:
        pins += collect_pins(page, (Path(root) / page).read_text(encoding="utf-8"))
    return pins
