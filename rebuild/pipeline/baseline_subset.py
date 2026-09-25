"""Filter the baseline tables to the M1 alphabet (M1-PLAN section 5, Group 3).

Streams each `rebuild/out/baseline-<config>.tsv.gz` once through `rebuild.validation.rowmodel.open_table`, keeps the rows whose code points are all in `M1_ALPHABET`, and writes `rebuild/out/m1/baseline-<config>.subset.tsv.gz` with the header lines and the source's (length, codepoints) row order unchanged.

Each refilter also checks two facts that only a refilter can change. First, every `DEFAULT_COVERED_CONFIGS` sub-table must be row-identical to the `IDENTITY_REFERENCE` sub-table, because the acceptance gate covers ss06, ss07 and ss06+ss07 by running default alone. The check compares the digests the filter pass computes over the kept rows, so no table is read a second time. A mismatch or a missing table raises `SubsetIdentityError` before the stamp is written, so the tables are never stamped fresh and every later run fails the same way. Second, `refresh` writes the distinct old glyph names of each configuration's kept rows to `subset-names.json`. The oracle's alias-completeness check reads that file instead of streaming every subset row, which keeps the check cheap on the `--gates-only` path.

`ensure_fresh` checks a third fact on every call, fresh or stale: that each source table was extracted from the site font on disk. `make all` rewrites that font without changing any key this module stamps, so only a check that runs on every call can catch a rebuilt font. A header whose `font_sha256` matches the font on disk passes. A version bump's `make all` rewrites only the font's `head` and `name` tables, so a header whose digest does not match still passes when the font it was extracted from and the font on disk are identical outside those two tables (`fingerprint.font_content_digest`). The header records only the raw digest, so this module records the extraction font's `head`- and `name`-blind digest in `rebuild/out/baseline-font-projections.json`, keyed by font path and raw digest, on each call where every table passes. A machine that never checked the tables before the font changed has no such entry and fails with the re-extract remedy.

`run_m1` calls `ensure_fresh` before its gates, so an `M1_ALPHABET` edit cannot feed the oracle stale subset tables. `subset_stamp.json` records a key over the alphabet, the source tables, and this module's code, plus each output's content hash, each output's kept-row count (see `subset_row_counts`), and the names sidecar's hash. The refilter is skipped only when the key matches and the outputs on disk are the stamped set with the stamped bytes. A truncated or edited table, a missing or edited names sidecar, and an orphan left by a removed source all read as stale, and `refresh` deletes orphans. Subset gzip members are written with mtime=0, so refiltering unchanged sources reproduces each table byte for byte.

Run by hand (unconditional refilter) as: uv run python -m rebuild.pipeline.baseline_subset
"""

from __future__ import annotations

import gzip
import hashlib
import io
import itertools
import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from rebuild.pipeline import fingerprint
from rebuild.validation.rowmodel import open_table, read_header

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = REPO_ROOT / "rebuild" / "out"
OUT_DIR = BASELINE_DIR / "m1"
STAMP_NAME = "subset_stamp.json"
STAMP_FORMAT = "ams-baseline-subset-stamp/2"
NAMES_NAME = "subset-names.json"
NAMES_FORMAT = "ams-baseline-subset-names/1"
FONT_PROJECTION_SIDECAR = "baseline-font-projections.json"
FONT_PROJECTION_FORMAT = "ams-baseline-font-projections/1"
DEFAULT_COVERED_CONFIGS = ("ss06", "ss07", "ss06+ss07")
IDENTITY_REFERENCE = "default"

M1_ALPHABET = frozenset(
    {
        0x0020,
        0x00B7,
        0x200C,
        0xE650,
        0xE651,
        0xE652,
        0xE653,
        0xE654,
        0xE655,
        0xE656,
        0xE657,
        0xE658,
        0xE659,
        0xE65A,
        0xE65B,
        0xE65C,
        0xE65D,
        0xE65E,
        0xE65F,
        0xE660,
        0xE665,
        0xE666,
        0xE667,
        0xE668,
        0xE670,
        0xE672,
        0xE673,
        0xE674,
        0xE675,
        0xE676,
        0xE677,
        0xE678,
        0xE679,
        0xE67A,
        0xE67B,
        0xE67E,
    }
)

