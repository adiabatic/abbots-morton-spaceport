"""Corpus-pin replay per rebuild/BASELINE-PLAN.md §7.

Collects every data-expect run from the three corpora with the existing test-suite collector and parser (imported read-only), filters to senior runs whose input falls inside the 47-symbol basis alphabet and whose stylistic-set configuration is one of the plan §5 eleven, and replays each pin's per-seam expectations against this suite's black-box shaping and seam classification. The pins are ground truth the existing test suite already enforces against this exact font, so any live disagreement is a validation-suite bug until proven otherwise.

Variant assertions other than the `half`/`alt` traits (which appear verbatim in compiled glyph names) check compiled-YAML compat metadata in the original suite; replaying them here would require old-pipeline archaeology, so they are skipped and counted instead — the existing suite keeps enforcing them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import SeamClassifier
from .rowmodel import (
    ALPHABET_SET,
    Row,
    config_token_for_features,
    format_codepoints,
)
from .shaping import Shaper, last_glyph_covering, row_for

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_FILES = ("site/index.html", "site/the-manual.html", "site/extra-senior-words.html")

_test_shaping: Any = None


def _import_test_shaping() -> Any:
    global _test_shaping
    if _test_shaping is None:
        test_dir = str(REPO_ROOT / "test")
        if test_dir not in sys.path:
            sys.path.insert(0, test_dir)
        import test_shaping

        _test_shaping = test_shaping
    return _test_shaping


@dataclass(frozen=True)
class PinRun:
    source: str
    expect: str
    text: str
    config_token: str
    features: dict[str, bool]
    tokens: tuple[dict, ...]
    connections: tuple[dict, ...]


@dataclass
class Disagreement:
    kind: str
    source: str
    config: str
    codepoints: str
    expect: str
    detail: str

    def to_tsv(self) -> str:
        return "\t".join((self.kind, self.source, self.config, self.codepoints, self.expect, self.detail))


@dataclass
class ReplayReport:
    cells_total: int = 0
    runs_total: int = 0
    runs_replayed: int = 0
    skipped_junior: int = 0
    skipped_empty: int = 0
    skipped_non_basis: int = 0
    skipped_config: int = 0
    seam_assertions_checked: int = 0
    identity_assertions_checked: int = 0
    variant_assertions_skipped: int = 0
    baseline_rows_checked: int = 0
    baseline_windows_checked: int = 0
    disagreements: list[Disagreement] = field(default_factory=list)
    horizon_findings: list[Disagreement] = field(default_factory=list)

    @property
    def failure_count(self) -> int:
        return len(self.disagreements)


def collect_pin_runs(report: ReplayReport) -> list[PinRun]:
    ts = _import_test_shaping()
    pin_runs: list[PinRun] = []
    for rel in CORPUS_FILES:
        path = REPO_ROOT / rel
        collector = ts._DataExpectCollector()
        collector.feed(path.read_text(encoding="utf-8"))
        for _text, expect, line, stylistic_set, runs in collector.cells:
            if not expect or not expect.strip():
                continue
            report.cells_total += 1
            tokens, connections = ts.parse_expect(expect)
            cell_features = (
                {f"ss{ss.zfill(2)}": True for ss in stylistic_set.split()} if stylistic_set else {}
            )
            slices = ts._partition_by_runs(runs, tokens, connections)
            for sl in slices:
                if not sl["text"]:
                    continue
                report.runs_total += 1
                if sl["font"] != "senior":
                    report.skipped_junior += 1
                    continue
                merged = {**cell_features, **(sl.get("features") or {})}
                token = config_token_for_features(merged)
                if token is None:
                    report.skipped_config += 1
                    continue
                if any(ord(ch) not in ALPHABET_SET for ch in sl["text"]):
                    report.skipped_non_basis += 1
                    continue
                pin_runs.append(
                    PinRun(
                        source=f"{rel}:{line}",
                        expect=expect,
                        text=sl["text"],
                        config_token=token,
                        features=merged,
                        tokens=tuple(sl["tokens"]),
                        connections=tuple(sl["connections"]),
                    )
                )
    return pin_runs


def _expected_base(token: dict) -> str:
    if token["lig_base"]:
        return f"{token['base']}_{token['lig_base']}"
    return token["base"]


def _check_interpretation(
    text: str,
    tokens: list[dict],
    connections: list[dict],
    row: Row,
    report: ReplayReport,
) -> tuple[str | None, int, int, int]:
    """Check one maybe-ligature interpretation against a shaped row; returns (first failure or None, seam assertions, identity assertions, variant assertions skipped)."""
    ts = _import_test_shaping()
    spans = ts._token_char_spans(text, tokens)
    seam_checks = 0
    identity_checks = 0
    variant_skips = 0

    for i, token in enumerate(tokens):
        start, end = spans[i]
        glyph = row.glyphs[last_glyph_covering(row.clusters, start)]
        base = glyph.split(".")[0]
        expected = _expected_base(token)
        identity_checks += 1
        if base != expected:
            return (
                f"token {i}: expected base {expected}, got {glyph!r}",
                seam_checks,
                identity_checks,
                variant_skips,
            )
        if token["exact_glyph"] and glyph != expected:
            return (
                f"token {i}: expected exact glyph {expected}, got {glyph!r}",
                seam_checks,
                identity_checks,
                variant_skips,
            )
        name_parts = glyph.split(".")[1:]
        for v in token["variants"]:
            if v in ("half", "alt"):
                identity_checks += 1
                if v not in name_parts:
                    return (
                        f"token {i}: expected trait {v!r} in {glyph!r}",
                        seam_checks,
                        identity_checks,
                        variant_skips,
                    )
            else:
                variant_skips += 1
        for v in token.get("neg_variants", []):
            if v in ("half", "alt"):
                identity_checks += 1
                if v in name_parts:
                    return (
                        f"token {i}: trait {v!r} must not appear in {glyph!r}",
                        seam_checks,
                        identity_checks,
                        variant_skips,
                    )
            else:
                variant_skips += 1
        for k in range(start, end - 1):
            seam_checks += 1
            if row.seams[k] != "lig":
                return (
                    f"token {i}: expected ligature seam at {k}, got {row.seams[k]!r}",
                    seam_checks,
                    identity_checks,
                    variant_skips,
                )

    for i, conn in enumerate(connections):
        seam_index = spans[i + 1][0] - 1
        seam = row.seams[seam_index]
        kind = conn["kind"]
        if kind == "maybe":
            continue
        seam_checks += 1
        if kind == "height":
            if seam != f"y{conn['y']}":
                return (
                    f"connection {i}: expected y{conn['y']} at seam {seam_index}, got {seam!r}",
                    seam_checks,
                    identity_checks,
                    variant_skips,
                )
        elif kind == "join":
            if not seam.startswith("y"):
                return (
                    f"connection {i}: expected a join at seam {seam_index}, got {seam!r}",
                    seam_checks,
                    identity_checks,
                    variant_skips,
                )
        elif kind in ("break", "break_no_isolation"):
            if seam != "break":
                return (
                    f"connection {i}: expected break at seam {seam_index}, got {seam!r}",
                    seam_checks,
                    identity_checks,
                    variant_skips,
                )
        else:
            raise ValueError(f"unknown connection kind {kind!r}")

    return (None, seam_checks, identity_checks, variant_skips)


def check_pin(shaper: Shaper, classifier: SeamClassifier, pin: PinRun, report: ReplayReport) -> Row:
    ts = _import_test_shaping()
    row = row_for(shaper, classifier, pin.text, pin.features or None)
    interpretations = ts._expand_maybe_ligatures(list(pin.tokens), list(pin.connections))
    errors: list[str] = []
    for tokens, connections in interpretations:
        try:
            error, seam_checks, identity_checks, variant_skips = _check_interpretation(
                pin.text, tokens, connections, row, report
            )
        except ValueError as exc:
            errors.append(f"structural: {exc}")
            continue
        if error is None:
            report.runs_replayed += 1
            report.seam_assertions_checked += seam_checks
            report.identity_assertions_checked += identity_checks
            report.variant_assertions_skipped += variant_skips
            return row
        errors.append(error)
    report.runs_replayed += 1
    report.disagreements.append(
        Disagreement(
            kind="pin-live",
            source=pin.source,
            config=pin.config_token,
            codepoints=format_codepoints(row.codepoints),
            expect=pin.expect,
            detail=f"shaped {'|'.join(row.glyphs)} seams {','.join(row.seams)}; " + " // ".join(errors),
        )
    )
    return row


def check_against_baseline(
    pins_with_rows: list[tuple[PinRun, Row]],
    tables: dict[str, Path],
    report: ReplayReport,
) -> None:
    """Cross-check replayed pins against extracted baseline tables, one table per config token. Length ≤ 4 runs must match their table row byte-for-byte; longer runs only report seam differences for each embedded length-4 window (the accepted depth-2 horizon)."""
    from .rowmodel import iter_rows

    wanted: dict[str, dict[tuple[int, ...], list[tuple[PinRun, Row, int | None]]]] = {}
    for pin, row in pins_with_rows:
        table = tables.get(pin.config_token)
        if table is None:
            continue
        per_config = wanted.setdefault(pin.config_token, {})
        if len(row.codepoints) <= 4:
            per_config.setdefault(row.codepoints, []).append((pin, row, None))
        else:
            for j in range(len(row.codepoints) - 3):
                per_config.setdefault(row.codepoints[j : j + 4], []).append((pin, row, j))

    for token, queries in wanted.items():
        found: set[tuple[int, ...]] = set()
        for table_row in iter_rows(tables[token]):
            consumers = queries.get(table_row.codepoints)
            if not consumers:
                continue
            found.add(table_row.codepoints)
            for pin, live_row, window_start in consumers:
                if window_start is None:
                    report.baseline_rows_checked += 1
                    if table_row != live_row:
                        report.disagreements.append(
                            Disagreement(
                                kind="baseline-row-mismatch",
                                source=pin.source,
                                config=token,
                                codepoints=format_codepoints(live_row.codepoints),
                                expect=pin.expect,
                                detail=f"live: {live_row.to_tsv()} // table: {table_row.to_tsv()}",
                            )
                        )
                else:
                    report.baseline_windows_checked += 1
                    live_seams = live_row.seams[window_start : window_start + 3]
                    if live_seams != table_row.seams:
                        report.horizon_findings.append(
                            Disagreement(
                                kind="depth-2-horizon",
                                source=pin.source,
                                config=token,
                                codepoints=format_codepoints(table_row.codepoints),
                                expect=pin.expect,
                                detail=(
                                    f"window at {window_start}: long-context seams {','.join(live_seams)}"
                                    f" vs window-row seams {','.join(table_row.seams)}"
                                ),
                            )
                        )
        for codepoints, consumers in queries.items():
            if codepoints in found:
                continue
            for pin, _live_row, window_start in consumers:
                report.disagreements.append(
                    Disagreement(
                        kind="baseline-row-missing",
                        source=pin.source,
                        config=token,
                        codepoints=format_codepoints(codepoints),
                        expect=pin.expect,
                        detail="no table row for this string"
                        + ("" if window_start is None else f" (window at {window_start})"),
                    )
                )
