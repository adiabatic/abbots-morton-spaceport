"""The golden single-window settlement corpus (issue #41): one replayed settlement window per line, the arguments the kernel was called with beside the record it produced, so a Rust settlement core can be differentially tested against the Python oracle case by case. Nothing it writes is ever checked in — `rebuild/out/` is gitignored by design, and the oracle is whatever HEAD settles today, so the corpus is regenerated whenever the thing it measures moves rather than frozen as a fixture that would drift out of agreement with the spec it was cut from.

Layout follows the enumeration artifacts beside it: gzip with a zeroed stamp, a `# ams-m1-corpus/1\t{head json}` marker line naming the configuration, the spec the cases were cut from, and `trace_memo.spec_structure_digest` of it, then one JSON object per line, deduplicated and sorted, so two runs at one HEAD are byte-identical. A case carries the whole call — the left context (its kind, plus the full settled triple wherever the left is a letter), the input rune, and the four right tokens — and exactly one result: the row-visible trace record (the settled triple, the prospect, the joint floor, the provenance notes, and the fired-pointer delta the evaluation journaled) or the raise identity, `E-INCOMPARABLE`, `E-AMBIGUOUS`, or the unreachable-window bucket every other `SettleError` falls into. The fired delta rides every settled case because it is the field no downstream artifact re-derives: `DecisionTable.cited_provenance` is the union of exactly these deltas, so a port that settles every window correctly and journals the wrong records still builds a table the dead-policy gate rejects. Raising cases carry no delta — the kernel aborts its capture on the way out.

Sampling is deterministic, and runs in two arms because the kernel's two answer shapes live in different places. Settled cases replay a stratified sample of the fixpoint's own rows: `enumerate_transitions`' key-sorted stream grouped per (input family, left kind, identity-vs-moved outcome, each deep slot's liveness on its own, the joint floor, whether notes fired) with the first `--per-group` rows of each group kept, so every shape of window the enumeration reaches — depth-4 rows, flagged seams and note-carrying rows included, none of which the cheap key-order prefix would reach on its own — is represented while the corpus stays a sample rather than a second copy of the table. Each replay's arguments are reconstructed from the row it came from — the left from `left_settled` where it has one and from `table.BOUNDARY_LEFT_LABELS` where it does not, a deep class id at either deep slot through its representative member, and a `#NA` slot as `EDGE`, which is exactly what the enumeration handed the kernel wherever it recorded `#NA`. A sampled row that raises on replay aborts the export: the row came from the enumeration, so a raise there is a reconstruction defect, never a case. Raising cases come from where no enumerated row can reach: the virtual lefts `table._ProspectLiveness` probes with, crossed with its probe alphabet at the two nearer slots and `EDGE` at the deep ones. Both surfaces are reused rather than copied (`_seat_left_classes`, `_probe_tokens`), so the corpus's left collapse and probe alphabet are the build's own; the walk records every window that raises and a per-family cap (`--per-family`) stops it.

`read_corpus` is the reader side of the same contract, and the one place the format marker is checked; the differential harness reads a file through it rather than re-deriving the layout.

Run as: uv run python -m rebuild.tools.export_settlement_corpus [--spec {live,mini}] [--configs CONFIG ...] [--out DIR] [--per-group N] [--per-family N]
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
from pathlib import Path
from typing import Iterator

from rebuild.pipeline import conform, fixtures, trace_memo
from rebuild.pipeline import table as table_module
from rebuild.pipeline.model import CellId, ResolvedSpec, Settled
from rebuild.pipeline.run_m1 import REPO_ROOT
from rebuild.pipeline.settle import (
    EDGE,
    NAMER_DOT,
    SPACE,
    ZWNJ,
    Engine,
    LeftContext,
    RightToken,
    SettleError,
)
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline.specificity import EAmbiguousError, EIncomparableError
from rebuild.pipeline.table import BOUNDARY_LEFT_LABELS, NA_LABEL, FixpointProduct

CORPUS_FORMAT = "ams-m1-corpus/1"
CORPUS_DIR = REPO_ROOT / "rebuild" / "out" / "kernel-corpus"
DEFAULT_CONFIGS = ("default", "ss03", "ss10")
RAISE_INCOMPARABLE = "E-INCOMPARABLE"
RAISE_AMBIGUOUS = "E-AMBIGUOUS"
RAISE_UNREACHABLE = "E-UNREACHABLE"

_BOUNDARY_KIND_OF_LABEL = {label: kind for kind, label in BOUNDARY_LEFT_LABELS.items()}
_BOUNDARY_TOKENS = {"edge": EDGE, "space": SPACE, "zwnj": ZWNJ, "namer-dot": NAMER_DOT}


def corpus_path(out_dir: Path, config: str) -> Path:
    return Path(out_dir) / f"corpus-{config}.ndjson.gz"


def _cell_row(cell: CellId) -> list:
    return [cell.rune, cell.stance, cell.entry, cell.exit, list(cell.adjustments)]


def _settled_row(settled: Settled) -> dict:
    return {"cell": _cell_row(settled.cell), "seam": settled.seam, "extension": settled.extension}


def _token_row(token: RightToken) -> dict:
    return {"kind": token.kind, "letter": token.rune}


def _left_row(left: LeftContext) -> dict:
    return {
        "kind": left.kind,
        "settled": _settled_row(left.settled) if left.settled is not None else None,
    }


def _memo_key(
    left: LeftContext,
    token: RightToken,
    right1: RightToken,
    right2: RightToken,
    right3: RightToken,
    right4: RightToken,
) -> tuple:
    """The in-memory memo key `settle.Engine.transition_trace` files a computed trace under — the collapsed left plus the five raw tokens. Recomputing it here is what lets a case carry the fired-pointer delta that evaluation journaled, `_trace_fired` being keyed on it; `_replay` treats a missing entry as an error rather than an empty delta, so the day that key shape moves this tool says so instead of quietly exporting no provenance."""
    settled = left.settled
    return (
        left.kind,
        settled.cell.rune if settled is not None else None,
        settled.cell.stance if settled is not None else None,
        settled.seam if settled is not None else None,
        settled.extension if settled is not None else 0,
        token.rune,
        right1,
        right2,
        right3,
        right4,
    )


def _replay(
    engine: Engine, left: LeftContext, token: RightToken, rights: tuple[RightToken, ...]
) -> tuple[dict, bool]:
    """One case's result and whether it raised: the row-visible trace record with its fired delta, or the raise identity alone."""
    right1, right2, right3, right4 = rights
    try:
        trace = engine.transition_trace(left, token, right1, right2, right3, right4)
    except EIncomparableError:
        return {"raise": RAISE_INCOMPARABLE}, True
    except EAmbiguousError:
        return {"raise": RAISE_AMBIGUOUS}, True
    except SettleError:
        return {"raise": RAISE_UNREACHABLE}, True
    key = _memo_key(left, token, right1, right2, right3, right4)
    fired = engine._trace_fired.get(key)
    if fired is None:
        raise SystemExit(
            f"the settled case {key} left no journaled fired delta — settle.Engine.transition_trace's memo key has moved and _memo_key must follow"
        )
    return {
        "settled": _settled_row(trace.settled),
        "prospect": trace.prospect,
        "joint_floor": trace.joint_floor,
        "notes": list(trace.notes),
        "fired": list(fired),
    }, False


