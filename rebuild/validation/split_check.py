"""Split-buffer cross-check per rebuild/BASELINE-PLAN.md §4.

A `break` classification should correspond to genuinely uncoupled shaping. For every length-2 baseline row with a break seam, and a deterministic 1% sample of length-3/4 rows containing at least one break seam, shape the string split at each break seam and compare the flanking glyphs against the full-buffer baseline row. Disagreements are current-font facts (isolation leaks), not extraction failures; the baseline records the full-buffer truth.

One stated refinement of the plan's "names and positions" comparison: an x_advance-only difference on a flanking glyph is the GPOS kern channel — the font legitimately kerns across non-joining pairs (the hidden-junction kerning system), and the existing suite's break-isolation invariant likewise skips position comparison entirely when the names agree. Counting every kerned break pair as a disagreement would bury the leak signal the census is sized against, so advance-only differences are tallied as `kern_only` in the summary counts instead of emitted as disagreement rows; name or offset differences are the real triage rows.
"""

from __future__ import annotations

import hashlib
import multiprocessing
from collections import Counter
from pathlib import Path

from .rowmodel import (
    CONFIGS,
    Row,
    format_codepoints,
    header_config_token,
    iter_line_chunks,
    read_header,
)
from .shaping import SENIOR_FONT, Shaper, first_glyph_covering, last_glyph_covering

DISAGREEMENT_COLUMNS = (
    "config",
    "codepoints",
    "seam_index",
    "full_glyphs",
    "split_glyphs",
    "mismatch",
)

SAMPLE_SALT = "amsp-split-check-v1"
SAMPLE_RATE = 0.01
_SAMPLE_THRESHOLD = int(SAMPLE_RATE * 2**64)

CHUNK_SIZE = 20_000


def sampled(codepoints: tuple[int, ...]) -> bool:
    """Fixed-seed 1% sampling keyed by the codepoint tuple, identical across runs and configurations."""
    digest = hashlib.sha256(f"{SAMPLE_SALT}:{format_codepoints(codepoints)}".encode()).digest()
    return int.from_bytes(digest[:8], "big") < _SAMPLE_THRESHOLD


def _flank(name: str, position: tuple[int, int, int]) -> str:
    return f"{name}@{position[0]},{position[1]},{position[2]}"


def check_break_seam(
    shaper: Shaper, row: Row, seam_index: int, features: dict[str, bool], config_token: str
) -> tuple[str | None, bool]:
    """Returns (disagreement line or None, kern_only flag)."""
    text = row.text
    split_at = seam_index + 1
    split = shaper.shape_split(text, [split_at], features or None)

    full_left = last_glyph_covering(row.clusters, seam_index)
    full_right = first_glyph_covering(row.clusters, seam_index + 1)
    split_left = last_glyph_covering(split.clusters, seam_index)
    split_right = first_glyph_covering(split.clusters, seam_index + 1)

    mismatches = []
    advance_only = []
    for side, full_i, split_i in (("left", full_left, split_left), ("right", full_right, split_right)):
        if row.glyphs[full_i] != split.names[split_i]:
            mismatches.append(f"{side}-name")
        if row.positions[full_i][:2] != split.positions[split_i][:2]:
            mismatches.append(f"{side}-offset")
        if row.positions[full_i][2] != split.positions[split_i][2]:
            advance_only.append(f"{side}-advance")
    if not mismatches:
        return (None, bool(advance_only))
    line = "\t".join(
        (
            config_token,
            format_codepoints(row.codepoints),
            str(seam_index),
            f"{_flank(row.glyphs[full_left], row.positions[full_left])}|"
            f"{_flank(row.glyphs[full_right], row.positions[full_right])}",
            f"{_flank(split.names[split_left], split.positions[split_left])}|"
            f"{_flank(split.names[split_right], split.positions[split_right])}",
            "+".join(mismatches + advance_only),
        )
    )
    return (line, False)


def check_row(
    shaper: Shaper, row: Row, features: dict[str, bool], config_token: str
) -> tuple[int, int, list[str]]:
    break_seams = [k for k, seam in enumerate(row.seams) if seam == "break"]
    if not break_seams:
        return (0, 0, [])
    if len(row.codepoints) > 2 and not sampled(row.codepoints):
        return (0, 0, [])
    lines = []
    kern_only = 0
    for k in break_seams:
        line, was_kern_only = check_break_seam(shaper, row, k, features, config_token)
        if line is not None:
            lines.append(line)
        elif was_kern_only:
            kern_only += 1
    return (len(break_seams), kern_only, lines)


_worker_state: dict = {}


def _init_worker(font_path: str, features: dict[str, bool], config_token: str) -> None:
    _worker_state["shaper"] = Shaper(font_path)
    _worker_state["features"] = features
    _worker_state["config_token"] = config_token


def _process_chunk(lines: list[str]) -> tuple[int, int, int, list[str]]:
    shaper = _worker_state["shaper"]
    features = _worker_state["features"]
    config_token = _worker_state["config_token"]
    seams_checked = 0
    kern_only = 0
    out: list[str] = []
    for line in lines:
        row = Row.from_tsv(line)
        checked, row_kern_only, row_lines = check_row(shaper, row, features, config_token)
        seams_checked += checked
        kern_only += row_kern_only
        out.extend(row_lines)
    return (len(lines), seams_checked, kern_only, out)


def run(
    baseline_path: Path | str,
    out_fh,
    font_path: Path | str = SENIOR_FONT,
    workers: int = 1,
    limit: int | None = None,
) -> Counter:
    token = header_config_token(read_header(baseline_path))
    if token not in CONFIGS:
        raise ValueError(f"baseline table declares unknown config {token!r}")
    features = CONFIGS[token]

    counts: Counter = Counter()
    chunks = iter_line_chunks(baseline_path, CHUNK_SIZE, limit)

    def consume(result: tuple[int, int, int, list[str]]) -> None:
        n_rows, seams_checked, kern_only, out_lines = result
        counts["rows"] += n_rows
        counts["seams_checked"] += seams_checked
        counts["kern_only"] += kern_only
        counts["disagreements"] += len(out_lines)
        for line in out_lines:
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
