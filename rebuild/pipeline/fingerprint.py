"""Content fingerprints for the build inputs, keyed by component so the readiness checker can name the remedy when a component goes stale.

The surface manifest's `generated_at` stamp is mtime-based and exists to key unit-id joinability; it cannot answer "does this surface reflect the sources on disk right now". These fingerprints do: pure content hashes (plus stat sizes for the 400MB baseline TSVs, whose content digests already live in digests.tsv), sorted and mtime-free so consecutive builds of the same inputs stay byte-identical.

Chain honesty: run_m1 persists the Stage A components (`data`, `baselines`, `pipeline_code`) into rebuild/out/m1/inputs_fingerprint.json at build time, and the review build copies those recorded values into the manifest instead of recomputing them — so a surface rebuilt over stale out/m1 artifacts carries the stale hashes and the checker flags it.

`tables_value` serves the same honesty for a build artifact rather than a manifest: the serialized decision tables carry it, so the conformance sweep can tell a table its own sources produced from one it must rebuild.

Rune files are hashed by `rune_file_digest`, a prose-blind digest over the parsed document rather than the raw bytes: YAML comments and formatting, the ductus prose, the notes prose, and the `why` rationale on prefer/extend/contract/resolve/unlock records are all documentation nothing downstream consumes, so editing them must not stale the surface or re-run a cycle. What stays in the digest is exactly what can move an output or a gate: every geometric and policy field, the ductus *keys* (motion names, which the parity and naming lints enforce), the *presence* of every prose field (the schema requires `why` on absolute prefers), and — the one quoted prose — `policy.refuse[].why`, which settle.py embeds in elimination diagnostics that the review surface serves in its explain panel.
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


def pipeline_code_paths(repo_root: Path) -> list[Path]:
    """rebuild/validation rides in this component: the shaper, row model, seam classifier, and Manual-pin replays are the before side of the M1 comparison — both fingerprinted trees import them — and a key blind to that tree would let every skip fire over changed code. The whole directory, not just the modules currently imported: a list that tracks who happens to import what goes wrong the next time an import is added, and over-invalidation is the safe direction."""
    root = Path(repo_root)
    return sorted((root / "rebuild" / "pipeline").glob("*.py")) + sorted(
        (root / "rebuild" / "validation").glob("*.py")
    )


def review_code_paths(repo_root: Path) -> list[Path]:
    """serve.py is excluded: it is the dev server, not build code, and editing it must not flag the surface stale."""
    return sorted(
        path for path in (Path(repo_root) / "rebuild" / "review").glob("*.py") if path.name != "serve.py"
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
    return sorted(
        f"{_label(repo_root, path)}\t{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in paths
        if path.is_file()
    )


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
    """The prose-blind view of a parsed rune document (see the module docstring for the contract). Anything shaped in a way the schema would reject — a non-string prose value, a non-dict ductus — passes through unprojected, so a type-breaking edit still moves the digest and the load failure it causes stays visible. refuse records keep their `why`: it is the one rationale settle.py quotes into the elimination diagnostics the review surface serves."""
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
        f"{_label(root, path)}\t"
        + (rune_file_digest(path) if path in runes else hashlib.sha256(path.read_bytes()).hexdigest())
        for path in data_paths(root)
        if path.is_file()
    )


def data_value(repo_root: Path) -> str:
    """The `data` component: rune files by their prose-blind digest, every other data input by raw bytes."""
    return hashlib.sha256("\n".join(data_lines(repo_root)).encode()).hexdigest()


def rune_digests(repo_root: Path) -> dict[str, str]:
    """Every rune file's prose-blind digest, keyed by family name (the file stem, which spec_load lints to equal the `rune:` field). This is the per-rune grain the trace-memo store invalidates at: an entry survives a cycle exactly when every family it names still carries the digest recorded beside it."""
    return {path.stem: rune_file_digest(path) for path in rune_paths(Path(repo_root)) if path.is_file()}


def tables_environment_value(repo_root: Path) -> str:
    """`tables_value` with the per-rune digests factored out: the non-rune data inputs plus the pipeline code. The trace-memo store stamps itself with this wholesale — any of these moving invalidates every entry — while the rune files invalidate at per-entry grain through `rune_digests`."""
    root = Path(repo_root)
    runes = set(rune_paths(root))
    lines = sorted(
        f"{_label(root, path)}\t{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in data_paths(root)
        if path.is_file() and path not in runes
    )
    lines.append(f"pipeline_code\t{hash_paths(root, pipeline_code_paths(root))}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def tables_value(repo_root: Path) -> str:
    """The content key over everything the decision-table fixpoint reads: the rune and config data plus the pipeline code. A serialized window enumeration carries this value so it can prove it still describes the sources on disk, and the conformance sweep rebuilds the moment it does not. Deliberately narrower than the Stage A record — the oracle's baselines feed no table, so re-extracting them must not throw the windows away."""
    root = Path(repo_root)
    lines = (
        f"data\t{data_value(root)}",
        f"pipeline_code\t{hash_paths(root, pipeline_code_paths(root))}",
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
