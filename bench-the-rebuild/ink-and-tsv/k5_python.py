"""K5 — TSV parsing. Repo baselines plus the obvious Python rewrites, so the report can separate
"Python is slow" from "this Python is written slowly".

Three kernels, all driven off real data:
  * rowmodel.Row.from_tsv over bench-the-rebuild/fixtures/baseline-rows.tsv (54,240 real rows,
    byte-identical to the body of rebuild/out/m1/baseline-default.subset.tsv.gz).
  * review.audit.load_audit over rebuild/out/m1/divergence-audit.tsv (292,098 rows, 59 MB).
  * pipeline.baseline_subset.filter_table over one decompressed rebuild/out/baseline-*.tsv
    (4,985,767 rows, 554 MB).

Every variant is timed on the parse only and verified afterwards by re-serializing the parsed rows to
the canonical TSV form and hashing that — a round trip, not a hash of the input bytes, so a variant
that skipped a field cannot pass.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from rebuild.pipeline.baseline_subset import M1_ALPHABET, filter_table  # noqa: E402
from rebuild.review.audit import AUDIT_HEADER, AuditRow, load_audit  # noqa: E402
from rebuild.validation.rowmodel import Row  # noqa: E402

ROWS_TSV = REPO / "tmp" / "perf" / "attr-overhead" / "data" / "baseline-rows.tsv"
AUDIT_TSV = REPO / "rebuild" / "out" / "m1" / "divergence-audit.tsv"
BIG_TSV = HERE / "work" / "baseline-default.tsv"


# --------------------------------------------------------------------------------------------------
# rowmodel.Row.from_tsv
# --------------------------------------------------------------------------------------------------


def rows_baseline(path: Path) -> list:
    """The repo path exactly: text-mode lines through the frozen dataclass's classmethod."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return [Row.from_tsv(line) for line in fh if line.strip() and not line.startswith("#")]


def rows_opt_dataclass(path: Path) -> list:
    """Same Row dataclass, better Python: one bytes read, split with maxsplit, list comps rather than
    generator expressions inside tuple()."""
    data = path.read_bytes()
    out = []
    append = out.append
    make = Row
    for line in data.split(b"\n"):
        if not line or line[0] == 35:
            continue
        cps, glyphs, clusters, seams, positions = line.split(b"\t", 4)
        append(
            make(
                tuple([int(cp, 16) for cp in cps.split(b":")]),
                tuple([g.decode() for g in glyphs.split(b"|")]),
                tuple([int(c) for c in clusters.split(b",")]),
                tuple([s.decode() for s in seams.split(b",")]) if seams else (),
                tuple(
                    [
                        (int(t[0]), int(t[1]), int(t[2]))
                        for t in [p.split(b",") for p in positions.split(b"|")]
                    ]
                ),
            )
        )
    return out


def rows_opt_str_tuple(path: Path) -> list:
    """Text mode, so the io layer decodes the whole file in one C call instead of a per-field
    bytes.decode(); plus maxsplit, list comps, and no per-row object."""
    text = path.read_text(encoding="utf-8", newline="")
    out = []
    append = out.append
    for line in text.split("\n"):
        if not line or line[0] == "#":
            continue
        cps, glyphs, clusters, seams, positions = line.split("\t", 4)
        append(
            (
                tuple([int(cp, 16) for cp in cps.split(":")]),
                tuple(glyphs.split("|")),
                tuple([int(c) for c in clusters.split(",")]),
                tuple(seams.split(",")) if seams else (),
                tuple(
                    [
                        (int(t[0]), int(t[1]), int(t[2]))
                        for t in [p.split(",") for p in positions.split("|")]
                    ]
                ),
            )
        )
    return out


def rows_opt_tuple(path: Path) -> list:
    """No per-row object at all: the same five fields as a plain tuple. This is the measurement the
    cost model's 375.4 ns frozen-dataclass construct predicts should matter."""
    data = path.read_bytes()
    out = []
    append = out.append
    for line in data.split(b"\n"):
        if not line or line[0] == 35:
            continue
        cps, glyphs, clusters, seams, positions = line.split(b"\t", 4)
        append(
            (
                tuple([int(cp, 16) for cp in cps.split(b":")]),
                tuple([g.decode() for g in glyphs.split(b"|")]),
                tuple([int(c) for c in clusters.split(b",")]),
                tuple([s.decode() for s in seams.split(b",")]) if seams else (),
                tuple(
                    [
                        (int(t[0]), int(t[1]), int(t[2]))
                        for t in [p.split(b",") for p in positions.split(b"|")]
                    ]
                ),
            )
        )
    return out


