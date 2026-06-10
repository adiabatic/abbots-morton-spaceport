"""Shared row model for the section 13.1 baseline: the Row dataclass, TSV serialization and parsing, canonical row ordering, the configuration registry, and header rendering. Both the extractor and the validation suite import from here so the row format cannot drift between them."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

TOOL_VERSION = "1.0.0"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FONT_RELATIVE_PATH = "site/AbbotsMortonSpaceportSansSenior-Regular.otf"
FONT_PATH = REPO_ROOT / FONT_RELATIVE_PATH
DEFAULT_OUT_DIR = REPO_ROOT / "rebuild" / "out"

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

SEAM_TOKENS: tuple[str, ...] = ("y0", "y5", "y6", "y8", "lig", "break")


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
                codepoints_field(self.codepoints),
                "|".join(self.glyphs),
                ",".join(str(cluster) for cluster in self.clusters),
                ",".join(self.seams),
                "|".join(f"{x},{y},{advance}" for x, y, advance in self.positions),
            )
        )

    @classmethod
    def from_tsv(cls, line: str) -> "Row":
        codepoints, glyphs, clusters, seams, positions = line.rstrip("\n").split("\t")
        return cls(
            codepoints=tuple(int(part, 16) for part in codepoints.split(":")),
            glyphs=tuple(glyphs.split("|")),
            clusters=tuple(int(part) for part in clusters.split(",")),
            seams=tuple(seams.split(",")) if seams else (),
            positions=tuple(_parse_position(part) for part in positions.split("|")),
        )


def _parse_position(field: str) -> tuple[int, int, int]:
    x, y, advance = field.split(",")
    return (int(x), int(y), int(advance))


def codepoints_field(codepoints: tuple[int, ...]) -> str:
    return ":".join(f"{cp:04X}" for cp in codepoints)


def row_sort_key(row: Row) -> tuple[int, tuple[int, ...]]:
    return (len(row.codepoints), row.codepoints)


def feature_note(config_token: str) -> str:
    features = CONFIGS[config_token]
    return " ".join(f"{tag}=1" for tag, enabled in features.items() if enabled)


def render_header(
    config_token: str,
    *,
    git_sha: str,
    font_sha256: str,
    alphabet_sha256: str,
    tool_version: str = TOOL_VERSION,
    subset: str | None = None,
) -> list[str]:
    """The fixed-order header lines of a baseline table (plan section 3). The optional subset line marks smoke runs (--limit / --sample) so a partial table can never be mistaken for the full oracle."""
    lines = [
        f"# baseline-extract v{tool_version}",
        f"# git_sha: {git_sha}",
        f"# font: {FONT_RELATIVE_PATH}",
        f"# font_sha256: {font_sha256}",
        f"# config: {config_token} ({feature_note(config_token)})",
    ]
    if subset is not None:
        lines.append(f"# subset: {subset}")
    lines.append(f"# alphabet_sha256: {alphabet_sha256}")
    lines.append("# columns: codepoints glyphs clusters seams positions")
    return lines


def parse_header(lines: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in lines:
        body = line.removeprefix("# ")
        if body.startswith("baseline-extract v"):
            parsed["tool_version"] = body.removeprefix("baseline-extract v")
            continue
        key, _, value = body.partition(": ")
        parsed[key] = value
    if "config" in parsed:
        parsed["config"] = parsed["config"].split(" (")[0]
    return parsed


def current_git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return "unknown"
    return completed.stdout.strip()


@cache
def font_sha256(font_path: str | Path = FONT_PATH) -> str:
    return hashlib.sha256(Path(font_path).read_bytes()).hexdigest()
