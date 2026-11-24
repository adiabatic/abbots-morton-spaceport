"""Write `rebuild/review-rs/src/pyfmt/unicode_tables.rs`, the code point ranges this CPython's `str.isprintable`, `str.isspace`, `str.isdigit` and `str.isdecimal` accept, which the review crate reads to write `repr` and to port the corpus build's string handling as CPython runs it. With `--check` it writes nothing and fails when the file differs from what it would write, as it does after a Python upgrade that moves `unicodedata.unidata_version`.

Run as: uv run python -m rebuild.tools.unicode_tables [--check]
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNICODE_TABLES = REPO_ROOT / "rebuild" / "review-rs" / "src" / "pyfmt" / "unicode_tables.rs"
# Each table's Rust name, the `str` predicate it holds, and what its doc comment says it is.
UNICODE_PREDICATES = (
    ("PRINTABLE", "isprintable", "`str.isprintable`"),
    ("SPACE", "isspace", "`str.isspace`, behind `strip`, `split` and `re`'s `\\s`"),
    ("DIGIT", "isdigit", "`str.isdigit`"),
    ("DECIMAL", "isdecimal", "`str.isdecimal`, the digits `int` reads in any script,"),
)


def _predicate_ranges(predicate: Callable[[str], bool]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = None
    for point in range(sys.maxunicode + 2):
        if point <= sys.maxunicode and predicate(chr(point)):
            if start is None:
                start = point
        elif start is not None:
            ranges.append((start, point - 1))
            start = None
    return ranges


def _check_decimal_runs(ranges: Sequence[tuple[int, int]]) -> None:
    """Raise ValueError unless every `str.isdecimal` range is whole runs of ten digits from zero, the premise `pyfmt::decimal_value` reads a digit's value from."""
    for start, end in ranges:
        for point in range(start, end + 1):
            if unicodedata.decimal(chr(point)) != (point - start) % 10:
                raise ValueError(f"U+{point:04X} breaks the runs of ten decimal digits from U+{start:04X}")


def unicode_tables_text() -> str:
    """The text of `UNICODE_TABLES` for this CPython: each predicate's inclusive code point ranges in ascending order, one per line under `#[rustfmt::skip]`."""
    lines = [
        "//! The pinned CPython's `str` predicates as code point ranges, for `repr` and the ported string handling ([`super::is_printable`], [`super::is_space`], [`super::is_digit`], [`super::decimal_value`]). `uv run python -m rebuild.tools.unicode_tables` generates this file and `--check` fails when it is stale; it is never edited by hand.",
        "",
        "/// The Unicode version of the CPython the tables come from (`unicodedata.unidata_version`).",
        f'pub const UNICODE_VERSION: &str = "{unicodedata.unidata_version}";',
    ]
    for name, method, what in UNICODE_PREDICATES:
        ranges = _predicate_ranges(getattr(str, method))
        if method == "isdecimal":
            _check_decimal_runs(ranges)
        lines += [
            "",
            f"/// The code points {what} accepts, as inclusive ranges in ascending order.",
            "#[rustfmt::skip]",
            f"pub static {name}: &[(u32, u32)] = &[",
            *(f"    (0x{start:04X}, 0x{end:04X})," for start, end in ranges),
            "];",
        ]
    return "\n".join(lines) + "\n"


def unicode_tables(check: bool) -> bool:
    """Write `UNICODE_TABLES`, or with `check` report whether it holds what this CPython would write, and return whether it does."""
    text = unicode_tables_text()
    if not check:
        UNICODE_TABLES.write_text(text, encoding="utf-8")
        print(f"wrote {UNICODE_TABLES.relative_to(REPO_ROOT)}", file=sys.stderr)
        return True
    current = UNICODE_TABLES.read_text(encoding="utf-8") if UNICODE_TABLES.is_file() else None
    if current != text:
        print(
            f"{UNICODE_TABLES.relative_to(REPO_ROOT)} is not what this CPython (Unicode "
            f"{unicodedata.unidata_version}) gives; `uv run python -m rebuild.tools.unicode_tables` rewrites it",
            file=sys.stderr,
        )
        return False
    print(f"{UNICODE_TABLES.relative_to(REPO_ROOT)} is current", file=sys.stderr)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the review crate's str predicate tables.")
    parser.add_argument("--check", action="store_true", help="fail when the tables are stale")
    args = parser.parse_args(argv)
    return 0 if unicode_tables(args.check) else 1


if __name__ == "__main__":
    raise SystemExit(main())
