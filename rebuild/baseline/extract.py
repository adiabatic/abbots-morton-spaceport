"""Extraction orchestration: shard the basis by (length, first symbol), shape each shard in a worker process, then concatenate shards in canonical order into a gzipped table whose uncompressed bytes are independent of scheduling. Also owns the per-configuration digest, digests.tsv, and SUMMARY.md generation."""

from __future__ import annotations

import gzip
import hashlib
import json
import multiprocessing
import shutil
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import alphabet
from .classify import SeamClassifier
from .model import (
    CONFIGS,
    FONT_PATH,
    FONT_RELATIVE_PATH,
    TOOL_VERSION,
    Row,
    codepoints_field,
    current_git_sha,
    feature_note,
    font_sha256,
    render_header,
)
from .shaper import Shaper

MULTI_HEIGHT_EXAMPLE_CAP = 5


@dataclass
class Digest:
    config: str
    rows: int
    sha256_uncompressed: str
    seam_counts: dict[str, int]
    glyph_counts: dict[str, int]
    multi_height_examples: list[tuple[str, str]] = field(default_factory=list)
    subset: str | None = None

    def to_json_dict(self) -> dict:
        return {
            "tool_version": TOOL_VERSION,
            "font": FONT_RELATIVE_PATH,
            "font_sha256": font_sha256(),
            "config": self.config,
            "features": feature_note(self.config),
            "subset": self.subset,
            "rows": self.rows,
            "sha256_uncompressed": self.sha256_uncompressed,
            "seam_counts": dict(sorted(self.seam_counts.items())),
            "multi_height_examples": self.multi_height_examples,
            "glyph_distinct": len(self.glyph_counts),
            "glyph_counts": dict(sorted(self.glyph_counts.items())),
        }


def build_row(
    shaper: Shaper,
    classifier: SeamClassifier,
    codepoints: tuple[int, ...],
    features: dict[str, bool],
) -> Row:
    """Shape one basis string and classify each input seam. The flanking output-glyph pair at seam k is the last glyph covering input k and the first glyph covering input k+1; when one glyph covers both inputs the seam was consumed by ligation."""
    result = shaper.shape(alphabet.string_text(codepoints), features)
    seams: list[str] = []
    for k in range(len(codepoints) - 1):
        left_index = bisect_right(result.clusters, k) - 1
        right_index = left_index + 1
        if right_index >= len(result.clusters) or result.clusters[right_index] > k + 1:
            seams.append("lig")
        else:
            seams.append(classifier.classify(result.names[left_index], result.names[right_index]))
    return Row(
        codepoints=codepoints,
        glyphs=result.names,
        clusters=result.clusters,
        seams=tuple(seams),
        positions=result.positions,
    )


