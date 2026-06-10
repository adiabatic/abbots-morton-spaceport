"""The section 6.3a explain CLI: replay settlement for a rune sequence and a stylistic-set configuration, printing the full candidate table per position, every elimination attributed to its file and record, and the rank comparison that chose the winner.

Usage: uv run python -m rebuild.pipeline.explain E665:E670:E665 --features ss03

Sequence positions are colon-separated and may be hex codepoints (E665, 0xE665, U+E665) or qs-names (qsMay), mixed freely; `space`, `zwnj`, and `namer-dot` name the boundary tokens. The CLI loads the real rune files through spec_load when that module exists; until then it falls back to the hand-built fixtures spec with a notice.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from rebuild.pipeline.model import ResolvedSpec, Settled, feature_config_token
from rebuild.pipeline.settle import Engine, TransitionTrace, cell_label, is_boundary_settled, settle_traces


@dataclass(frozen=True)
class PositionReport:
    index: int
    token: str
    trace: TransitionTrace


@dataclass(frozen=True)
class ExplainReport:
    spec: ResolvedSpec
    codepoints: tuple[int, ...]
    features: frozenset[str]
    positions: tuple[PositionReport, ...]

    @property
    def settled(self) -> tuple[Settled, ...]:
        return tuple(position.trace.settled for position in self.positions)

    def render(self) -> str:
        lines: list[str] = []
        sequence = ":".join(f"{cp:04X}" for cp in self.codepoints)
        lines.append(f"sequence {sequence}   config {feature_config_token(self.features)}")
        lines.append("settled: " + " ".join(cell_label(self.spec, s.cell) for s in self.settled))
        for position in self.positions:
            trace = position.trace
            settled = trace.settled
            lines.append("")
            lines.append(f"position {position.index}: {position.token}")
            if is_boundary_settled(settled):
                lines.append(
                    "  boundary token; splits run"
                    if settled.cell.rune in ("space", "zwnj")
                    else "  boundary token; does not split the run"
                )
                continue
            lines.append(f"  candidates (join-count = left seam + own seam + optimistic prospect):")
            for ranked in trace.ranked:
                candidate = ranked.candidate
                marker = (
                    "->"
                    if (candidate.stance, candidate.entry, candidate.seam)
                    == (settled.cell.stance, settled.cell.entry, settled.seam)
                    else "  "
                )
                entry = candidate.entry or "none"
                seam = candidate.seam or "none"
                lines.append(
                    f"  {marker} {candidate.stance:<16} entry={entry:<10} seam={seam:<10} join-count={ranked.join_count} prospect={ranked.prospect}"
                )
            if trace.eliminations:
                lines.append("  eliminated before ranking:")
                for elimination in trace.eliminations:
                    source = f"  [{elimination.provenance}]" if elimination.provenance else ""
                    lines.append(f"    - ({elimination.stage}) {elimination.description}{source}")
            decided = f"  decided by: {trace.decided_stage}"
            if trace.runner_up is not None:
                runner = trace.runner_up
                decided += (
                    f" (over {runner.stance} entry={runner.entry or 'none'} seam={runner.seam or 'none'})"
                )
            lines.append(decided)
            if trace.joint_floor:
                lines.append(
                    "  joint: the structural floor broke a realization tie — routed to the expensive test tier"
                )
            for note in trace.notes:
                lines.append(f"  note: {note}")
            lines.append(
                f"  settled: {cell_label(self.spec, settled.cell)}   seam={settled.seam or 'none'}   extension={settled.extension}"
            )
        return "\n".join(lines)


def explain(spec: ResolvedSpec, codepoints: Sequence[int], features: frozenset[str]) -> ExplainReport:
    engine = Engine(spec, frozenset(features))
    traces = settle_traces(engine, codepoints)
    tokens = _position_tokens(spec, traces)
    positions = tuple(
        PositionReport(index, token, trace) for index, (token, trace) in enumerate(zip(tokens, traces))
    )
    return ExplainReport(
        spec=spec, codepoints=tuple(codepoints), features=frozenset(features), positions=positions
    )


def _position_tokens(spec: ResolvedSpec, traces: Sequence[TransitionTrace]) -> list[str]:
    return [
        trace.settled.cell.rune if not is_boundary_settled(trace.settled) else trace.settled.cell.rune
        for trace in traces
    ]


def parse_sequence(spec: ResolvedSpec, text: str) -> list[int]:
    by_name = {
        name: info.codepoint for name, info in spec.registry.families.items() if info.codepoint is not None
    }
    boundary_by_name = {name: token.codepoint for name, token in spec.registry.boundary_tokens.items()}
    codepoints: list[int] = []
    for part in text.split(":"):
        part = part.strip()
        if not part:
            continue
        if part in by_name:
            codepoints.append(by_name[part])
            continue
        if part in boundary_by_name:
            codepoints.append(boundary_by_name[part])
            continue
        cleaned = part
        for prefix in ("U+", "u+", "0x", "0X"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]
                break
        try:
            codepoints.append(int(cleaned, 16))
        except ValueError:
            raise SystemExit(
                f"cannot parse sequence position {part!r}: not a qs-name, boundary token, or hex codepoint"
            )
    return codepoints


def _load_spec() -> tuple[ResolvedSpec, str | None]:
    try:
        from pathlib import Path

        from rebuild.pipeline import spec_load  # noqa: PLC0415

        repo = Path(__file__).resolve().parents[2]
        spec = spec_load.load_spec(
            repo / "glyph_data" / "runes", repo / "rebuild" / "script.yaml", repo / "rebuild" / "schema"
        )
        return spec, None
    except (
        Exception
    ) as error:  # noqa: BLE001 — the fixtures fallback is the documented pre-integration behavior
        from rebuild.pipeline import fixtures

        return (
            fixtures.mini_spec(),
            f"spec_load unavailable ({type(error).__name__}: {error}); using the hand-built fixtures spec",
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Replay settlement for a rune sequence, printing the full candidate table per position."
    )
    parser.add_argument(
        "sequence",
        help="colon-separated positions: hex codepoints or qs-names, e.g. E665:E670:E665 or qsMay:qsIt:qsMay",
    )
    parser.add_argument(
        "--features",
        action="append",
        default=[],
        help="active stylistic sets, comma-separable, e.g. --features ss03 or --features ss02,ss03",
    )
    args = parser.parse_args(argv)
    features = frozenset(tag for chunk in args.features for tag in chunk.split(",") if tag)
    spec, notice = _load_spec()
    if notice:
        print(f"note: {notice}")
    report = explain(spec, parse_sequence(spec, args.sequence), features)
    print(report.render())


if __name__ == "__main__":
    main()
