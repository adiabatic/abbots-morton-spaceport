"""The §3.4 intended-equivalence triage per rebuild/BASELINE-PLAN.md §6.

For every baseline row whose string is eligible, shape the string with a prepended or appended boundary symbol and compare the string's own portion of the boundary shaping against the baseline row. In the new model post-ZWNJ ≡ word-initial and pre-boundary ≡ word-final hold by definition; today they are maintained by hand, so divergences here are triage rows (current-font facts), never extraction errors.
"""

from __future__ import annotations

import multiprocessing
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .classify import SeamClassifier
from .rowmodel import (
    BOUNDARY_CODEPOINTS,
    CONFIGS,
    Row,
    format_codepoints,
    header_config_token,
    iter_line_chunks,
    read_header,
)
from .shaping import SENIOR_FONT, Shaper, row_for

CHECKS: tuple[tuple[str, str, str], ...] = (
    ("zwnj-vs-edge", "prefix", "‌"),
    ("space-vs-edge", "prefix", " "),
    ("edge-vs-zwnj", "suffix", "‌"),
    ("edge-vs-space", "suffix", " "),
)

TRIAGE_COLUMNS = (
    "config",
    "check",
    "codepoints",
    "baseline_glyphs",
    "boundary_glyphs",
    "first_divergent_position",
    "baseline_seams",
    "boundary_seams",
    "divergence_kind",
)

CHUNK_SIZE = 20_000


@dataclass(frozen=True)
class Portion:
    glyphs: tuple[str, ...]
    clusters: tuple[int, ...]
    seams: tuple[str, ...]
    positions: tuple[tuple[int, int, int], ...]


def w_portion(boundary_row: Row, side: str, w_length: int) -> Portion:
    """Slice the w-side of a boundary shaping: for prefix checks the glyphs whose clusters cover the post-boundary positions (re-based), for suffix checks the glyphs covering the pre-boundary positions."""
    if side == "prefix":
        indices = [i for i, c in enumerate(boundary_row.clusters) if c >= 1]
        return Portion(
            glyphs=tuple(boundary_row.glyphs[i] for i in indices),
            clusters=tuple(boundary_row.clusters[i] - 1 for i in indices),
            seams=boundary_row.seams[1:],
            positions=tuple(boundary_row.positions[i] for i in indices),
        )
    indices = [i for i, c in enumerate(boundary_row.clusters) if c < w_length]
    return Portion(
        glyphs=tuple(boundary_row.glyphs[i] for i in indices),
        clusters=tuple(boundary_row.clusters[i] for i in indices),
        seams=boundary_row.seams[: w_length - 1],
        positions=tuple(boundary_row.positions[i] for i in indices),
    )


def compare_portion(baseline_row: Row, portion: Portion) -> tuple[str, int] | None:
    """Return (divergence_kind, first_divergent_position) or None when the portion agrees with the baseline row."""
    if portion.glyphs != baseline_row.glyphs or portion.clusters != baseline_row.clusters:
        limit = min(len(portion.glyphs), len(baseline_row.glyphs))
        for i in range(limit):
            if portion.glyphs[i] != baseline_row.glyphs[i] or portion.clusters[i] != baseline_row.clusters[i]:
                return ("glyph", i)
        return ("glyph", limit)
    if portion.seams != baseline_row.seams:
        for i, (a, b) in enumerate(zip(portion.seams, baseline_row.seams)):
            if a != b:
                return ("seam", i)
        return ("seam", min(len(portion.seams), len(baseline_row.seams)))
    if portion.positions != baseline_row.positions:
        for i, (a, b) in enumerate(zip(portion.positions, baseline_row.positions)):
            if a != b:
                return ("position-only", i)
        return ("position-only", min(len(portion.positions), len(baseline_row.positions)))
    return None


def check_row(
    shaper: Shaper,
    classifier: SeamClassifier,
    row: Row,
    features: dict[str, bool],
    config_token: str,
) -> list[str]:
    lines: list[str] = []
    text = row.text
    for check_name, side, boundary_char in CHECKS:
        if side == "prefix":
            if row.codepoints[0] in BOUNDARY_CODEPOINTS:
                continue
            boundary_text = boundary_char + text
        else:
            if row.codepoints[-1] in BOUNDARY_CODEPOINTS:
                continue
            boundary_text = text + boundary_char
        boundary_row = row_for(shaper, classifier, boundary_text, features or None)
        portion = w_portion(boundary_row, side, len(text))
        divergence = compare_portion(row, portion)
        if divergence is None:
            continue
        kind, position = divergence
        lines.append(
            "\t".join(
                (
                    config_token,
                    check_name,
                    format_codepoints(row.codepoints),
                    "|".join(row.glyphs),
                    "|".join(portion.glyphs),
                    str(position),
                    ",".join(row.seams),
                    ",".join(portion.seams),
                    kind,
                )
            )
        )
    return lines


_worker_state: dict = {}


def _init_worker(font_path: str, features: dict[str, bool], config_token: str) -> None:
    _worker_state["shaper"] = Shaper(font_path)
    _worker_state["classifier"] = SeamClassifier(font_path)
    _worker_state["features"] = features
    _worker_state["config_token"] = config_token


def _process_chunk(lines: list[str]) -> tuple[int, list[str]]:
    shaper = _worker_state["shaper"]
    classifier = _worker_state["classifier"]
    features = _worker_state["features"]
    config_token = _worker_state["config_token"]
    out: list[str] = []
    for line in lines:
        row = Row.from_tsv(line)
        out.extend(check_row(shaper, classifier, row, features, config_token))
    return (len(lines), out)


def run(
    baseline_path: Path | str,
    out_fh,
    font_path: Path | str = SENIOR_FONT,
    workers: int = 1,
    limit: int | None = None,
) -> Counter:
    """Stream one baseline table, append divergence lines to out_fh, and return per-(check, kind) divergence counts plus a rows-processed count."""
    token = header_config_token(read_header(baseline_path))
    if token not in CONFIGS:
        raise ValueError(f"baseline table declares unknown config {token!r}")
    features = CONFIGS[token]

    counts: Counter = Counter()
    chunks = iter_line_chunks(baseline_path, CHUNK_SIZE, limit)

    def consume(result: tuple[int, list[str]]) -> None:
        n_rows, out_lines = result
        counts["rows"] += n_rows
        for line in out_lines:
            fields = line.split("\t")
            counts[(fields[1], fields[8])] += 1
            out_fh.write(line + "\n")

    if workers <= 1:
        _init_worker(str(font_path), features, token)
        for chunk in chunks:
            consume(_process_chunk(chunk))
        return counts

    with multiprocessing.Pool(
        workers, initializer=_init_worker, initargs=(str(font_path), features, token)
    ) as pool:
        for result in pool.imap(_process_chunk, chunks):
            consume(result)
    return counts