_IDENTITY_REMEDY = "the acceptance gate covers it by running default alone, which holds only while the two filter to the same rows; if it has genuinely diverged, add it to ACCEPTANCE_CONFIGS in rebuild/pipeline/conform.py (what the ·Owe migration needs, BASELINE-PLAN section 5) and drop it from DEFAULT_COVERED_CONFIGS here"

_EXTRACT_REMEDY = "re-extract with `uv run python -m rebuild.baseline.cli extract --all --out rebuild/out` then `uv run python -m rebuild.baseline.cli summarize --out rebuild/out`, or rebuild the font the tables were extracted from (the header's git_sha names the commit it was built at; the site font is `make all` output)"


class SubsetIdentityError(RuntimeError):
    """Raised when a `DEFAULT_COVERED_CONFIGS` sub-table does not match the reference sub-table. It is raised before the stamp is written, so a freshness check cannot skip it."""


class BaselineProvenanceError(RuntimeError):
    """Raised when a source baseline table cannot be shown to come from the site font on disk. `ensure_fresh` checks this before its freshness check, so the oracle never compares against rows another font shaped."""


@dataclass(frozen=True)
class FilteredTable:
    """What one filter pass records about the table it wrote: the kept-row count, the old glyph names in those rows, and a digest over the kept data lines as written."""

    kept: int
    glyph_names: frozenset[str]
    rows_sha256: str


def _codepoints_in_alphabet(field: str, alphabet: frozenset[int]) -> bool:
    try:
        return all(int(token, 16) in alphabet for token in field.split(":"))
    except ValueError:
        return False


def _open_writer(destination: Path):
    """Open a text writer, gzip-compressed for a `.gz` path. Gzip members use mtime=0 so a refilter of unchanged sources is byte-identical: `run_m1_skip_fingerprint` in rebuild/tools/artifact_cycle.py hashes these tables, and a timestamp-only change would force a run_m1 rerun."""
    if destination.suffix == ".gz":
        return io.TextIOWrapper(gzip.GzipFile(str(destination), "wb", mtime=0), encoding="utf-8", newline="")
    return open(destination, "w", encoding="utf-8", newline="")