def canon_row(fields) -> bytes:
    codepoints, glyphs, clusters, seams, positions = fields
    return (
        ":".join(f"{cp:04X}" for cp in codepoints)
        + "\t"
        + "|".join(glyphs)
        + "\t"
        + ",".join(str(c) for c in clusters)
        + "\t"
        + ",".join(seams)
        + "\t"
        + "|".join(f"{x},{y},{a}" for x, y, a in positions)
        + "\n"
    ).encode()


def rows_checksum(rows) -> str:
    digest = hashlib.sha256()
    for row in rows:
        fields = (
            (row.codepoints, row.glyphs, row.clusters, row.seams, row.positions)
            if isinstance(row, Row)
            else row
        )
        digest.update(canon_row(fields))
    return digest.hexdigest()


# --------------------------------------------------------------------------------------------------
# review.audit.load_audit
# --------------------------------------------------------------------------------------------------


def audit_opt_dataclass(path: Path) -> list:
    data = path.read_bytes()
    lines = data.split(b"\n")
    if tuple(lines[0].decode().split("\t")) != AUDIT_HEADER:
        raise ValueError("unexpected audit header")
    out = []
    append = out.append
    make = AuditRow
    for line in lines[1:]:
        if not line:
            continue
        config, codepoints, kinds, matched_entry, baseline, new = line.split(b"\t", 5)
        append(
            make(
                config.decode(),
                codepoints.decode(),
                tuple([k.decode() for k in kinds.split(b",")]),
                matched_entry.decode(),
                tuple([b.decode() for b in baseline.split(b"|")]),
                tuple([n.decode() for n in new.split(b"|")]),
            )
        )
    return out


def audit_opt_str_tuple(path: Path) -> list:
    text = path.read_text(encoding="utf-8", newline="")
    out = []
    append = out.append
    for line in text.split("\n")[1:]:
        if not line:
            continue
        config, codepoints, kinds, matched_entry, baseline, new = line.split("\t", 5)
        append(
            (
                config,
                codepoints,
                tuple(kinds.split(",")),
                matched_entry,
                tuple(baseline.split("|")),
                tuple(new.split("|")),
            )
        )
    return out


def audit_opt_tuple(path: Path) -> list:
    data = path.read_bytes()
    lines = data.split(b"\n")
    out = []
    append = out.append
    for line in lines[1:]:
        if not line:
            continue
        config, codepoints, kinds, matched_entry, baseline, new = line.split(b"\t", 5)
        append(
            (
                config.decode(),
                codepoints.decode(),
                tuple([k.decode() for k in kinds.split(b",")]),
                matched_entry.decode(),
                tuple([b.decode() for b in baseline.split(b"|")]),
                tuple([n.decode() for n in new.split(b"|")]),
            )
        )
    return out


def canon_audit(fields) -> bytes:
    config, codepoints, kinds, matched_entry, baseline, new = fields
    return (
        "\t".join((config, codepoints, ",".join(kinds), matched_entry, "|".join(baseline), "|".join(new)))
        + "\n"
    ).encode()


def audit_checksum(rows) -> str:
    digest = hashlib.sha256()
    for row in rows:
        fields = (
            (row.config, row.codepoints, row.kinds, row.matched_entry, row.baseline, row.new)
            if isinstance(row, AuditRow)
            else row
        )
        digest.update(canon_audit(fields))
    return digest.hexdigest()


# --------------------------------------------------------------------------------------------------
# baseline_subset.filter_table
# --------------------------------------------------------------------------------------------------

ALPHABET_TOKENS = frozenset(f"{cp:04X}".encode() for cp in M1_ALPHABET)


def filter_opt(source: Path, destination: Path) -> int:
    """Bytes throughout, one field split, and the alphabet test done as a byte-token set membership
    rather than int(token, 16) — the hex text is already canonical four-digit uppercase, so parsing it
    to compare against a set of ints is pure waste."""
    data = source.read_bytes()
    kept = 0
    out = []
    append = out.append
    tokens = ALPHABET_TOKENS
    for line in data.split(b"\n"):
        if not line:
            continue
        if line[0] == 35:
            append(line)
            continue
        field = line[: line.index(b"\t")]
        ok = True
        for token in field.split(b":"):
            if token not in tokens:
                ok = False
                break
        if ok:
            append(line)
            kept += 1
    destination.write_bytes(b"\n".join(out) + b"\n")
    return kept


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def warm(path: Path) -> int:
    """Page-cache warm-up. Without it the first rep of a 59 MB or 554 MB file is a disk read and the
    min-of-reps figure swings by up to 2x between runs."""
    total = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            total += len(block)
    return total


