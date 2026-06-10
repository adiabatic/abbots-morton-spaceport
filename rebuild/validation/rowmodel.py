"""Row model and table I/O for the §13.1 baseline, per rebuild/BASELINE-PLAN.md §3.

This is the validation suite's implementation of the plan's shared row contract. The TSV format itself, not this Python class, is the cross-implementer interface: any table the extractor writes per plan §3 parses here, and any Row this module serializes is byte-identical to the extractor's serialization of the same shaping outcome.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

QUIKSCRIPT_RUNES = tuple(range(0xE650, 0xE66D)) + tuple(range(0xE670, 0xE67F))
SPACE = 0x0020
NAMER_DOT = 0x00B7
ZWNJ = 0x200C
ALPHABET = tuple(sorted((SPACE, NAMER_DOT, ZWNJ) + QUIKSCRIPT_RUNES))
ALPHABET_SET = frozenset(ALPHABET)
BOUNDARY_CODEPOINTS = frozenset({SPACE, ZWNJ})

CONFIGS: dict[str, dict[str, bool]] = {
    "default": {},
    "ss02": {"ss02": True},
    "ss03": {"ss03": True},
    "ss04": {"ss04": True},
    "ss05": {"ss05": True},
    "ss06": {"ss06": True},
    "ss07": {"ss07": True},
    "ss10": {"ss10": True},
    "ss02+ss03": {"ss02": True, "ss03": True},
    "ss06+ss07": {"ss06": True, "ss07": True},
    "ss02+ss03+ss05": {"ss02": True, "ss03": True, "ss05": True},
}


def config_token_for_features(features: dict[str, bool] | None) -> str | None:
    """Return the plan §5 config token for a feature dict, or None when the configuration is outside the covered eleven."""
    enabled = sorted(name for name, on in (features or {}).items() if on)
    token = "+".join(enabled) if enabled else "default"
    return token if token in CONFIGS else None


@dataclass(frozen=True)
class Row:
    codepoints: tuple[int, ...]
    glyphs: tuple[str, ...]
    clusters: tuple[int, ...]
    seams: tuple[str, ...]
    positions: tuple[tuple[int, int, int], ...]

    def to_tsv(self) -> str:
        return "\t".join(
            (
                ":".join(f"{cp:04X}" for cp in self.codepoints),
                "|".join(self.glyphs),
                ",".join(str(c) for c in self.clusters),
                ",".join(self.seams),
                "|".join(f"{x},{y},{a}" for x, y, a in self.positions),
            )
        )

    @classmethod
    def from_tsv(cls, line: str) -> "Row":
        cps, glyphs, clusters, seams, positions = line.rstrip("\n").split("\t")
        return cls(
            codepoints=tuple(int(cp, 16) for cp in cps.split(":")),
            glyphs=tuple(glyphs.split("|")),
            clusters=tuple(int(c) for c in clusters.split(",")),
            seams=tuple(seams.split(",")) if seams else (),
            positions=tuple(
                tuple(int(v) for v in triple.split(","))  # type: ignore[misc]
                for triple in positions.split("|")
            ),
        )

    @property
    def text(self) -> str:
        return "".join(chr(cp) for cp in self.codepoints)


def row_sort_key(row: Row) -> tuple[int, tuple[int, ...]]:
    return (len(row.codepoints), row.codepoints)


def format_codepoints(codepoints: tuple[int, ...]) -> str:
    return ":".join(f"{cp:04X}" for cp in codepoints)


def open_table(path: Path | str) -> IO[str]:
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


def read_header(path: Path | str) -> dict[str, str]:
    """Parse the plan §3 leading comment lines into a key → value dict; the version line lands under "tool"."""
    header: dict[str, str] = {}
    with open_table(path) as fh:
        for line in fh:
            if not line.startswith("# "):
                break
            body = line[2:].rstrip("\n")
            if ":" in body:
                key, _, value = body.partition(":")
                header[key.strip()] = value.strip()
            else:
                header.setdefault("tool", body)
    return header


def header_config_token(header: dict[str, str]) -> str:
    config = header.get("config", "")
    if not config:
        raise ValueError("baseline table header has no '# config:' line")
    return config.split()[0]


def iter_rows(path: Path | str) -> Iterator[Row]:
    with open_table(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            yield Row.from_tsv(line)


def iter_line_chunks(path: Path | str, chunk_size: int, limit: int | None = None) -> Iterator[list[str]]:
    """Yield data lines (header and blank lines dropped) in chunks, preserving file order so parallel consumers stay deterministic."""
    chunk: list[str] = []
    seen = 0
    with open_table(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            chunk.append(line)
            seen += 1
            if limit is not None and seen >= limit:
                break
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
    if chunk:
        yield chunk
