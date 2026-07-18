"""Content fingerprints for the review-surface build inputs, keyed by component so the readiness checker can name the remedy when a component goes stale.

The surface manifest's `generated_at` stamp is mtime-based and exists to key unit-id joinability; it cannot answer "does this surface reflect the sources on disk right now". These fingerprints do: pure content hashes (plus stat sizes for the 400MB baseline TSVs, whose content digests already live in digests.tsv), sorted and mtime-free so consecutive builds of the same inputs stay byte-identical.

Chain honesty: run_m1 persists the Stage A components (`data`, `baselines`, `pipeline_code`) into rebuild/out/m1/inputs_fingerprint.json at build time, and the review build copies those recorded values into the manifest instead of recomputing them — so a surface rebuilt over stale out/m1 artifacts carries the stale hashes and the checker flags it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

FORMAT = "ams-inputs-fingerprint/1"
STAGE_A_COMPONENTS = ("data", "baselines", "pipeline_code")
STAGE_B_COMPONENTS = ("review_code", "static", "fonts")
COMPONENTS = STAGE_A_COMPONENTS + STAGE_B_COMPONENTS
STAGE_A_FILENAME = "inputs_fingerprint.json"


def data_paths(repo_root: Path) -> list[Path]:
    root = Path(repo_root)
    paths = sorted((root / "glyph_data" / "runes").glob("*.yaml"))
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
    return sorted((Path(repo_root) / "rebuild" / "pipeline").glob("*.py"))


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


def hash_paths(repo_root: Path, paths: list[Path]) -> str:
    lines = sorted(
        f"{_label(repo_root, path)}\t{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in paths
        if path.is_file()
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
        "data": hash_paths(root, data_paths(root)),
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
