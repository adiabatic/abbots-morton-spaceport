"""The word lists of the Manual letter survey: their file format, their checks, and the edits that fill them in.

A word list (`reference/manual-letters/words/pNN.json`) covers one page of `reference/Quikscript Manual.pdf`. Its `lines` are the handwriting lines, each with a number `n`, a baseline `base` as `[x_left, y_left, x_right, y_right]`, and `short`, the height in pixels of a Short letter on that line; a line of a passage written at a deliberate slant also carries `slanted: true`. Its `words` are the handwritten words in reading order. A word carries its letters (`qsPea`, `qsPea.half`, `qsNo.alt`), the join after each letter but the last (`b`, `x`, `t`, `6`, `+` for a pair the font ligates, `|` for none), the `line` it sits on, its horizontal extent `x`, and `cuts`, one x between each pair of neighboring letters. An optional `base` overrides the line's baseline y for that word. Coordinates are pixels of the page's embedded scan, origin at the top left.

`tools/manual_letters_prefill.py` writes the lists from the HTML transcription and `tools/manual_letters.py` reads them; this module imports nothing outside the standard library so both can use it.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SURVEY = ROOT / "reference" / "manual-letters"
WORDS_DIR = SURVEY / "words"
LOCKS_DIR = ROOT / "tmp" / "manual-letters" / "locks"

FORMS = ("full", "half", "alt")
JOIN_TEXT = {"|": "|", "+": "+", "b": "~b~", "x": "~x~", "t": "~t~", "6": "~6~"}


def family_order() -> list[str]:
    """Return the Quikscript families in code-point order, read from `doc/glyph-names.md`."""
    order = []
    for row in (ROOT / "doc" / "glyph-names.md").read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) == 3 and cells[2].startswith("qs"):
            order.append(cells[2])
    return order


def split_form(letter: str) -> tuple[str, str]:
    family, _, form = letter.partition(".")
    return family, form or "full"


def page_path(page: int) -> Path:
    return WORDS_DIR / f"p{page:02d}.json"


def load_page(page: int) -> dict[str, Any]:
    return json.loads(page_path(page).read_text(encoding="utf-8"))


def all_pages() -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(WORDS_DIR.glob("p*.json"))]


def dump_page(data: dict[str, Any]) -> str:
    """Serialize one page with a line record or a word per text line, so a position edit is a one-line diff."""
    lines = ",\n".join(
        json.dumps(line, ensure_ascii=False) for line in sorted(data["lines"], key=lambda line: line["n"])
    )
    words = ",\n".join(json.dumps(word, ensure_ascii=False) for word in data["words"])
    return f'{{\n"page": {data["page"]},\n"lines": [\n{lines}\n],\n"words": [\n{words}\n]\n}}\n'


def save_page(data: dict[str, Any]) -> None:
    """Write a page's word list by replacing the file whole, so a concurrent reader never sees half of one."""
    WORDS_DIR.mkdir(parents=True, exist_ok=True)
    path = page_path(data["page"])
    scratch = path.with_suffix(f".{os.getpid()}.tmp")
    scratch.write_text(dump_page(data), encoding="utf-8")
    os.replace(scratch, path)


@contextmanager
def editing(page: int) -> Iterator[dict[str, Any]]:
    """Yield a page's word list and save it on exit, holding a lock so two processes editing one page take turns."""
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCKS_DIR / f"p{page:02d}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load_page(page)
        yield data
        save_page(data)


def set_line(page: int, n: int, base: list[int], short: int) -> None:
    with editing(page) as data:
        kept = [line for line in data["lines"] if line["n"] != n]
        old = next((line for line in data["lines"] if line["n"] == n), {})
        data["lines"] = [*kept, {**old, "n": n, "base": base, "short": short}]


def set_slanted(page: int, numbers: list[int], slanted: bool) -> None:
    """Mark the numbered lines of a page as written at a deliberate slant, or as upright."""
    with editing(page) as data:
        lines = {line["n"]: line for line in data["lines"]}
        for n in numbers:
            lines[n].pop("slanted", None)
            if slanted:
                lines[n]["slanted"] = True


def set_word(word_id: str, **fields: Any) -> None:
    """Overwrite the named fields of one word."""
    with editing(int(word_id[1:3])) as data:
        word = next(w for w in data["words"] if w["id"] == word_id)
        word.update(fields)


def check(pages: list[dict[str, Any]]) -> list[str]:
    """Return every structural fault in the word lists."""
    faults = []
    known = set(family_order())
    for data in pages:
        lines = {line["n"] for line in data["lines"]}
        for word in data["words"]:
            where = word["id"]
            count = len(word["letters"])
            for letter in word["letters"]:
                family, form = split_form(letter)
                if family not in known or form not in FORMS:
                    faults.append(f"{where}: unknown letter {letter!r}")
            if len(word["joins"]) != count - 1 or any(j not in JOIN_TEXT for j in word["joins"]):
                faults.append(f"{where}: joins {word['joins']} do not fit {count} letters")
            if word["x"] is None:
                continue
            if word["line"] not in lines:
                faults.append(f"{where}: line {word['line']!r} is not in the page's lines")
            edges = [word["x"][0], *word["cuts"], word["x"][1]]
            if len(word["cuts"]) != count - 1:
                faults.append(f"{where}: {len(word['cuts'])} cuts for {count} letters")
            if any(b <= a for a, b in zip(edges, edges[1:])):
                faults.append(f"{where}: x and cuts are not increasing: {edges}")
    return faults


def unplaced(pages: list[dict[str, Any]]) -> list[str]:
    """Return the id of every word with neither a position nor a note saying why it has none."""
    return [word["id"] for data in pages for word in data["words"] if word["x"] is None and not word["note"]]
