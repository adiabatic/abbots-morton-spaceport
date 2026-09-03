"""Streaming filter of the baseline tables to the M1 sub-alphabet (M1-PLAN section 5, Group 3).

Streams each `rebuild/out/baseline-<config>.tsv.gz` once via `rebuild.validation.rowmodel.open_table`, keeps rows whose codepoints are a subset of the M1 alphabet, and writes `rebuild/out/m1/baseline-<config>.subset.tsv.gz` preserving the header lines and the canonical (length, codepoints) row order. The same filter runs over `equivalence-triage.tsv` into `rebuild/out/m1/triage.subset.tsv`.

Two claims about those tables are proven here rather than on every run that reads them, because a refilter is the only thing that can change either answer. The first is that every `DEFAULT_COVERED_CONFIGS` sub-table is row-identical to `IDENTITY_REFERENCE`'s: the acceptance gate covers ss06, ss07 and ss06+ss07 by running default alone, and that only holds while their filtered rows are the same rows. The digest each filter pass already folds over its kept data lines turns that proof into a comparison of hex strings — no second read of three 838k-row tables — and a mismatch raises `SubsetIdentityError` before the stamp is written, so a diverged configuration is never stamped fresh and the next run refilters into the same refusal rather than adjudicating against tables nobody proved. The second is the roster of old glyph names the kept rows carry: `refresh` writes it to `subset-names.json`, sorted and distinct per configuration, off the tokens the filter already splits — so the oracle's alias-completeness guard answers from a few thousand names instead of streaming ten million rows, on the `--gates-only` path as cheaply as on a full build. A third claim rides the opposite schedule, proven on every `ensure_fresh` rather than once per refilter: that every source table was extracted from the site font on disk, its header's `font_sha256` weighed against the font the header itself names. `make all` rewrites that font outside every stamp this module keeps, so a rebuilt or re-extracted font moves no key a freshness check would notice, and only a proof that runs whether the tables read fresh or stale can keep the oracle from adjudicating against rows some other font shaped.

run_m1 calls ensure_fresh() before its gates, so an M1_ALPHABET edit can never feed the oracle stale subset tables: subset_stamp.json records a key over the alphabet, the source tables, and this module, plus each output's content hash and the names sidecar's, and the refilter is skipped only when the key matches and the outputs on disk are exactly the stamped set with the stamped bytes — a truncated table, an edited table, a missing or edited sidecar, or an orphan left by a vanished source all read as stale, and refresh() prunes orphans. The alias map is deliberately outside the key even though the sidecar feeds the alias check: it is hand-edited far more often than the tables move, and folding it in would turn every alias edit into a full refilter of every configuration. Subset gzip members are written with mtime=0 so refiltering unchanged sources reproduces each table byte for byte.

Run by hand (unconditional refilter) as: uv run python -m rebuild.pipeline.baseline_subset
"""

from __future__ import annotations

import gzip
import hashlib
import io
import itertools
import json
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

EXTRACT_REMEDY = "re-extract with `uv run python -m rebuild.baseline.cli extract --all --out rebuild/out` then `uv run python -m rebuild.baseline.cli summarize --out rebuild/out`, or rebuild the font the tables were extracted from (the header's git_sha names the commit it was built at; the site font is `make all` output)"


class SubsetIdentityError(RuntimeError):
    """A DEFAULT_COVERED_CONFIGS sub-table that no longer matches the reference — raised before the stamp is written, so the refusal cannot be skipped by a freshness check."""


class BaselineProvenanceError(RuntimeError):
    """A source baseline table whose header names a font other than the one on disk — raised before any freshness check, so a re-extracted or rebuilt site font can never feed the oracle rows shaped by a different font."""


@dataclass(frozen=True)
class FilteredTable:
    """What one filter pass learned about the table it wrote: the kept-row count, every old glyph name those rows name, and a digest over the kept data lines exactly as written."""

    kept: int
    glyph_names: frozenset[str]
    rows_sha256: str


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


