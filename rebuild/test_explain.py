"""Tests for the section 6.3a explain CLI: sequence parsing, the Rust-backed per-position candidate table, elimination attribution to file and record, and the rank-comparison line."""

from rebuild.pipeline import explain as explain_module
from rebuild.pipeline import fixtures, kernel_exec
from rebuild.pipeline.explain import ExplainReport, PositionReport, explain, explain_many, parse_sequence
from rebuild.pipeline.model import CellId, Provenance, Settled
from rebuild.pipeline.settle import Candidate, Elimination, RankedCandidate, TransitionTrace, boundary_settled

SPEC = fixtures.mini_spec()


def test_parse_sequence_accepts_names_hex_and_boundaries():
    assert parse_sequence(SPEC, "qsMay:qsIt:qsMay") == [0xE665, 0xE670, 0xE665]
    assert parse_sequence(SPEC, "E665:0xE670:U+E665") == [0xE665, 0xE670, 0xE665]
    assert parse_sequence(SPEC, "qsIt:zwnj:qsTea") == [0xE670, 0x200C, 0xE652]


def test_report_settles_and_renders_candidates():
    report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsIt:qsMay"), frozenset())
    text = report.render()
    assert "qsMay.loop.ex-y5.ex-ext-1" in text
    assert "qsIt.hapax.en-y5.ex-y0.ex-ext-1" in text
    assert "join-count" in text
    assert "decided by:" in text


def test_eliminations_are_attributed_to_records():
    # qsMay's grounded baseline exit toward qsIt dies to the authored refusal; the report names the record's file and key path.
    report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsIt"), frozenset())
    text = report.render()
    assert "glyph_data/runes/qsMay.yaml:policy.refuse[0]" in text
    assert "(refuse)" in text


def test_feature_configuration_changes_the_outcome():
    default_report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsTea"), frozenset())
    ss03_report = explain(SPEC, parse_sequence(SPEC, "qsMay:qsTea"), frozenset({"ss03"}))
    assert "qsMay.loop.ex-bind-pulled-back" in default_report.render()
    assert "qsTea.half.en-y5" in ss03_report.render()
    assert "config ss03" in ss03_report.render()


def test_boundary_positions_render():
    report = explain(SPEC, parse_sequence(SPEC, "qsIt:zwnj:qsTea"), frozenset())
    text = report.render()
    assert "boundary token" in text
    assert "qsTea.full.locked" in text


def test_an_overlay_configuration_explains_the_bare_stream_without_the_crate(monkeypatch):
    """Under ss10 nothing settles, so the report is the registry's answer — every letter its default-stance cell with no seam, a ligature's components unformed, boundaries as boundaries — decided by the overlay stage, rendered as such, and reached without a kernel invocation; a batch that mixes overlay and settling requests keeps its order."""
    from rebuild.pipeline.settle import ISOLATED_OVERLAY_STAGE

    crate = kernel_exec.settle_sequences

    def only_for_settling(spec, requests, **rest):
        assert all(not features for _tokens, features in requests)
        return crate(spec, requests, **rest)

    monkeypatch.setattr(kernel_exec, "settle_sequences", only_for_settling)
    codepoints = parse_sequence(SPEC, "qsTea:qsOy:zwnj:qsIt")
    overlay, plain = explain_many(SPEC, [(codepoints, frozenset({"ss10"})), (codepoints, frozenset())])
    assert overlay.features == frozenset({"ss10"}) and plain.features == frozenset()
    assert [item.cell.rune for item in overlay.settled] == ["qsTea", "qsOy", "zwnj", "qsIt"]
    assert all(item.seam is None and item.extension == 0 for item in overlay.settled)
    assert [position.trace.decided_stage for position in overlay.positions] == [
        ISOLATED_OVERLAY_STAGE,
        ISOLATED_OVERLAY_STAGE,
        "boundary",
        ISOLATED_OVERLAY_STAGE,
    ]
    assert [item.cell.rune for item in plain.settled] == ["qsTea_qsOy", "zwnj", "qsIt"]
    text = overlay.render()
    assert "config ss10" in text and "isolated overlay" in text and "join-count" not in text


def test_cli_prints_the_rust_backed_report(monkeypatch, capsys):
    monkeypatch.setattr(explain_module, "_load_spec", lambda: (SPEC, None))
    explain_module.main(["qsMay:qsIt"])
    output = capsys.readouterr().out
    assert output.startswith("sequence E665:E670")
    assert "glyph_data/runes/qsMay.yaml:policy.refuse[0]" in output


