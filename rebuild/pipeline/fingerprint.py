"""Content fingerprints for the build inputs, keyed by component so the readiness checker can name the remedy when a component goes stale.

The surface manifest's `generated_at` stamp is mtime-based and exists to key unit-id joinability; it cannot answer "does this surface reflect the sources on disk right now". These fingerprints do: pure content hashes (plus stat sizes for the 400MB baseline TSVs, whose content digests already live in digests.tsv), sorted and mtime-free so consecutive builds of the same inputs stay byte-identical.

Chain honesty: run_m1 persists the Stage A components (`data`, `baselines`, `pipeline_code`) into rebuild/out/m1/inputs_fingerprint.json at build time, and the review build copies those recorded values into the manifest instead of recomputing them — so a surface rebuilt over stale out/m1 artifacts carries the stale hashes and the checker flags it.

`tables_value` serves the same honesty for a build artifact rather than a manifest: the serialized decision tables carry it, so the conformance sweep can tell a table its own sources produced from one it must rebuild. It is keyed on `table_data_value` rather than `data_value` — the alias map, the divergence ledger, and the contact allow-list are read by gates that consume a built table and by nothing that builds one, so they belong to the whole-run record and not to this stamp. Its code half is `table_code_paths` rather than `pipeline_code_paths` for the same reason: the oracle's own module (`COMPARISON_CODE_MODULES`) runs against tables and a font already built, so an edit to the classifier or the ledger match re-adjudicates over the enumeration on disk instead of throwing it away, and rebuild/test_build_code_closure.py is what proves the build never reaches it.

Rune files are hashed by `rune_file_digest`, a prose-blind digest over the parsed document rather than the raw bytes: YAML comments and formatting, the ductus prose, the notes prose, and the `why` rationale on prefer/extend/contract/resolve/unlock records are all documentation nothing downstream consumes, so editing them must not stale the surface or re-run a cycle. What stays in the digest is exactly what can move an output or a gate: every geometric and policy field, the ductus *keys* (motion names, which the parity and naming lints enforce), the *presence* of every prose field (the schema requires `why` on absolute prefers), and — the one quoted prose — `policy.refuse[].why`, which the kernel crate's engine embeds in the elimination diagnostics the review surface serves in its explain panel.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

FORMAT = "ams-inputs-fingerprint/2"
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
STAGE_A_COMPONENTS = ("data", "baselines", "pipeline_code")
STAGE_B_COMPONENTS = ("review_code", "static", "fonts")
COMPONENTS = STAGE_A_COMPONENTS + STAGE_B_COMPONENTS
STAGE_A_FILENAME = "inputs_fingerprint.json"


def file_sha256(path: Path) -> str:
    """The shared file-content hash behind the build's fingerprints, stamps, and green records. Streamed through the digest rather than read whole, so hashing a file never costs its size in resident memory: the same value either way, and most of what passes through is small, but these inputs all grow with the migration and the one that forced the change is already hundreds of megabytes. A module that deliberately keeps rebuild.pipeline out of its import surface spells the same streamed read out inline instead and says why where it does; the roster is not written down here, because rebuild/test_fingerprint.py enforces it and a prose copy could only drift. Missing-file behavior stays with each caller, which is the one thing they disagree about."""
    with open(path, "rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def rune_paths(repo_root: Path) -> list[Path]:
    return sorted((Path(repo_root) / "glyph_data" / "runes").glob("*.yaml"))


def data_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    paths = rune_paths(root)
    paths += sorted((root / "rebuild" / "schema").glob("*.json"))
    paths += [
        root / "rebuild" / "script.yaml",
        root / "glyph_data" / "punctuation.yaml",
        root / "rebuild" / "m1-contact-allow.yaml",
        root / "rebuild" / "m1-aliases.yaml",
        root / "rebuild" / "m1-divergences.yaml",
        root / "glyph_data" / "senior_quikscript_kerning.yaml",
    ]
    return paths


# The comparison side of rebuild/pipeline/: modules that run against tables and a font already built and build neither. They ride `pipeline_code_paths` — the whole-run record, the run_m1 green, the review surface's stamp — and only `table_code_paths` leaves them out, so that a serialized window enumeration's stamp names what produced it and nothing more. rebuild/test_build_code_closure.py walks the import graph from every other pipeline module and from `run_m1.run`, and fails the moment either reaches one of these, so the roster cannot quietly admit a module the build actually runs.
COMPARISON_CODE_MODULES = frozenset({"oracle.py"})


def pipeline_code_paths(repo_root: Path) -> list[Path]:
    """rebuild/validation and the kernel crate ride in this component: the shaper, row model, seam classifier, and Manual-pin replays are the before side of the M1 comparison, while the crate emits the transition stream and formation guard the font is built from. Both fingerprinted Python trees and the crate's complete build-input surface are included rather than tracking current imports or modules piecemeal; those lists go wrong the next time an import or Rust module is added, and over-invalidation is the safe direction."""
    root = Path(repo_root)
    kernel = root / "rebuild" / "kernel-rs"
    return (
        sorted((root / "rebuild" / "pipeline").glob("*.py"))
        + sorted((root / "rebuild" / "validation").glob("*.py"))
        + [kernel / "Cargo.toml", kernel / "Cargo.lock"]
        + sorted((kernel / "src").rglob("*.rs"))
    )


REVIEW_NON_BUILD_MODULES = frozenset({"serve.py", "status.py", "journal.py", "export.py"})


def review_code_paths(repo_root: Path) -> list[Path]:
    """The surface build's own code: rebuild/review/ minus the modules the build never imports, because a stamp component that moves on an edit the build cannot execute costs a full surface rebuild and drops both per-unit caches while proving nothing. serve.py is the dev server; status.py, journal.py, and export.py belong to the verdict plumbing, whose own key hashes what it runs (plumbing_skip_fingerprint). rebuild/test_review_code_closure.py walks build.py's import graph both ways so this exclusion list cannot drift from the real closure."""
    return sorted(
        path
        for path in (Path(repo_root) / "rebuild" / "review").glob("*.py")
        if path.name not in REVIEW_NON_BUILD_MODULES
    )


def static_paths(repo_root: Path) -> list[Path]:
    return sorted(
        path for path in (Path(repo_root) / "rebuild" / "review" / "static").rglob("*") if path.is_file()
    )


def font_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    return [
        root / "site" / "AbbotsMortonSpaceportSansSenior-Regular.otf",
        root / "site" / "AbbotsMortonSpaceportSansJunior-Regular.otf",
    ]


def _label(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return path.name


def path_lines(repo_root: Path, paths: list[Path]) -> list[str]:
    """The per-file `label\\tdigest` lines a path-set hash is built from, sorted — exposed so a green record can store them and a skip miss can name exactly which input moved instead of reporting only that some 64-hex value did."""
    return sorted(f"{_label(repo_root, path)}\t{file_sha256(path)}" for path in paths if path.is_file())


def hash_paths(repo_root: Path, paths: list[Path]) -> str:
    return hashlib.sha256("\n".join(path_lines(repo_root, paths)).encode()).hexdigest()


def _without_why(record: object) -> object:
    if isinstance(record, dict) and isinstance(record.get("why"), str):
        return {**record, "why": None}
    return record


def _projected_stance(stance: object) -> object:
    if not isinstance(stance, dict):
        return stance
    surface = stance.get("surface")
    if not isinstance(surface, dict):
        return stance
    unlocks = surface.get("unlocks")
    if not isinstance(unlocks, list):
        return stance
    return {**stance, "surface": {**surface, "unlocks": [_without_why(unlock) for unlock in unlocks]}}


def _projected_rune(document: object) -> object:
    """The prose-blind view of a parsed rune document (see the module docstring for the contract). Anything shaped in a way the schema would reject — a non-string prose value, a non-dict ductus — passes through unprojected, so a type-breaking edit still moves the digest and the load failure it causes stays visible. refuse records keep their `why`: it is the one rationale the crate's engine quotes into the elimination diagnostics the review surface serves."""
    if not isinstance(document, dict):
        return document
    projected = dict(document)
    ductus = projected.get("ductus")
    if isinstance(ductus, dict):
        projected["ductus"] = {
            key: None if isinstance(value, str) else value for key, value in ductus.items()
        }
    if isinstance(projected.get("notes"), str):
        projected["notes"] = None
    policy = projected.get("policy")
    if isinstance(policy, dict):
        projected["policy"] = {
            kind: (
                [_without_why(record) for record in records]
                if kind in ("prefer", "extend", "contract", "resolve") and isinstance(records, list)
                else records
            )
            for kind, records in policy.items()
        }
    stances = projected.get("stances")
    if isinstance(stances, dict):
        projected["stances"] = {name: _projected_stance(stance) for name, stance in stances.items()}
    return projected


def rune_file_digest(path: Path) -> str:
    """Content digest of one rune file over its prose-blind projection, so documentation edits, comments, and reformatting leave it unmoved. Falls back to the raw byte hash when the file does not parse or serialize — a malformed rune is a build-stopping change, and the fallback keeps it visible."""
    raw = path.read_bytes()
    try:
        payload = json.dumps(
            _projected_rune(yaml.load(raw.decode(), Loader=_SAFE_LOADER)), ensure_ascii=False
        )
    except yaml.YAMLError, UnicodeDecodeError, TypeError, ValueError:
        return hashlib.sha256(raw).hexdigest()
    return hashlib.sha256(payload.encode()).hexdigest()


def data_lines(repo_root: Path) -> list[str]:
    """The per-file `label\\tdigest` lines the `data` component is built from: rune files by their prose-blind digest, every other data input by raw bytes. Sorted, like `path_lines`, and exposed for the same reason."""
    root = Path(repo_root)
    runes = set(rune_paths(root))
    return sorted(
        f"{_label(root, path)}\t" + (rune_file_digest(path) if path in runes else file_sha256(path))
        for path in data_paths(root)
        if path.is_file()
    )


def data_value(repo_root: Path) -> str:
    """The `data` component: rune files by their prose-blind digest, every other data input by raw bytes."""
    return hashlib.sha256("\n".join(data_lines(repo_root)).encode()).hexdigest()


NON_TABLE_DATA_LABELS = (
    "rebuild/m1-aliases.yaml",
    "rebuild/m1-contact-allow.yaml",
    "rebuild/m1-divergences.yaml",
)


def table_data_lines(repo_root: Path) -> list[str]:
    """`data_lines` minus the three data inputs no table stage reads. `rebuild/m1-contact-allow.yaml` is the defect gate's allow-list, handed to `defects.run_gates` alongside tables the fixpoint has already produced; `rebuild/m1-aliases.yaml` and `rebuild/m1-divergences.yaml` are the baseline oracle's, read to name and classify divergences the fixpoint has already decided. None of the three reaches the kernel crate or any stage that builds a decision table, so folding them into the tables' own stamp only made a ledger or classifier re-adjudication throw away an enumeration that would come back byte for byte.

    Narrower stamp, same coverage: all three stay in `data_lines`, which is what the artifact cycle's run_m1 green record and the Stage A `data` component are keyed on, so editing one still costs a full run and still re-runs the defect gate. What it stops costing is the fixpoint.
    """
    excluded = set(NON_TABLE_DATA_LABELS)
    return [line for line in data_lines(repo_root) if line.split("\t", 1)[0] not in excluded]


def table_data_value(repo_root: Path) -> str:
    """The data half of the stamp a serialized window enumeration carries: `table_data_lines` hashed, which is `data_value` narrowed by exactly the files named there and by nothing else."""
    return hashlib.sha256("\n".join(table_data_lines(repo_root)).encode()).hexdigest()


def rune_digests(repo_root: Path) -> dict[str, str]:
    """Every rune file's prose-blind digest, keyed by family name (the file stem, which spec_load lints to equal the `rune:` field). This is the per-rune grain the caches invalidate at: the oracle row cache's per-family content keys (`oracle_cache.family_content_keys`) and the review unit cache's are both built from these, so an entry survives a cycle exactly when every family it names still carries the digest recorded beside it."""
    return {path.stem: rune_file_digest(path) for path in rune_paths(Path(repo_root)) if path.is_file()}


def table_code_paths(repo_root: Path) -> list[Path]:
    """`pipeline_code_paths` minus `COMPARISON_CODE_MODULES`: the code half of the stamp a serialized window enumeration carries. Everything that can move a table or the font stays — the crate, the spec loader, the emitters, the compiler, the gates that run inside the build — and what leaves is only what runs against those artifacts afterward. Conservative in the same direction as `pipeline_code_paths`: a module the build never reaches is still stamped unless it is named in the roster, and the import-graph test is what earns a module its place there."""
    root = Path(repo_root)
    pipeline = root / "rebuild" / "pipeline"
    return [
        path
        for path in pipeline_code_paths(root)
        if not (path.parent == pipeline and path.name in COMPARISON_CODE_MODULES)
    ]


def tables_value(repo_root: Path) -> str:
    """The content key over everything the decision-table fixpoint and the font compile read: the rune and config data by `table_data_value`, plus the build side of the pipeline code by `table_code_paths`. A serialized window enumeration carries this value so it can prove it still describes the sources on disk, and the conformance sweep refuses the moment it does not. Deliberately narrower than the Stage A record at both ends — the oracle's baselines feed no table, so re-extracting them must not throw the windows away, and neither do the alias map, the divergence ledger, the contact allow-list, or the oracle's own code, so re-adjudicating one of those must not either."""
    root = Path(repo_root)
    lines = (
        f"table_data\t{table_data_value(root)}",
        f"pipeline_code\t{hash_paths(root, table_code_paths(root))}",
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def baselines_value(repo_root: Path) -> str:
    out = Path(repo_root) / "rebuild" / "out"
    lines = sorted(
        f"{_label(repo_root, path)}\t{path.stat().st_size}"
        for path in out.glob("baseline-*.tsv.gz")
        if path.is_file()
    )
    digests = out / "digests.tsv"
    payload = "\n".join(lines).encode() + b"\n" + (digests.read_bytes() if digests.is_file() else b"")
    return hashlib.sha256(payload).hexdigest()


def stage_a(repo_root: Path) -> dict:
    root = Path(repo_root)
    return {
        "data": data_value(root),
        "baselines": baselines_value(root),
        "pipeline_code": hash_paths(root, pipeline_code_paths(root)),
    }


def stage_b(repo_root: Path, before_font: Path, junior_font: Path) -> dict:
    root = Path(repo_root)
    return {
        "review_code": hash_paths(root, review_code_paths(root)),
        "static": hash_paths(root, static_paths(root)),
        "fonts": hash_paths(root, [Path(before_font), Path(junior_font)]),
    }


def compute_all(repo_root: Path) -> dict:
    root = Path(repo_root)
    before_font, junior_font = font_paths(root)
    return {**stage_a(root), **stage_b(root, before_font, junior_font)}


def write_stage_a(repo_root: Path, out_dir: Path) -> dict:
    record = {"format": FORMAT, **stage_a(repo_root)}
    (Path(out_dir) / STAGE_A_FILENAME).write_text(json.dumps(record, indent=2) + "\n")
    return record


def read_stage_a(out_dir: Path) -> dict | None:
    try:
        record = json.loads((Path(out_dir) / STAGE_A_FILENAME).read_text())
    except OSError, ValueError:
        return None
    if not isinstance(record, dict):
        return None
    values = {key: record.get(key) for key in STAGE_A_COMPONENTS}
    if not all(isinstance(value, str) for value in values.values()):
        return None
    return values