def sample_modulus(sample: int, max_length: int = alphabet.MAX_LENGTH) -> int:
    return max(1, alphabet.basis_size(max_length) // sample)


def sample_includes(codepoints: tuple[int, ...], modulus: int) -> bool:
    """Deterministic fixed-key sampling: a string is in the sample iff the leading 8 bytes of the SHA-256 of its codepoints field hit residue 0. Keyed on the codepoints alone so the sample is identical across runs and configurations."""
    digest = hashlib.sha256(codepoints_field(codepoints).encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % modulus == 0


_SHAPER: Shaper | None = None
_CLASSIFIER: SeamClassifier | None = None


def _init_worker(font_path: str) -> None:
    global _SHAPER, _CLASSIFIER
    _SHAPER = Shaper(font_path)
    _CLASSIFIER = SeamClassifier(font_path)


@dataclass(frozen=True)
class _ShardTask:
    length: int
    first_index: int
    take: int | None
    modulus: int | None
    features: tuple[tuple[str, bool], ...]
    tmp_path: str


def _run_shard(task: _ShardTask) -> tuple[int, Counter, Counter, list[tuple[str, str]]]:
    assert _SHAPER is not None and _CLASSIFIER is not None
    features = dict(task.features)
    seam_counts: Counter = Counter()
    glyph_counts: Counter = Counter()
    multi_height: list[tuple[str, str]] = []
    rows = 0
    with open(task.tmp_path, "w", encoding="utf-8", newline="\n") as f:
        for codepoints in alphabet.shard_strings(task.length, task.first_index):
            if task.take is not None and rows >= task.take:
                break
            if task.modulus is not None and not sample_includes(codepoints, task.modulus):
                continue
            row = build_row(_SHAPER, _CLASSIFIER, codepoints, features)
            f.write(row.to_tsv() + "\n")
            rows += 1
            for seam in row.seams:
                seam_counts[seam] += 1
                if "+" in seam and len(multi_height) < MULTI_HEIGHT_EXAMPLE_CAP:
                    multi_height.append((codepoints_field(codepoints), seam))
            for glyph in row.glyphs:
                glyph_counts[glyph] += 1
    return rows, seam_counts, glyph_counts, multi_height


def _build_tasks(
    config_token: str,
    tmp_dir: Path,
    limit: int | None,
    sample: int | None,
    max_length: int,
) -> list[_ShardTask]:
    features = tuple(sorted(CONFIGS[config_token].items()))
    modulus = sample_modulus(sample, max_length) if sample is not None else None
    tasks: list[_ShardTask] = []
    remaining = limit
    for length, first_index in alphabet.shard_keys(max_length):
        take = None
        if remaining is not None:
            take = min(alphabet.shard_size(length), remaining)
            remaining -= take
            if take == 0:
                continue
        tasks.append(
            _ShardTask(
                length=length,
                first_index=first_index,
                take=take,
                modulus=modulus,
                features=features,
                tmp_path=str(tmp_dir / f"shard-{length}-{first_index:02d}.tsv"),
            )
        )
    return tasks


def extract_config(
    config_token: str,
    out_dir: Path,
    workers: int = 10,
    *,
    limit: int | None = None,
    sample: int | None = None,
    max_length: int = alphabet.MAX_LENGTH,
) -> Digest:
    if config_token not in CONFIGS:
        raise ValueError(f"unknown config token {config_token!r}; expected one of {list(CONFIGS)}")
    if limit is not None and sample is not None:
        raise ValueError("limit and sample are mutually exclusive")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / f".tmp-baseline-{config_token}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    subset = None
    if limit is not None:
        subset = f"limit={limit}"
    elif sample is not None:
        subset = f"sample={sample} modulus={sample_modulus(sample, max_length)}"
    if max_length != alphabet.MAX_LENGTH:
        subset = f"{subset + ' ' if subset else ''}max_length={max_length}"
    try:
        tasks = _build_tasks(config_token, tmp_dir, limit, sample, max_length)
        if workers > 1:
            context = multiprocessing.get_context("spawn")
            with context.Pool(workers, initializer=_init_worker, initargs=(str(FONT_PATH),)) as pool:
                results = pool.map(_run_shard, tasks)
        else:
            _init_worker(str(FONT_PATH))
            results = [_run_shard(task) for task in tasks]
        total_rows = 0
        seam_counts: Counter = Counter()
        glyph_counts: Counter = Counter()
        multi_height: list[tuple[str, str]] = []
        for rows, shard_seams, shard_glyphs, shard_multi in results:
            total_rows += rows
            seam_counts.update(shard_seams)
            glyph_counts.update(shard_glyphs)
            multi_height.extend(shard_multi)
        multi_height = multi_height[:MULTI_HEIGHT_EXAMPLE_CAP]
        header = render_header(
            config_token,
            git_sha=current_git_sha(),
            font_sha256=font_sha256(),
            alphabet_sha256=alphabet.alphabet_sha256(),
            subset=subset,
        )
        digest_hash = hashlib.sha256()
        table_path = out_dir / f"baseline-{config_token}.tsv.gz"
        with open(table_path, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
                header_bytes = ("".join(line + "\n" for line in header)).encode("utf-8")
                gz.write(header_bytes)
                digest_hash.update(header_bytes)
                for task in tasks:
                    shard_bytes = Path(task.tmp_path).read_bytes()
                    gz.write(shard_bytes)
                    digest_hash.update(shard_bytes)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    digest = Digest(
        config=config_token,
        rows=total_rows,
        sha256_uncompressed=digest_hash.hexdigest(),
        seam_counts=dict(seam_counts),
        glyph_counts=dict(glyph_counts),
        multi_height_examples=multi_height,
        subset=subset,
    )
    write_digest(digest, out_dir)
    write_digests_tsv(out_dir)
    if multi_height:
        raise AssertionError(
            f"multi-height seam classifications in {config_token} (none expected); first examples: {multi_height}"
        )
    return digest


def run_all(
    out_dir: Path,
    workers: int = 10,
    *,
    limit: int | None = None,
    sample: int | None = None,
    max_length: int = alphabet.MAX_LENGTH,
) -> list[Digest]:
    return [
        extract_config(token, out_dir, workers, limit=limit, sample=sample, max_length=max_length)
        for token in CONFIGS
    ]


def _digest_path(out_dir: Path, config_token: str) -> Path:
    return out_dir / f"digest-{config_token}.json"


def write_digest(digest: Digest, out_dir: Path) -> None:
    path = _digest_path(out_dir, digest.config)
    path.write_text(json.dumps(digest.to_json_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_digest_dicts(out_dir: Path) -> list[dict]:
    digests = []
    for token in CONFIGS:
        path = _digest_path(out_dir, token)
        if path.exists():
            digests.append(json.loads(path.read_text(encoding="utf-8")))
    return digests


def write_digests_tsv(out_dir: Path) -> Path:
    path = out_dir / "digests.tsv"
    columns = ["config", "rows", "sha256_uncompressed", "y0", "y5", "y6", "y8", "lig", "break", "subset"]
    lines = ["\t".join(columns)]
    for digest in load_digest_dicts(out_dir):
        seams = digest["seam_counts"]
        lines.append(
            "\t".join(
                [
                    digest["config"],
                    str(digest["rows"]),
                    digest["sha256_uncompressed"],
                    *[str(seams.get(token, 0)) for token in ("y0", "y5", "y6", "y8", "lig", "break")],
                    digest["subset"] or "full",
                ]
            )
        )
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def _count_triage_rows(path: Path) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or line.startswith("config\t"):
            continue
        parts = line.split("\t")
        key = (parts[0], parts[1])
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_summary(out_dir: Path, top_glyphs: int = 20) -> Path:
    out_dir = Path(out_dir)
    lines: list[str] = ["# Baseline extraction summary", ""]
    lines.append(f"Generated by baseline-extract v{TOOL_VERSION} at git {current_git_sha()}.")
    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- Font: `{FONT_RELATIVE_PATH}`")
    lines.append(f"- Font SHA-256: `{font_sha256()}`")
    lines.append(f"- Alphabet SHA-256: `{alphabet.alphabet_sha256()}` ({len(alphabet.ALPHABET)} symbols)")
    lines.append("")
    lines.append("## Alphabet")
    lines.append("")
    lines.append("| Codepoint | Name |")
    lines.append("| --------- | ---- |")
    names = alphabet.symbol_names()
    for cp in alphabet.ALPHABET:
        lines.append(f"| U+{cp:04X} | {names[cp]} |")
    lines.append("")
    lines.append("## Per-configuration digests")
    lines.append("")
    digests = load_digest_dicts(out_dir)
    if digests:
        lines.append(
            "| Config | Rows | SHA-256 (uncompressed) | y0 | y5 | y6 | y8 | lig | break | Distinct glyphs | Subset |"
        )
        lines.append(
            "| ------ | ---- | ---------------------- | -- | -- | -- | -- | --- | ----- | --------------- | ------ |"
        )
        for digest in digests:
            seams = digest["seam_counts"]
            seam_cells = " | ".join(
                str(seams.get(token, 0)) for token in ("y0", "y5", "y6", "y8", "lig", "break")
            )
            lines.append(
                f"| {digest['config']} | {digest['rows']} | `{digest['sha256_uncompressed']}` | "
                f"{seam_cells} | {digest['glyph_distinct']} | {digest['subset'] or 'full'} |"
            )
        lines.append("")
        lines.append("## Resolved-glyph-name frequencies (top section)")
        lines.append("")
        for digest in digests:
            lines.append(f"### {digest['config']}")
            lines.append("")
            lines.append(f"{digest['glyph_distinct']} distinct resolved glyph names. Top {top_glyphs}:")
            lines.append("")
            lines.append("| Glyph | Count |")
            lines.append("| ----- | ----- |")
            ranked = sorted(digest["glyph_counts"].items(), key=lambda item: (-item[1], item[0]))
            for glyph, count in ranked[:top_glyphs]:
                lines.append(f"| `{glyph}` | {count} |")
            lines.append("")
    else:
        lines.append("No digests found; run the extraction first.")
        lines.append("")
    triage_path = out_dir / "equivalence-triage.tsv"
    lines.append("## Equivalence divergences")
    lines.append("")
    if triage_path.exists():
        counts = _count_triage_rows(triage_path)
        if counts:
            lines.append("| Config | Check | Divergences |")
            lines.append("| ------ | ----- | ----------- |")
            for (config, check), count in sorted(counts.items()):
                lines.append(f"| {config} | {check} | {count} |")
        else:
            lines.append("No divergences recorded.")
    else:
        lines.append("Not run yet (`equivalence-triage.tsv` absent).")
    lines.append("")
    split_path = out_dir / "split-check-disagreements.tsv"
    lines.append("## Split-buffer cross-check")
    lines.append("")
    if split_path.exists():
        disagreement_rows = [
            line
            for line in split_path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and not line.startswith("config\t")
        ]
        lines.append(
            f"{len(disagreement_rows)} disagreement rows recorded in `split-check-disagreements.tsv`."
        )
    else:
        lines.append("Not run yet (`split-check-disagreements.tsv` absent).")
    lines.append("")
    replay_path = out_dir / "replay-report.json"
    lines.append("## Corpus pin replay")
    lines.append("")
    if replay_path.exists():
        report = json.loads(replay_path.read_text(encoding="utf-8"))
        for key, value in sorted(report.items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("Not run yet (`replay-report.json` absent).")
    lines.append("")
    path = out_dir / "SUMMARY.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