def _best(fn, reps):
    best = None
    out = None
    for _ in range(reps):
        t = time.perf_counter()
        out = fn()
        elapsed = time.perf_counter() - t
        best = elapsed if best is None else min(best, elapsed)
    return best, out


def main() -> None:
    result: dict = {}
    work = HERE / "work"
    work.mkdir(exist_ok=True)

    warm(ROWS_TSV)
    warm(AUDIT_TSV)
    if BIG_TSV.exists():
        warm(BIG_TSV)

    # --- Row.from_tsv ------------------------------------------------------------------------------
    n_reps = 5
    variants = {
        "python_repo_baseline": lambda: rows_baseline(ROWS_TSV),
        "python_opt_bytes_dataclass": lambda: rows_opt_dataclass(ROWS_TSV),
        "python_opt_bytes_tuple": lambda: rows_opt_tuple(ROWS_TSV),
        "python_opt_str_tuple": lambda: rows_opt_str_tuple(ROWS_TSV),
    }
    rowmodel: dict = {}
    reference = None
    for name, fn in variants.items():
        elapsed, rows = _best(fn, n_reps)
        checksum = rows_checksum(rows)
        reference = reference or checksum
        rowmodel[name] = {
            "rows": len(rows),
            "seconds": elapsed,
            "ns_per_row": elapsed / len(rows) * 1e9,
            "checksum": checksum,
            "checksum_matches_baseline": checksum == reference,
        }
        del rows
    rowmodel["file"] = str(ROWS_TSV.relative_to(REPO))
    rowmodel["bytes"] = ROWS_TSV.stat().st_size
    result["rowmodel_from_tsv"] = rowmodel

    # --- load_audit --------------------------------------------------------------------------------
    audit: dict = {}
    reference = None
    for name, fn in {
        "python_repo_baseline": lambda: load_audit(AUDIT_TSV),
        "python_opt_bytes_dataclass": lambda: audit_opt_dataclass(AUDIT_TSV),
        "python_opt_bytes_tuple": lambda: audit_opt_tuple(AUDIT_TSV),
        "python_opt_str_tuple": lambda: audit_opt_str_tuple(AUDIT_TSV),
    }.items():
        elapsed, rows = _best(fn, 3)
        checksum = audit_checksum(rows)
        reference = reference or checksum
        audit[name] = {
            "rows": len(rows),
            "seconds": elapsed,
            "ns_per_row": elapsed / len(rows) * 1e9,
            "checksum": checksum,
            "checksum_matches_baseline": checksum == reference,
        }
        del rows
    audit["file"] = str(AUDIT_TSV.relative_to(REPO))
    audit["bytes"] = AUDIT_TSV.stat().st_size
    result["load_audit"] = audit

    # --- filter_table ------------------------------------------------------------------------------
    subset: dict = {}
    if BIG_TSV.exists():
        dest_a = work / "py-baseline.subset.tsv"
        dest_b = work / "py-opt.subset.tsv"
        elapsed, kept = _best(lambda: filter_table(BIG_TSV, dest_a, M1_ALPHABET), 3)
        subset["python_repo_baseline"] = {
            "kept": kept,
            "seconds": elapsed,
            "checksum": sha_file(dest_a),
        }
        elapsed, kept = _best(lambda: filter_opt(BIG_TSV, dest_b), 3)
        subset["python_opt_bytes"] = {
            "kept": kept,
            "seconds": elapsed,
            "checksum": sha_file(dest_b),
        }
        subset["python_opt_matches_baseline"] = (
            subset["python_repo_baseline"]["checksum"] == subset["python_opt_bytes"]["checksum"]
        )
        subset["source_rows"] = 4985767
        subset["source_bytes"] = BIG_TSV.stat().st_size
        # How much of the repo's real (gzip in, gzip out) filter_table is zlib rather than parsing.
        import gzip as _gzip

        gz = REPO / "rebuild" / "out" / "baseline-default.tsv.gz"
        t = time.perf_counter()
        raw = _gzip.open(gz, "rb").read()
        subset["inflate_seconds"] = time.perf_counter() - t
        subset["inflate_bytes"] = len(raw)
        del raw
        dest_gz = work / "py-baseline.subset.tsv.gz"
        t = time.perf_counter()
        filter_table(gz, dest_gz, M1_ALPHABET)
        subset["repo_end_to_end_gz_seconds"] = time.perf_counter() - t
    result["filter_table"] = subset

    print(json.dumps(result))


if __name__ == "__main__":
    main()