def _case_row(left: LeftContext, family: str, rights: tuple[RightToken, ...], result: dict) -> dict:
    return {
        "left": _left_row(left),
        "input": family,
        "right": [_token_row(token) for token in rights],
        "result": result,
    }


def _left_of(row: table_module.Transition) -> LeftContext:
    if row.left_settled is not None:
        return LeftContext("letter", row.left_settled)
    return LeftContext(_BOUNDARY_KIND_OF_LABEL[row.left])


def _token_of(label: str, decision: table_module.DecisionTable) -> RightToken:
    if label == NA_LABEL:
        return EDGE
    kind = _BOUNDARY_KIND_OF_LABEL.get(label)
    if kind is not None:
        return _BOUNDARY_TOKENS[kind]
    return RightToken("letter", decision.token_representative(label))


def settled_cases(engine: Engine, product: FixpointProduct, per_group: int) -> list[dict]:
    """The settled arm: the first `per_group` rows of each stratum of the fixpoint's key-sorted stream, replayed through the corpus engine so each case carries its own fired delta."""
    decision = table_module.DecisionTable(config=product.config, deep_classes=product.deep_classes)
    counts: dict[tuple, int] = {}
    rows: list[dict] = []
    for row in product.transitions:
        family = row.input_glyph.split(".")[0]
        left = _left_of(row)
        key = (
            family,
            left.kind,
            row.is_identity,
            row.right3 != NA_LABEL,
            row.right4 != NA_LABEL,
            row.joint,
            bool(row.provenance),
        )
        taken = counts.get(key, 0)
        if taken >= per_group:
            continue
        counts[key] = taken + 1
        rights = tuple(
            _token_of(label, decision) for label in (row.right1, row.right2, row.right3, row.right4)
        )
        result, raised = _replay(engine, left, RightToken("letter", family), rights)
        if raised:
            raise SystemExit(
                f"the enumerated window {row.key} raised on replay — the case reconstruction no longer hands the kernel the call the enumeration made"
            )
        rows.append(_case_row(left, family, rights, result))
    return rows