def filter_table(source: Path, destination: Path, alphabet: frozenset[int] = M1_ALPHABET) -> FilteredTable:
    """Filter one baseline table, copying its header lines unchanged, and return the kept rows' count, glyph names, and digest, all computed in the same pass that writes them."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    names: set[str] = set()
    rows = hashlib.sha256()
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
                rows.update(line.encode())
                names.update(line.split("\t", 2)[1].split("|"))
                kept += 1
    return FilteredTable(kept=kept, glyph_names=frozenset(names), rows_sha256=rows.hexdigest())


def _dirs(repo_root: Path) -> tuple[Path, Path]:
    baseline_dir = Path(repo_root) / "rebuild" / "out"
    return baseline_dir, baseline_dir / "m1"


def _subset_path(out_dir: Path, config: str) -> Path:
    return out_dir / f"baseline-{config}.subset.tsv.gz"


def stamp_key(repo_root: Path = REPO_ROOT) -> str:
    """The content key the stamp records: a hash of the alphabet, the source tables, and this module's code. The source tables enter through the size-plus-digests proxy of `fingerprint.baselines_value`, so a freshness check reads no source table. The code enters through the prose-blind `fingerprint.code_file_digest`, so a code change forces a refilter and a docstring edit does not."""
    lines = [
        "alphabet\t" + ",".join(f"{codepoint:04X}" for codepoint in sorted(M1_ALPHABET)),
        f"baselines\t{fingerprint.baselines_value(Path(repo_root))}",
        f"filter_code\t{fingerprint.code_file_digest(Path(__file__))}",
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _subset_outputs_on_disk(out_dir: Path) -> set[str]:
    return {path.name for path in out_dir.glob("baseline-*.subset.tsv.gz")}


def _data_lines(reader: Iterable[str]) -> Iterator[str]:
    return (line for line in reader if not line.startswith("#") and line.strip())


def _first_differing_row(left: Path, right: Path) -> tuple[str | None, str | None]:
    """Return the first pair of data lines that differ between two subset tables, so the error can name a row. It runs only after the digests disagree."""
    with open_table(left) as left_reader, open_table(right) as right_reader:
        for pair in itertools.zip_longest(_data_lines(left_reader), _data_lines(right_reader)):
            if pair[0] != pair[1]:
                return pair
    return None, None


def _prove_default_covered(out_dir: Path, filtered: Mapping[str, FilteredTable]) -> None:
    """Raise `SubsetIdentityError` unless every `DEFAULT_COVERED_CONFIGS` sub-table was written and has the same row digest as the `IDENTITY_REFERENCE` sub-table."""
    for config in DEFAULT_COVERED_CONFIGS:
        for name in (config, IDENTITY_REFERENCE):
            if name not in filtered:
                raise SubsetIdentityError(
                    f"subset table {name} was not written, so {config} cannot be proven row-identical to {IDENTITY_REFERENCE} — {_IDENTITY_REMEDY}"
                )
        if filtered[config].rows_sha256 == filtered[IDENTITY_REFERENCE].rows_sha256:
            continue
        first = _first_differing_row(_subset_path(out_dir, config), _subset_path(out_dir, IDENTITY_REFERENCE))
        raise SubsetIdentityError(
            f"subset table {config} is not row-identical to {IDENTITY_REFERENCE}: first differing pair {first} — {_IDENTITY_REMEDY}"
        )


def _write_subset_names(out_dir: Path, filtered: Mapping[str, FilteredTable]) -> Path:
    """Write the names sidecar, the alias check's only input: each configuration's distinct old glyph names from its kept rows, sorted."""
    payload = {
        "format": NAMES_FORMAT,
        "names": {config: sorted(table.glyph_names) for config, table in sorted(filtered.items())},
    }
    path = out_dir / NAMES_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def read_subset_names(out_dir: Path = OUT_DIR) -> dict[str, list[str]]:
    """Return the names sidecar as `{config: sorted names}`. Unlike `is_fresh`, it raises on a missing or malformed file: callers read it only after `ensure_fresh`, so a bad file means a broken invariant, not a state to refilter from."""
    path = Path(out_dir) / NAMES_NAME
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"{path} is missing — the subset tables were written by an older refilter; call baseline_subset.ensure_fresh to regenerate it"
        ) from error
    if not isinstance(payload, dict) or payload.get("format") != NAMES_FORMAT:
        raise ValueError(f"{path} is not a {NAMES_FORMAT} document")
    names = payload.get("names")
    if not isinstance(names, dict):
        raise ValueError(f"{path} carries no names mapping")
    return {str(config): list(entries) for config, entries in names.items()}