def filter_table(source: Path, destination: Path, alphabet: frozenset[int] = M1_ALPHABET) -> FilteredTable:
    """Filter one baseline table (header lines preserved verbatim), folding the kept rows' digest and their glyph names out of the same pass that writes them."""
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
    """The content key the stamp records: everything the subset tables are a pure function of — the alphabet, the source tables (by the same size-plus-digests proxy as fingerprint.baselines_value, so no 42MB table is ever read to answer a freshness check), and this module's own bytes, so a filter-logic change refilters rather than trusting output the old code wrote."""
    lines = [
        "alphabet\t" + ",".join(f"{codepoint:04X}" for codepoint in sorted(M1_ALPHABET)),
        f"baselines\t{fingerprint.baselines_value(Path(repo_root))}",
        f"filter_code\t{fingerprint.file_sha256(Path(__file__))}",
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _subset_outputs_on_disk(out_dir: Path) -> set[str]:
    return {path.name for path in out_dir.glob("baseline-*.subset.tsv.gz")}


def _data_lines(reader: Iterable[str]) -> Iterator[str]:
    return (line for line in reader if not line.startswith("#") and line.strip())


def _first_differing_row(left: Path, right: Path) -> tuple[str | None, str | None]:
    """The one read the happy path never pays for: two subset outputs walked in step for the first data line that differs, so a refusal can name a row rather than only a pair of digests."""
    with open_table(left) as left_reader, open_table(right) as right_reader:
        for pair in itertools.zip_longest(_data_lines(left_reader), _data_lines(right_reader)):
            if pair[0] != pair[1]:
                return pair
    return None, None


def _prove_default_covered(out_dir: Path, filtered: Mapping[str, FilteredTable]) -> None:
    """Every DEFAULT_COVERED_CONFIGS sub-table against IDENTITY_REFERENCE's, by the digest the filter pass already folded. Proven here because a refilter is the only event that can change the answer, and raised before the stamp so a diverged configuration can never be stamped fresh."""
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
    """The alias check's whole input, written once per refilter: the distinct old glyph names of each configuration's kept rows, sorted."""
    payload = {
        "format": NAMES_FORMAT,
        "names": {config: sorted(table.glyph_names) for config, table in sorted(filtered.items())},
    }
    path = out_dir / NAMES_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def read_subset_names(out_dir: Path = OUT_DIR) -> dict[str, list[str]]:
    """The names sidecar as `{config: sorted names}`. Unlike is_fresh this is loud: a caller that reaches for the sidecar has already been told the tables are fresh, so a missing or malformed one is a broken invariant rather than a state to refilter out of."""
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
    """Refilter every source table into rebuild/out/m1, prune any subset output no longer backed by a source, prove the default-covered configurations identical, write the names sidecar, and write the stamp; returns the output names mapped to their content hashes. The key is snapshotted before the filter loop (the _settle_green discipline): a source edited mid-refilter stamps under the pre-edit key and reads as stale next check, never as fresh tables it does not describe. The identity proof runs after the prune and before the stamp, so a refusal leaves the outputs on disk with no stamp vouching for them."""
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
        "sidecars": {NAMES_NAME: fingerprint.file_sha256(names_path)},
    }
    (out_dir / STAMP_NAME).write_text(json.dumps(payload, indent=2) + "\n")
    return outputs


def is_fresh(repo_root: Path = REPO_ROOT) -> bool:
    """Whether the stamped subset tables still describe the alphabet and sources on disk: the stamp is of this format, its key matches a recomputation, the subset outputs on disk are exactly the stamped set (an orphan or a stray extra reads as stale), every output's bytes hash to the stamped digest (a truncated or edited table reads as stale), and the names sidecar the stamp records is still on disk with the bytes it recorded. A missing or malformed stamp of any shape — including every state predating this format — reads as stale, never raises."""
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


def prove_font_provenance(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Every `rebuild/out/baseline-*.tsv.gz` header's recorded `font_sha256` weighed against the font that same header names, returned as `{table name: font_sha256}` for the tables proven. A baseline row is a pure function of the font bytes, the alphabet and the extractor code: the header's `alphabet_sha256` pins the second and rebuild/test_extractor.py's determinism and header tests pin the third, so this is what pins the first, and it is why no stage re-shapes a table row to check it. Twelve gzip headers and one hash of a half-megabyte font cost milliseconds, which is what lets it run on every call rather than ride a stamp. An empty tree proves nothing and refuses nothing — the no-tables case is `_prove_default_covered`'s to refuse, downstream."""
    baseline_dir, _ = _dirs(repo_root)
    proven: dict[str, str] = {}
    live_digests: dict[Path, str] = {}
    for source in sorted(baseline_dir.glob("baseline-*.tsv.gz")):
        header = read_header(source)
        font_relative = header.get("font")
        recorded = header.get("font_sha256")
        if not font_relative or not recorded:
            raise BaselineProvenanceError(
                f"{source.name} carries no '# font:' / '# font_sha256:' header pair, so nothing says which font shaped its rows — it predates the header contract rebuild/baseline/model.render_header writes, so {EXTRACT_REMEDY}"
            )
        font_path = Path(repo_root) / font_relative
        if font_path not in live_digests:
            if not font_path.is_file():
                raise BaselineProvenanceError(
                    f"{source.name} was extracted from {font_relative}, which is not on disk at {font_path} — the site font is gitignored `make all` output, so run `make all` before adjudicating against these tables, or {EXTRACT_REMEDY}"
                )
            live_digests[font_path] = fingerprint.file_sha256(font_path)
        live = live_digests[font_path]
        if live != recorded:
            raise BaselineProvenanceError(
                f"{source.name} was extracted from a {font_relative} that hashed to {recorded}, but the {font_relative} on disk now hashes to {live} — its rows are not the rows this font shapes, so {EXTRACT_REMEDY}"
            )
        proven[source.name] = recorded
    return proven


def ensure_fresh(repo_root: Path = REPO_ROOT) -> bool:
    """run_m1's pre-gate guard: prove the source tables' font provenance, then refilter when stale and no-op when fresh. Returns whether a refilter ran, raises BaselineProvenanceError when a source table's header names a font other than the one on disk, and raises SubsetIdentityError when the refilter finds a default-covered configuration that has diverged. The provenance proof runs first and on every call, fresh or stale, because the site font is `make all` output rather than an input to the filter: it can be rebuilt or re-extracted under a stamp key that never moves, so a proof that rode the stamp would be a proof that never ran again."""
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
