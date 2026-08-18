"""Streaming filter of the baseline tables to the M1 sub-alphabet (M1-PLAN section 5, Group 3).

Streams each `rebuild/out/baseline-<config>.tsv.gz` once via `rebuild.validation.rowmodel.iter_rows`, keeps rows whose codepoints are a subset of the M1 alphabet, and writes `rebuild/out/m1/baseline-<config>.subset.tsv.gz` preserving the header lines and the canonical (length, codepoints) row order. The same filter runs over `equivalence-triage.tsv` into `rebuild/out/m1/triage.subset.tsv`.

run_m1 calls ensure_fresh() before its gates, so an M1_ALPHABET edit can never feed the oracle stale subset tables: subset_stamp.json records a key over the alphabet, the source tables, and this module, plus each output's content hash, and the refilter is skipped only when the key matches and the outputs on disk are exactly the stamped set with the stamped bytes — a truncated table, an edited table, or an orphan left by a vanished source all read as stale, and refresh() prunes orphans. Subset gzip members are written with mtime=0 so refiltering unchanged sources reproduces each table byte for byte.

Run by hand (unconditional refilter) as: uv run python -m rebuild.pipeline.baseline_subset
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.validation.rowmodel import open_table

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = REPO_ROOT / "rebuild" / "out"
OUT_DIR = BASELINE_DIR / "m1"
STAMP_NAME = "subset_stamp.json"
STAMP_FORMAT = "ams-baseline-subset-stamp/1"

M1_ALPHABET = frozenset(
    {
        0x0020,
        0x00B7,
        0x200C,
        0xE650,
        0xE652,
        0xE653,
        0xE658,
        0xE659,
        0xE65A,
        0xE65D,
        0xE665,
        0xE666,
        0xE667,
        0xE668,
        0xE670,
        0xE672,
        0xE675,
        0xE676,
        0xE677,
        0xE678,
        0xE679,
        0xE67A,
        0xE67B,
    }
)


def _codepoints_in_alphabet(field: str, alphabet: frozenset[int]) -> bool:
    try:
        return all(int(token, 16) in alphabet for token in field.split(":"))
    except ValueError:
        return False


def _open_writer(destination: Path):
    """gzip members carry mtime=0 so a refilter of unchanged sources is byte-identical — the run-m1 green fingerprint hashes these tables, and a timestamp-only rewrite must not move it."""
    if destination.suffix == ".gz":
        return io.TextIOWrapper(gzip.GzipFile(str(destination), "wb", mtime=0), encoding="utf-8", newline="")
    return open(destination, "w", encoding="utf-8", newline="")


def filter_table(source: Path, destination: Path, alphabet: frozenset[int] = M1_ALPHABET) -> int:
    """Filter one baseline table (header lines preserved verbatim); returns the kept-row count."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with open_table(source) as reader, _open_writer(destination) as writer:
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


def _dirs(repo_root: Path) -> tuple[Path, Path]:
    baseline_dir = Path(repo_root) / "rebuild" / "out"
    return baseline_dir, baseline_dir / "m1"


def stamp_key(repo_root: Path = REPO_ROOT) -> str:
    """The content key the stamp records: everything the subset tables are a pure function of — the alphabet, the source tables (by the same size-plus-digests proxy as fingerprint.baselines_value, so no 42MB table is ever read to answer a freshness check), the triage source when present, and this module's own bytes, so a filter-logic change refilters rather than trusting output the old code wrote."""
    baseline_dir, _ = _dirs(repo_root)
    triage = baseline_dir / "equivalence-triage.tsv"
    lines = [
        "alphabet\t" + ",".join(f"{codepoint:04X}" for codepoint in sorted(M1_ALPHABET)),
        f"baselines\t{fingerprint.baselines_value(Path(repo_root))}",
        f"triage\t{hashlib.sha256(triage.read_bytes()).hexdigest() if triage.is_file() else 'absent'}",
        f"filter_code\t{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}",
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _subset_outputs_on_disk(out_dir: Path) -> set[str]:
    names = {path.name for path in out_dir.glob("baseline-*.subset.tsv.gz")}
    if (out_dir / "triage.subset.tsv").exists():
        names.add("triage.subset.tsv")
    return names


def refresh(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Refilter every source table into rebuild/out/m1, prune any subset output no longer backed by a source, and write the stamp; returns the output names mapped to their content hashes. The key is snapshotted before the filter loop (the _settle_green discipline): a source edited mid-refilter stamps under the pre-edit key and reads as stale next check, never as fresh tables it does not describe."""
    baseline_dir, out_dir = _dirs(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = stamp_key(repo_root)
    outputs: dict[str, str] = {}
    for source in sorted(baseline_dir.glob("baseline-*.tsv.gz")):
        config = source.name[len("baseline-") : -len(".tsv.gz")]
        destination = out_dir / f"baseline-{config}.subset.tsv.gz"
        kept = filter_table(source, destination)
        print(f"{source.name}: kept {kept} rows -> {destination}")
        outputs[destination.name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    triage = baseline_dir / "equivalence-triage.tsv"
    if triage.exists():
        kept = filter_triage(triage, out_dir / "triage.subset.tsv")
        print(f"{triage.name}: kept {kept} rows -> {out_dir / 'triage.subset.tsv'}")
        outputs["triage.subset.tsv"] = hashlib.sha256(
            (out_dir / "triage.subset.tsv").read_bytes()
        ).hexdigest()
    for name in sorted(_subset_outputs_on_disk(out_dir) - outputs.keys()):
        (out_dir / name).unlink()
        print(f"pruned orphaned {name}")
    payload = {"format": STAMP_FORMAT, "key": key, "outputs": outputs}
    (out_dir / STAMP_NAME).write_text(json.dumps(payload, indent=2) + "\n")
    return outputs


def is_fresh(repo_root: Path = REPO_ROOT) -> bool:
    """Whether the stamped subset tables still describe the alphabet and sources on disk: the stamp's key matches a recomputation, the subset outputs on disk are exactly the stamped set (an orphan or a stray extra reads as stale), and every output's bytes hash to the stamped digest (a truncated or edited table reads as stale). A missing or malformed stamp of any shape — including every state predating the stamp — reads as stale, never raises."""
    _, out_dir = _dirs(repo_root)
    try:
        stamp = json.loads((out_dir / STAMP_NAME).read_text())
    except OSError, ValueError:
        return False
    if not isinstance(stamp, dict) or stamp.get("key") != stamp_key(repo_root):
        return False
    outputs = stamp.get("outputs")
    if not isinstance(outputs, dict):
        return False
    if _subset_outputs_on_disk(out_dir) != set(outputs):
        return False
    for name, digest in outputs.items():
        if not isinstance(digest, str):
            return False
        try:
            if hashlib.sha256((out_dir / name).read_bytes()).hexdigest() != digest:
                return False
        except OSError:
            return False
    return True


def ensure_fresh(repo_root: Path = REPO_ROOT) -> bool:
    """run_m1's pre-gate guard: refilter when stale, no-op when fresh. Returns whether a refilter ran."""
    if is_fresh(repo_root):
        return False
    refresh(repo_root)
    return True


def main() -> None:
    refresh(REPO_ROOT)


if __name__ == "__main__":
    main()
