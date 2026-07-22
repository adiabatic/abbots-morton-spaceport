"""One-time streaming filter of the baseline tables to the M1 sub-alphabet (M1-PLAN section 5, Group 3).

Streams each `rebuild/out/baseline-<config>.tsv.gz` once via `rebuild.validation.rowmodel.iter_rows`, keeps rows whose codepoints are a subset of the M1 alphabet, and writes `rebuild/out/m1/baseline-<config>.subset.tsv.gz` preserving the header lines and the canonical (length, codepoints) row order. The same filter runs over `equivalence-triage.tsv` into `rebuild/out/m1/triage.subset.tsv`.

Run as: uv run python -m rebuild.pipeline.baseline_subset
"""

from __future__ import annotations

import gzip
from pathlib import Path

from rebuild.validation.rowmodel import open_table

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = REPO_ROOT / "rebuild" / "out"
OUT_DIR = BASELINE_DIR / "m1"

M1_ALPHABET = frozenset(
    {
        0x0020,
        0x00B7,
        0x200C,
        0xE650,
        0xE652,
        0xE653,
        0xE658,
        0xE665,
        0xE666,
        0xE667,
        0xE670,
        0xE676,
        0xE679,
        0xE67A,
    }
)


def _codepoints_in_alphabet(field: str, alphabet: frozenset[int]) -> bool:
    try:
        return all(int(token, 16) in alphabet for token in field.split(":"))
    except ValueError:
        return False


def filter_table(source: Path, destination: Path, alphabet: frozenset[int] = M1_ALPHABET) -> int:
    """Filter one baseline table (header lines preserved verbatim); returns the kept-row count."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    opener = gzip.open if destination.suffix == ".gz" else open
    with open_table(source) as reader, opener(destination, "wt", encoding="utf-8", newline="") as writer:
        for line in reader:
            if line.startswith("#"):
                writer.write(line)
                continue
            if not line.strip():
                continue
            codepoints = line.split("\t", 1)[0]
            if _codepoints_in_alphabet(codepoints, alphabet):
                writer.write(line)
                kept += 1
    return kept


def filter_triage(source: Path, destination: Path, alphabet: frozenset[int] = M1_ALPHABET) -> int:
    """Filter the equivalence-triage TSV (codepoints in the third column); returns the kept-row count."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with open_table(source) as reader, open(destination, "w", encoding="utf-8", newline="") as writer:
        for index, line in enumerate(reader):
            if index == 0 or line.startswith("#"):
                writer.write(line)
                continue
            fields = line.split("\t")
            if len(fields) > 2 and _codepoints_in_alphabet(fields[2], alphabet):
                writer.write(line)
                kept += 1
    return kept


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for source in sorted(BASELINE_DIR.glob("baseline-*.tsv.gz")):
        config = source.name[len("baseline-") : -len(".tsv.gz")]
        destination = OUT_DIR / f"baseline-{config}.subset.tsv.gz"
        kept = filter_table(source, destination)
        print(f"{source.name}: kept {kept} rows -> {destination}")
    triage = BASELINE_DIR / "equivalence-triage.tsv"
    if triage.exists():
        kept = filter_triage(triage, OUT_DIR / "triage.subset.tsv")
        print(f"{triage.name}: kept {kept} rows -> {OUT_DIR / 'triage.subset.tsv'}")


if __name__ == "__main__":
    main()
