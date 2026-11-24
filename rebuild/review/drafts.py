"""What the review build hands the crate's verdict-export drafters (rebuild/REVIEW-PLAN.md §4.3) from Python. An approval drafts a whole-word data-expect pin, checked with `test_shaping.parse_expect`, a rejection the smallest one-line record that reverses the change, and "fine either way" an any-of record with both behaviors as full expect strings; the review crate drafts all three. A pin draft's `attribute` and `duplicate_of` come from the site corpus's pinned cells (`corpus_index_from_pages`), and its expect string from the letter names test/test_shaping.py maps each character to (`_import_test_shaping`), both of which cross in the units init (`units_exec.write_init`)."""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

from rebuild.validation.rowmodel import config_token_for_features

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


_test_shaping: Any = None


def _import_test_shaping() -> Any:
    global _test_shaping
    if _test_shaping is None:
        test_dir = str(REPO_ROOT / "test")
        if test_dir not in sys.path:
            sys.path.insert(0, test_dir)
        import test_shaping

        _test_shaping = test_shaping
    return _test_shaping


class _PinCollector(HTMLParser):
    """A data-expect cell scanner for duplicate detection: records (text content, attribute, cell stylistic set, line) for every td, span, or dd carrying either expect attribute. It is coarser than the test suite's run-aware collector because duplicate detection needs only the text and the cell's feature context."""

    _TAGS = {"td", "span", "dd"}

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[tuple[str, str, str | None, int]] = []
        self._stack: list[tuple[str, bool]] = []
        self._depth_active = 0
        self._text: list[str] = []
        self._attribute = ""
        self._stylistic_set: str | None = None
        self._line = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._TAGS:
            return
        attr_dict = dict(attrs)
        has_pin = "data-expect" in attr_dict or "data-expect-noncanonically" in attr_dict
        if has_pin and self._depth_active == 0:
            self._attribute = "data-expect" if "data-expect" in attr_dict else "data-expect-noncanonically"
            self._stylistic_set = attr_dict.get("data-stylistic-set")
            self._line = self.getpos()[0]
            self._text = []
            self._stack.append((tag, True))
            self._depth_active = 1
        elif self._depth_active:
            self._stack.append((tag, False))
            self._depth_active += 1

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._TAGS or not self._depth_active or not self._stack:
            return
        open_tag, is_cell = self._stack.pop()
        self._depth_active -= 1
        if open_tag == tag and is_cell and self._depth_active == 0:
            text = "".join(self._text).strip()
            if text:
                self.cells.append((text, self._attribute, self._stylistic_set, self._line))

    def handle_data(self, data: str) -> None:
        if self._depth_active:
            self._text.append(data)


def corpus_index_from_pages(pages: Mapping[str, bytes | None]) -> dict:
    """(text, config token) → {"source": "file:line", "attribute": ...} for every pinned cell of `pages`, the bytes `fingerprint.read_site_corpus` read, keeping the first cell for each key in the mapping's order. A page that does not exist (None) contributes nothing."""
    index: dict[tuple[str, str], dict] = {}
    for rel, page in pages.items():
        if page is None:
            continue
        collector = _PinCollector()
        collector.feed(page.decode("utf-8"))
        for text, attribute, stylistic_set, line in collector.cells:
            features = {f"ss{ss.zfill(2)}": True for ss in stylistic_set.split()} if stylistic_set else {}
            token = config_token_for_features(features) or "non-covered"
            index.setdefault((text, token), {"source": f"{rel}:{line}", "attribute": attribute})
    return index