def refresh(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Refilter every source table into rebuild/out/m1, delete subset outputs that have no source, check the default-covered configurations, write the names sidecar, and write the stamp, in that order. Returns the output names mapped to their content hashes. The key is computed before filtering, so a source edited mid-refilter is stamped under the old key and reads as stale on the next check. The identity check runs before the stamp is written, so a failure leaves the outputs on disk with no stamp that matches them."""
    baseline_dir, out_dir = _dirs(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = stamp_key(repo_root)
    outputs: dict[str, str] = {}
    filtered: dict[str, FilteredTable] = {}
    for source in sorted(baseline_dir.glob("baseline-*.tsv.gz")):
        config = source.name[len("baseline-") : -len(".tsv.gz")]
        destination = _subset_path(out_dir, config)
        result = filter_table(source, destination)
        filtered[config] = result
        print(f"{source.name}: kept {result.kept} rows -> {destination}")
        outputs[destination.name] = fingerprint.file_sha256(destination)
    for name in sorted(_subset_outputs_on_disk(out_dir) - outputs.keys()):
        (out_dir / name).unlink()
        print(f"pruned orphaned {name}")
    _prove_default_covered(out_dir, filtered)
    names_path = _write_subset_names(out_dir, filtered)
    payload = {
        "format": STAMP_FORMAT,
        "key": key,
        "outputs": outputs,
        "rows": {config: table.kept for config, table in sorted(filtered.items())},
        "sidecars": {NAMES_NAME: fingerprint.file_sha256(names_path)},
    }
    (out_dir / STAMP_NAME).write_text(json.dumps(payload, indent=2) + "\n")
    return outputs


def subset_row_counts(out_dir: Path = OUT_DIR) -> dict[str, int]:
    """Return each subset table's kept-row count as `{config: rows}`, read from the stamp, so the oracle can cut a table into row ranges without streaming it. Returns `{}` when the stamp is missing, malformed, of another format, or has no counts, as in a hand-made table directory; the caller then treats each table as one range."""
    try:
        stamp = json.loads((Path(out_dir) / STAMP_NAME).read_text())
    except OSError, ValueError:
        return {}
    if not isinstance(stamp, dict) or stamp.get("format") != STAMP_FORMAT:
        return {}
    rows = stamp.get("rows")
    if not isinstance(rows, dict):
        return {}
    try:
        return {str(config): int(count) for config, count in rows.items()}
    except TypeError, ValueError:
        return {}


def is_fresh(repo_root: Path = REPO_ROOT) -> bool:
    """Return whether the stamped subset tables still match the alphabet and sources on disk. That requires a stamp of this format whose key matches a fresh computation, subset outputs on disk that are exactly the stamped set, and outputs and a names sidecar whose bytes hash to the stamped digests. A missing or malformed stamp reads as stale and never raises."""
    _, out_dir = _dirs(repo_root)
    try:
        stamp = json.loads((out_dir / STAMP_NAME).read_text())
    except OSError, ValueError:
        return False
    if not isinstance(stamp, dict) or stamp.get("format") != STAMP_FORMAT:
        return False
    if stamp.get("key") != stamp_key(repo_root):
        return False
    outputs = stamp.get("outputs")
    if not isinstance(outputs, dict):
        return False
    if _subset_outputs_on_disk(out_dir) != set(outputs):
        return False
    sidecars = stamp.get("sidecars")
    if not isinstance(sidecars, dict) or NAMES_NAME not in sidecars:
        return False
    for name, digest in list(outputs.items()) + [(NAMES_NAME, sidecars[NAMES_NAME])]:
        if not isinstance(digest, str):
            return False
        try:
            if fingerprint.file_sha256(out_dir / name) != digest:
                return False
        except OSError:
            return False
    return True


def read_font_projections(baseline_dir: Path) -> dict[str, dict[str, str]]:
    """Return the provenance sidecar as `{font relative path: {raw sha256 a header records: head- and name-blind digest of that font}}`. A missing, malformed, or other-format sidecar reads as empty, so every raw-digest mismatch then fails."""
    try:
        payload = json.loads((Path(baseline_dir) / FONT_PROJECTION_SIDECAR).read_text())
    except OSError, ValueError:
        return {}
    if not isinstance(payload, dict) or payload.get("format") != FONT_PROJECTION_FORMAT:
        return {}
    fonts = payload.get("fonts")
    if not isinstance(fonts, dict):
        return {}
    return {
        str(font): {str(raw): str(projection) for raw, projection in entries.items()}
        for font, entries in fonts.items()
        if isinstance(entries, dict)
    }


def _write_font_projections(baseline_dir: Path, fonts: Mapping[str, Mapping[str, str]]) -> None:
    """Write the provenance sidecar to a staging file and rename it into place, so an interrupted call leaves the previous sidecar intact."""
    payload = {
        "format": FONT_PROJECTION_FORMAT,
        "fonts": {font: dict(sorted(entries.items())) for font, entries in sorted(fonts.items())},
    }
    path = Path(baseline_dir) / FONT_PROJECTION_SIDECAR
    staging = path.with_name(path.name + ".staging")
    staging.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(staging, path)


def prove_font_provenance(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Check that every `rebuild/out/baseline-*.tsv.gz` was extracted from the font now on disk at the path its header names, and return `{table name: font_sha256}` for the tables checked.

    A baseline row depends only on the font bytes, the alphabet, and the extractor code. The header's `alphabet_sha256` records the alphabet, and the determinism and header tests in rebuild/test_extractor.py cover the extractor. This check covers the font, so no stage needs to re-shape table rows to verify them. Reading each table's header and hashing the font takes milliseconds, so the check runs on every call instead of being keyed to the stamp. With no tables it checks nothing and raises nothing; `_prove_default_covered` fails on that case during the refilter.

    A header whose `font_sha256` matches the font on disk passes, and the font's `head`- and `name`-blind digest (`fingerprint.font_content_digest`) is recorded under that raw digest. A header whose digest does not match passes only when `FONT_PROJECTION_SIDECAR` holds a digest for the font it names and the font on disk has the same `head`- and `name`-blind digest, which is the case a version bump's `make all` leaves. Otherwise the call raises `BaselineProvenanceError`, and the message says whether the two fonts differ outside `head` and `name` or no digest was recorded for the extraction font. The sidecar is written at the end of a call where every table passes, and only when its contents change. It holds one entry per (font, raw digest) pair that a current header names, so it does not accumulate old fonts, and a failing call writes nothing.
    """
    baseline_dir, _ = _dirs(repo_root)
    proven: dict[str, str] = {}
    live_digests: dict[Path, str] = {}
    live_projections: dict[Path, str] = {}
    recorded_projections = read_font_projections(baseline_dir)
    current_projections: dict[str, dict[str, str]] = {}
    for source in sorted(baseline_dir.glob("baseline-*.tsv.gz")):
        header = read_header(source)
        font_relative = header.get("font")
        recorded = header.get("font_sha256")
        if not font_relative or not recorded:
            raise BaselineProvenanceError(
                f"{source.name} carries no '# font:' / '# font_sha256:' header pair, so nothing says which font shaped its rows — it predates the header contract rebuild/baseline/model.render_header writes, so {_EXTRACT_REMEDY}"
            )
        font_path = Path(repo_root) / font_relative
        if font_path not in live_digests:
            if not font_path.is_file():
                raise BaselineProvenanceError(
                    f"{source.name} was extracted from {font_relative}, which is not on disk at {font_path} — the site font is gitignored `make all` output, so run `make all` before adjudicating against these tables, or {_EXTRACT_REMEDY}"
                )
            live_digests[font_path] = fingerprint.file_sha256(font_path)
            live_projections[font_path] = fingerprint.font_content_digest(font_path)
        live = live_digests[font_path]
        projection = live_projections[font_path]
        if live == recorded:
            current_projections.setdefault(font_relative, {})[recorded] = projection
        else:
            stored = recorded_projections.get(font_relative, {}).get(recorded)
            if stored is None:
                raise BaselineProvenanceError(
                    f"{source.name} was extracted from a {font_relative} that hashed to {recorded}, but the {font_relative} on disk now hashes to {live}, and {FONT_PROJECTION_SIDECAR} records no head- and name-blind projection for the font it was extracted from, so nothing can say whether its rows are the rows this font shapes — {_EXTRACT_REMEDY}"
                )
            if stored != projection:
                raise BaselineProvenanceError(
                    f"{source.name} was extracted from a {font_relative} that hashed to {recorded}, but the {font_relative} on disk now hashes to {live} and differs from it outside the head and name tables — its rows are not the rows this font shapes, so {_EXTRACT_REMEDY}"
                )
            current_projections.setdefault(font_relative, {})[recorded] = stored
        proven[source.name] = recorded
    if current_projections and current_projections != recorded_projections:
        _write_font_projections(baseline_dir, current_projections)
    return proven


def ensure_fresh(repo_root: Path = REPO_ROOT) -> bool:
    """Check the source tables' font provenance, then refilter if the subset tables are stale, and return whether a refilter ran. Raises `BaselineProvenanceError` from the provenance check and `SubsetIdentityError` from the refilter. The provenance check runs on every call because the site font is `make all` output and not a stamp input, so it can change while the stamp key stays the same."""
    prove_font_provenance(repo_root)
    if is_fresh(repo_root):
        return False
    refresh(repo_root)
    return True


def main() -> None:
    prove_font_provenance(REPO_ROOT)
    refresh(REPO_ROOT)


if __name__ == "__main__":
    main()
