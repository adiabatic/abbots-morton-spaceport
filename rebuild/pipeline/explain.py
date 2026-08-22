"""The section 6.3a explain CLI: replay settlement for a rune sequence and a stylistic-set configuration, printing the full candidate table per position, every elimination attributed to its file and record, and the rank comparison that chose the winner.

Usage: uv run python -m rebuild.pipeline.explain E665:E670:E665 --features ss03

Sequence positions are colon-separated and may be hex codepoints (E665, 0xE665, U+E665) or qs-names (qsMay), mixed freely; `space`, `zwnj`, and `namer-dot` name the boundary tokens. The CLI loads the real rune files through spec_load, falling back to the hand-built fixtures spec with a notice when the loader is unavailable.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from rebuild.pipeline import kernel_exec
from rebuild.pipeline.model import CellId, Provenance, ResolvedSpec, Settled, feature_config_token
from rebuild.pipeline.settle import (
    EDGE,
    Candidate,
    Elimination,
    LeftContext,
    RankedCandidate,
    RightToken,
    TransitionTrace,
    boundary_settled,
    cell_label,
    form_ligatures,
    is_boundary_settled,
    tokens_from_codepoints,
)

SETTLE_CASE_BATCH_SIZE = 2048


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
    return explain_many(spec, [(codepoints, features)])[0]


def explain_many(
    spec: ResolvedSpec,
    requests: Sequence[tuple[Sequence[int], frozenset[str]]],
    guard_verdicts: Mapping[tuple[str, RightToken, RightToken], bool] | None = None,
) -> list[ExplainReport]:
    """Settle a batch of sequences through the Rust kernel, grouping every same-depth window by feature configuration so a surface build pays for a handful of `settle-cases` processes rather than one process per review unit.

    The verb accepts independent windows, while a sequence's next left context is the previous window's answer. The batch therefore advances in waves: all first positions, then all second positions using the first wave's Rust results, and so on. Boundary positions are deterministic model tokens and reset the left locally; every letter trace, including the settled cell fed into the next wave, comes from the crate.
    """
    if not requests:
        return []
    if guard_verdicts is None:
        guard_verdicts = kernel_exec.guard_sweep(spec)
    states = [
        _SequenceState(
            codepoints=tuple(codepoints),
            features=frozenset(features),
            tokens=tuple(form_ligatures(spec, tokens_from_codepoints(spec, codepoints), guard_verdicts)),
        )
        for codepoints, features in requests
    ]
    max_positions = max((len(state.tokens) for state in states), default=0)
    for position in range(max_positions):
        batches: dict[frozenset[str], list[tuple[_SequenceState, dict]]] = {}
        for state in states:
            if position >= len(state.tokens):
                continue
            token = state.tokens[position]
            if token.kind != "letter":
                trace = TransitionTrace(boundary_settled(token.kind), False, 0, (), (), "boundary", None, ())
                state.traces.append(trace)
                state.left = LeftContext(token.kind)
                continue
            rights = tuple(
                state.tokens[index] if index < len(state.tokens) else EDGE
                for index in range(position + 1, position + 5)
            )
            case = _case_row(state.left, token, rights)
            batches.setdefault(state.features, []).append((state, case))
        for features, pending in batches.items():
            for start in range(0, len(pending), SETTLE_CASE_BATCH_SIZE):
                chunk = pending[start : start + SETTLE_CASE_BATCH_SIZE]
                answers = kernel_exec.settle_cases(spec, [case for _state, case in chunk], features)
                for (state, _case), answer in zip(chunk, answers):
                    trace = _trace_of(answer["result"])
                    state.traces.append(trace)
                    state.left = LeftContext("letter", trace.settled)
    return [_report_of(spec, state) for state in states]


@dataclass
class _SequenceState:
    codepoints: tuple[int, ...]
    features: frozenset[str]
    tokens: tuple[RightToken, ...]
    traces: list[TransitionTrace] = field(default_factory=list)
    left: LeftContext = LeftContext("edge")


def _report_of(spec: ResolvedSpec, state: _SequenceState) -> ExplainReport:
    traces = state.traces
    tokens = _position_tokens(spec, traces)
    positions = tuple(
        PositionReport(index, token, trace) for index, (token, trace) in enumerate(zip(tokens, traces))
    )
    return ExplainReport(spec=spec, codepoints=state.codepoints, features=state.features, positions=positions)


def _token_row(token: RightToken) -> dict:
    return {"kind": token.kind, "letter": token.rune}


def _settled_row(settled: Settled) -> dict:
    cell = settled.cell
    return {
        "cell": [cell.rune, cell.stance, cell.entry, cell.exit, list(cell.adjustments)],
        "seam": settled.seam,
        "extension": settled.extension,
    }


def _case_row(left: LeftContext, token: RightToken, rights: tuple[RightToken, ...]) -> dict:
    return {
        "left": {
            "kind": left.kind,
            "settled": _settled_row(left.settled) if left.settled is not None else None,
        },
        "input": token.rune,
        "right": [_token_row(right) for right in rights],
        "result": None,
    }


def _candidate_of(row) -> Candidate:
    if not isinstance(row, list) or len(row) != 5:
        raise kernel_exec.KernelRunError(f"settle-cases returned a malformed candidate: {row!r}")
    stance, entry, seam, order_index, exit_index = row
    return Candidate(stance, entry, seam, order_index, exit_index)


def _settled_of(row) -> Settled:
    if not isinstance(row, Mapping) or set(row) != {"cell", "seam", "extension"}:
        raise kernel_exec.KernelRunError(f"settle-cases returned a malformed settled result: {row!r}")
    cell_row = row["cell"]
    if not isinstance(cell_row, list) or len(cell_row) != 5 or not isinstance(cell_row[4], list):
        raise kernel_exec.KernelRunError(f"settle-cases returned a malformed cell: {cell_row!r}")
    cell = CellId(cell_row[0], cell_row[1], cell_row[2], cell_row[3], tuple(cell_row[4]))
    return Settled(cell, row["seam"], row["extension"])


def _provenance_of(pointer) -> Provenance | None:
    if pointer is None:
        return None
    if not isinstance(pointer, str) or ":" not in pointer:
        raise kernel_exec.KernelRunError(f"settle-cases returned a malformed provenance pointer: {pointer!r}")
    file, path = pointer.rsplit(":", 1)
    return Provenance(file, path)


def _trace_of(result) -> TransitionTrace:
    if not isinstance(result, Mapping):
        raise kernel_exec.KernelRunError(f"settle-cases returned a malformed result: {result!r}")
    if "raise" in result:
        raise kernel_exec.KernelRunError(
            f"settle-cases raised {result.get('raise')}: {result.get('message', 'no message')}"
        )
    expected = {
        "settled",
        "prospect",
        "joint_floor",
        "notes",
        "fired",
        "decided_stage",
        "runner_up",
        "ranked",
        "eliminations",
    }
    if set(result) != expected:
        raise kernel_exec.KernelRunError(
            f"settle-cases returned trace fields {sorted(result)}, expected {sorted(expected)}"
        )
    for field_name in ("notes", "fired", "ranked", "eliminations"):
        if not isinstance(result[field_name], list):
            raise kernel_exec.KernelRunError(
                f"settle-cases returned a malformed {field_name} field: {result[field_name]!r}"
            )
    ranked = tuple(
        RankedCandidate(_candidate_of(row[0]), row[1], row[2])
        for row in result["ranked"]
        if isinstance(row, list) and len(row) == 3
    )
    if len(ranked) != len(result["ranked"]):
        raise kernel_exec.KernelRunError("settle-cases returned a malformed ranked ladder")
    eliminations = tuple(
        Elimination(row[0], row[1], _provenance_of(row[2]))
        for row in result["eliminations"]
        if isinstance(row, list) and len(row) == 3
    )
    if len(eliminations) != len(result["eliminations"]):
        raise kernel_exec.KernelRunError("settle-cases returned malformed eliminations")
    runner_up = None if result["runner_up"] is None else _candidate_of(result["runner_up"])
    return TransitionTrace(
        settled=_settled_of(result["settled"]),
        joint_floor=result["joint_floor"],
        prospect=result["prospect"],
        ranked=ranked,
        eliminations=eliminations,
        decided_stage=result["decided_stage"],
        runner_up=runner_up,
        notes=tuple(result["notes"]),
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
    except Exception as error:  # noqa: BLE001 — the fixtures fallback is deliberate
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