def _probe_windows(
    liveness: table_module._ProspectLiveness, family: str
) -> Iterator[tuple[LeftContext, tuple[RightToken, ...]]]:
    """Every virtual-left window the raising arm probes for one input family, in a fixed order: the collapsed left classes crossed with the probe alphabet at both nearer slots, the deep slots held at EDGE."""
    tokens = liveness._probe_tokens()
    for left in liveness._seat_left_classes(family):
        for right1 in tokens:
            for right2 in tokens:
                yield left, (right1, right2, EDGE, EDGE)


def raising_cases(spec: ResolvedSpec, engine: Engine, per_family: int) -> list[dict]:
    """The raising arm: virtual lefts probed the way the deep-slot liveness filters probe them, recording every window that raises until the per-family cap stops the family's walk."""
    liveness = table_module._ProspectLiveness(spec, engine)
    rows: list[dict] = []
    for family in sorted(spec.runes):
        token = RightToken("letter", family)
        recorded = 0
        for left, rights in _probe_windows(liveness, family):
            if recorded >= per_family:
                break
            result, raised = _replay(engine, left, token, rights)
            if not raised:
                continue
            rows.append(_case_row(left, family, rights, result))
            recorded += 1
    return rows


def write_corpus(head: dict, cases: list[dict], path: Path) -> None:
    lines = sorted({json.dumps(case, separators=(",", ":")) for case in cases})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle:
        handle.write(f"# {CORPUS_FORMAT}\t{json.dumps(head, separators=(',', ':'))}\n".encode())
        for line in lines:
            handle.write((line + "\n").encode())


def read_corpus(path: Path) -> tuple[dict, list[dict]]:
    """The `write_corpus` inverse: the head and every case, with the format marker checked. Raises ValueError on a file this build does not understand and OSError when it is absent."""
    with gzip.open(path, "rt") as handle:
        marker, _, payload = handle.readline().rstrip("\n").partition("\t")
        if marker != f"# {CORPUS_FORMAT}":
            raise ValueError(f"{path}: not a {CORPUS_FORMAT} corpus")
        head = json.loads(payload)
        cases = [json.loads(line) for line in handle]
    return head, cases


def export_config(
    spec: ResolvedSpec,
    spec_name: str,
    config: str,
    out_dir: Path,
    per_group: int,
    per_family: int,
) -> tuple[Path, int, int]:
    """One configuration's corpus file, and the (settled, raising) case counts behind it."""
    features = conform.features_for_config(config)
    product = table_module.enumerate_transitions(spec, features)
    engine = Engine(spec, features, trace_memo=True)
    settled = settled_cases(engine, product, per_group)
    raising = raising_cases(spec, engine, per_family)
    head = {
        "config": config,
        "spec": spec_name,
        "spec_structure_digest": trace_memo.spec_structure_digest(spec),
    }
    path = corpus_path(out_dir, config)
    write_corpus(head, settled + raising, path)
    return path, len(settled), len(raising)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export the golden single-window settlement corpus.")
    parser.add_argument("--out", type=Path, default=CORPUS_DIR, help="directory the corpus files land in")
    parser.add_argument(
        "--configs",
        nargs="+",
        default=None,
        help="feature configurations to export (default: the acceptance configurations among default, ss03, ss10)",
    )
    parser.add_argument("--spec", choices=("live", "mini"), default="live", help="which spec to cut from")
    parser.add_argument("--per-group", type=int, default=2, help="settled cases kept per stratum")
    parser.add_argument("--per-family", type=int, default=4, help="raising cases kept per input family")
    args = parser.parse_args(argv)
    spec = fixtures.mini_spec() if args.spec == "mini" else load_default_spec()
    configs = (
        tuple(args.configs)
        if args.configs is not None
        else tuple(config for config in DEFAULT_CONFIGS if config in conform.ACCEPTANCE_CONFIGS)
    )
    start = time.perf_counter()
    for config in configs:
        path, settled, raising = export_config(
            spec, args.spec, config, args.out, args.per_group, args.per_family
        )
        print(f"{config}: {settled} settled and {raising} raising cases -> {path}", flush=True)
    print(f"[t] export_settlement_corpus_total {time.perf_counter() - start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