def _panel_report() -> ExplainReport:
    """Every line `render` can emit, assembled by hand: a letter position with a ranked ladder, an elimination carrying a record pointer and one carrying none, a joint floor, a note, and a runner-up; a boundary position that splits the run; a letter position that was the only candidate; and a boundary position that does not."""
    loop = Candidate("loop", None, "x-height", 0, 0)
    grounded = Candidate("grounded", None, "baseline", 1, 1)
    hapax = Candidate("hapax", "x-height", None, 0, 0)
    first = TransitionTrace(
        settled=Settled(CellId("qsMay", "loop", None, "x-height", ("ex-ext-1",)), "x-height", 1),
        joint_floor=True,
        prospect=1,
        ranked=(RankedCandidate(loop, 2, 1), RankedCandidate(grounded, 1, 0)),
        eliminations=(
            Elimination(
                "refuse",
                "qsMay.grounded: exit baseline refused — the grounded tail cannot reach",
                Provenance("glyph_data/runes/qsMay.yaml", "policy.refuse[0]"),
            ),
            Elimination("require", "qsMay.pulled_back: requires a live entry"),
        ),
        decided_stage="floor",
        runner_up=grounded,
        notes=("prefer applied: glyph_data/runes/qsMay.yaml:policy.prefer[1]",),
    )
    boundary = TransitionTrace(boundary_settled("zwnj"), False, 0, (), (), "boundary", None, ())
    unsplitting = TransitionTrace(boundary_settled("namer-dot"), False, 0, (), (), "boundary", None, ())
    third = TransitionTrace(
        settled=Settled(CellId("qsIt", "hapax", "x-height", None, ()), None, 0),
        joint_floor=False,
        prospect=0,
        ranked=(RankedCandidate(hapax, 1, 0),),
        eliminations=(),
        decided_stage="only-candidate",
        runner_up=None,
        notes=(),
    )
    return ExplainReport(
        spec=SPEC,
        codepoints=(0xE665, 0x200C, 0xE670, 0x00B7),
        features=frozenset({"ss03"}),
        positions=(
            PositionReport(0, "qsMay", first),
            PositionReport(1, "zwnj", boundary),
            PositionReport(2, "qsIt", third),
            PositionReport(3, "namer-dot", unsplitting),
        ),
    )


PANEL = """\
sequence E665:200C:E670:00B7   config ss03
settled: qsMay.loop.ex-y5.ex-ext-1 uni200C qsIt.hapax.en-y5 periodcentered

position 0: qsMay
  candidates (join-count = left seam + own seam + optimistic prospect):
  -> loop             entry=none       seam=x-height   join-count=2 prospect=1
     grounded         entry=none       seam=baseline   join-count=1 prospect=0
  eliminated before ranking:
    - (refuse) qsMay.grounded: exit baseline refused — the grounded tail cannot reach  [glyph_data/runes/qsMay.yaml:policy.refuse[0]]
    - (require) qsMay.pulled_back: requires a live entry
  decided by: floor (over grounded entry=none seam=baseline)
  joint: the structural floor broke a realization tie — routed to the expensive test tier
  note: prefer applied: glyph_data/runes/qsMay.yaml:policy.prefer[1]
  settled: qsMay.loop.ex-y5.ex-ext-1   seam=x-height   extension=1

position 1: zwnj
  boundary token; splits run

position 2: qsIt
  candidates (join-count = left seam + own seam + optimistic prospect):
  -> hapax            entry=x-height   seam=none       join-count=1 prospect=0
  decided by: only-candidate
  settled: qsIt.hapax.en-y5   seam=none   extension=0

position 3: namer-dot
  boundary token; does not split the run"""


def test_a_report_renders_every_line_the_panel_reads():
    """The rendering is the whole author-facing product of this module, and every line of it is reachable from literal trace values — no kernel, no spec load. Pinning the exact string is what catches a stray space, a reordered field, or a line that quietly stopped being emitted."""
    assert _panel_report().render() == PANEL


def test_explain_many_batches_same_config_sequences_by_position(monkeypatch):
    """The waves are `kernel_exec.settle_sequences`', and what they cost is one invocation per feature configuration per position — not one per sequence — which is why a surface build explaining thousands of units is affordable at all."""
    calls: list[tuple[frozenset[str], int]] = []
    original = kernel_exec.settle_cases

    def recording(spec, cases, features, *, modes=None, decode=None):
        calls.append((features, len(cases)))
        return original(spec, cases, features, modes, decode)

    monkeypatch.setattr(kernel_exec, "settle_cases", recording)
    requests = [
        (parse_sequence(SPEC, "qsMay:qsIt:qsMay"), frozenset()),
        (parse_sequence(SPEC, "qsIt:zwnj:qsTea"), frozenset()),
        (parse_sequence(SPEC, "qsMay:qsTea"), frozenset({"ss03"})),
    ]
    reports = explain_many(SPEC, requests)
    assert [report.codepoints for report in reports] == [tuple(codepoints) for codepoints, _ in requests]
    assert calls == [
        (frozenset(), 2),
        (frozenset({"ss03"}), 1),
        (frozenset(), 1),
        (frozenset({"ss03"}), 1),
        (frozenset(), 2),
    ]
