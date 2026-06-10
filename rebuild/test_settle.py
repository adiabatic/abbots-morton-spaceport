"""Settlement-kernel tests over the real M1 rune data (rebuild/pipeline/fixtures.py) plus small synthetic specs for the stages the real records leave unexercised (prefers, the structural-floor joint flag, bind contracts).

Expectations marked AUTHORED-DATA FINDING assert the authored rune files' actual semantics where they knowingly diverge from today's font (the qsMay grounded exit is unscoped and its refusal list lacks qsTea; the qsMay baseline entry extension's trigger list lacks qsTea_qsOy; qsMay withdraws its exit stub mid-word). Those rows are divergence-ledger material for Phase 5, not kernel bugs — see the Deviations section appended to rebuild/M1-PLAN.md.
"""

import pytest

from rebuild.pipeline import fixtures
from rebuild.pipeline.model import (
    BoundaryToken,
    CellId,
    Condition,
    FamilyInfo,
    Pairing,
    Pairings,
    Policy,
    PolicyRecord,
    ResolvedSpec,
    Rune,
    ScriptRegistry,
    Settled,
    Stance,
    Surface,
    SurfaceRow,
    When,
)
from rebuild.pipeline.settle import (
    EDGE,
    Engine,
    EStrandedError,
    LeftContext,
    RightToken,
    cell_label,
    is_entry_bearing,
    settle,
    word_position,
)

SPEC = fixtures.mini_spec()

NAME_TO_CODEPOINT = {
    name: info.codepoint for name, info in SPEC.registry.families.items() if info.codepoint is not None
}
NAME_TO_CODEPOINT.update({name: token.codepoint for name, token in SPEC.registry.boundary_tokens.items()})


def run(sequence: str, features=()) -> tuple[str, ...]:
    codepoints = [NAME_TO_CODEPOINT[name] for name in sequence.split()]
    return tuple(cell_label(SPEC, settled.cell) for settled in settle(SPEC, codepoints, frozenset(features)))


ROWS = (
    ("qsIt", (), ("qsIt.bar",)),
    ("qsTea", (), ("qsTea.full",)),
    ("qsMay", (), ("qsMay.loop",)),
    ("qsPea", (), ("qsPea.full",)),
    ("qsOy", (), ("qsOy.loop",)),
    # The half-·Tea x-height seam; qsIt's faithful-from-YAML entry extension fires (M1-PLAN section 5 authoring note: the gates and the ledger arbitrate, not the spec).
    ("qsTea qsIt", (), ("qsTea.half.ex-y5", "qsIt.bar.en-y5.en-ext-1")),
    ("qsIt qsMay", (), ("qsIt.bar.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    ("qsMay qsIt", (), ("qsMay.loop.ex-y5.ex-ext-1", "qsIt.bar.en-y5")),
    ("qsMay qsMay", (), ("qsMay.grounded-loop.ex-y0", "qsMay.loop.en-y0")),
    ("qsTea qsMay", (), ("qsTea.full.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    # Phase 5 authoring fix: qsTea joined the grounded-exit refusal list (today's font breaks May.Tea while May.May joins, and the off-anchor contact gate rejected the loop top touching the bar), so the mid-word non-join renders pulled back.
    ("qsMay qsTea", (), ("qsMay.loop.ex-bind-pulled-back", "qsTea.full")),
    # Under ss03 the x-height path scores equal and the declared order: (loop before grounded-loop) decides.
    ("qsMay qsTea", ("ss03",), ("qsMay.loop.ex-y5.ex-ext-1", "qsTea.half.en-y5")),
    # The optimistic third term buys the second join; the two equal-demand exit extends (self entry live; toward-list with qsIt) co-match without E-INCOMPARABLE.
    (
        "qsTea qsMay qsIt",
        (),
        ("qsTea.full.ex-y0", "qsMay.loop.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.bar.en-y5"),
    ),
    # Same-seam non-summing: the middle qsIt's extended exit suppresses the follower qsMay's entry extension.
    (
        "qsMay qsIt qsMay",
        (),
        ("qsMay.loop.ex-y5.ex-ext-1", "qsIt.bar.en-y5.ex-y0.ex-ext-1", "qsMay.loop.en-y0"),
    ),
    ("qsIt qsMay qsIt", (), ("qsIt.bar.ex-y0", "qsMay.loop.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.bar.en-y5")),
    # The entered middle qsIt withdraws its exit before a follower that refuses its baseline entry after qsIt; withdrawal: safe leaves the plain exit-none cell.
    ("qsTea qsIt qsTea", (), ("qsTea.half.ex-y5", "qsIt.bar.en-y5.en-ext-1", "qsTea.full")),
    ("qsIt qsTea", (), ("qsIt.bar", "qsTea.full")),
    ("qsTea qsTea", (), ("qsTea.full", "qsTea.full")),
    ("qsIt qsIt", (), ("qsIt.bar", "qsIt.bar")),
    # qsPea joins followers through the half way's x-height dip; the halves-class entry extension excepts qsPea, so qsIt takes no en-ext here.
    ("qsPea qsIt", (), ("qsPea.half.ex-y5", "qsIt.bar.en-y5")),
    # The y6 chain keeps all four heights live.
    ("qsPea qsPea", (), ("qsPea.half.ex-y6", "qsPea.full.en-y6")),
    ("qsPea qsPea qsIt", (), ("qsPea.half.ex-y6", "qsPea.half.en-y6.ex-y5", "qsIt.bar.en-y5")),
    ("qsMay qsPea", (), ("qsMay.loop.ex-y5", "qsPea.full.en-y5")),
    # The both-dipped half cell: entered at the x-height and exiting at the x-height in one explicit cells: composition.
    ("qsMay qsPea qsIt", (), ("qsMay.loop.ex-y5", "qsPea.half.en-y5.ex-y5", "qsIt.bar.en-y5")),
    ("qsPea qsOy", (), ("qsPea.full", "qsOy.loop")),
    ("qsMay qsOy", (), ("qsMay.loop.ex-y5", "qsOy.loop.en-y5")),
    ("qsMay qsOy qsIt", (), ("qsMay.loop.ex-y5", "qsOy.loop.en-y5.ex-y0", "qsIt.bar.en-y0")),
    ("qsOy qsIt", (), ("qsOy.loop.ex-y0", "qsIt.bar.en-y0")),
    ("qsOy qsTea", (), ("qsOy.loop.ex-y0", "qsTea.full.en-y0")),
    ("qsIt qsOy", (), ("qsIt.bar", "qsOy.loop")),
    # Formation runs first, unconditionally; the entryless ligature severs left joins (predecessor withdrawal is cell semantics on the predecessor's side).
    ("qsTea qsOy", (), ("qsTea_qsOy.bar-into-loop",)),
    ("qsTea qsOy qsIt", (), ("qsTea_qsOy.bar-into-loop.ex-y0", "qsIt.bar.en-y0")),
    ("qsTea qsOy qsTea", (), ("qsTea_qsOy.bar-into-loop.ex-y0", "qsTea.full.en-y0")),
    # Phase 5 authoring fix: qsTea_qsOy restored to qsMay's baseline entry-extension trigger list (the old pipeline's ligature expansion included it, and the baseline proves today's en-ext-1).
    ("qsTea qsOy qsMay", (), ("qsTea_qsOy.bar-into-loop.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    ("qsIt qsTea qsOy", (), ("qsIt.bar", "qsTea_qsOy.bar-into-loop")),
    # AUTHORED-DATA FINDING (generalized stranded-exit-withdrawal): qsMay's declined exit mid-word renders with the pulled-back withdrawal binding, carried in the cell identity.
    ("qsMay qsTea qsOy", (), ("qsMay.loop.ex-bind-pulled-back", "qsTea_qsOy.bar-into-loop")),
    ("qsTea qsOy qsTea qsOy", (), ("qsTea_qsOy.bar-into-loop", "qsTea_qsOy.bar-into-loop")),
    (
        "qsMay qsTea qsIt",
        (),
        ("qsMay.loop.ex-bind-pulled-back", "qsTea.half.ex-y5", "qsIt.bar.en-y5.en-ext-1"),
    ),
    # ZWNJ splits the run; entry-bearing letters after it settle as locked twins with the entry severed.
    ("qsIt zwnj qsTea", (), ("qsIt.bar", "uni200C", "qsTea.full.locked")),
    ("zwnj qsTea qsIt", (), ("uni200C", "qsTea.half.ex-y5.locked", "qsIt.bar.en-y5.en-ext-1")),
    ("zwnj qsMay qsTea", ("ss03",), ("uni200C", "qsMay.loop.ex-y5.locked.ex-ext-1", "qsTea.half.en-y5")),
    # The ss03 cross-ZWNJ leak, fixed structurally: no join across the break.
    ("qsMay zwnj qsTea", ("ss03",), ("qsMay.loop", "uni200C", "qsTea.full.locked")),
    ("qsMay space qsTea", ("ss03",), ("qsMay.loop", "space", "qsTea.full")),
    ("qsIt zwnj qsTea qsOy", (), ("qsIt.bar", "uni200C", "qsTea_qsOy.bar-into-loop")),
    # The namer dot does not split runs but has no join surface, so adjacency breaks naturally and nothing locks after it.
    ("qsMay namer-dot qsIt", (), ("qsMay.loop", "periodcentered", "qsIt.bar")),
    # ss02/ss04/ss05 triggers are out of the M1 alphabet: identical to default over these windows.
    ("qsMay qsTea", ("ss02",), ("qsMay.loop.ex-bind-pulled-back", "qsTea.full")),
    # AUTHORED-DATA FINDING: the qsIt baseline-exit refusal toward [qsTea, qsRoe, qsIt] is self-scoped to unentered cells, so an entered qsIt joins a following qsIt at the baseline (today's font breaks here); identical under ss04 because every ss04 unlock partner is out of the alphabet.
    ("qsTea qsIt qsIt", (), ("qsTea.half.ex-y5", "qsIt.bar.en-y5.ex-y0.en-ext-1.ex-ext-1", "qsIt.bar.en-y0")),
    (
        "qsTea qsIt qsIt",
        ("ss04",),
        ("qsTea.half.ex-y5", "qsIt.bar.en-y5.ex-y0.en-ext-1.ex-ext-1", "qsIt.bar.en-y0"),
    ),
)


@pytest.mark.parametrize(
    "sequence,features,expected", ROWS, ids=[f"{row[0]}|{'+'.join(row[1]) or 'default'}" for row in ROWS]
)
def test_settlement_rows(sequence, features, expected):
    assert run(sequence, features) == expected


def test_exit_extension_amount_rides_the_seam():
    codepoints = [NAME_TO_CODEPOINT[name] for name in ("qsMay", "qsIt")]
    settled = settle(SPEC, codepoints, frozenset())
    assert settled[0].extension == 1
    assert settled[0].seam == "x-height"
    assert settled[1].extension == 0


def test_entry_extension_suppressed_when_left_seam_already_extended():
    codepoints = [NAME_TO_CODEPOINT[name] for name in ("qsMay", "qsIt", "qsMay")]
    settled = settle(SPEC, codepoints, frozenset())
    assert settled[1].extension == 1
    assert settled[2].cell.adjustments == ()


def test_e_stranded_raises_on_forged_commitment():
    engine = Engine(SPEC, frozenset())
    forged = LeftContext("letter", Settled(CellId("qsTea", "full", None, "top"), seam="top", extension=0))
    with pytest.raises(EStrandedError):
        engine.transition_trace(forged, RightToken("letter", "qsIt"), EDGE, EDGE)


def test_entry_bearing_census():
    assert is_entry_bearing(SPEC, "qsPea")
    assert is_entry_bearing(SPEC, "qsTea")
    assert is_entry_bearing(SPEC, "qsMay")
    assert is_entry_bearing(SPEC, "qsIt")
    assert is_entry_bearing(SPEC, "qsOy")
    assert not is_entry_bearing(SPEC, "qsTea_qsOy")


def test_word_position_derivation():
    assert word_position("edge", "edge") == "isolated"
    assert word_position("space", "letter") == "initial"
    assert word_position("letter", "zwnj") == "final"
    assert word_position("namer-dot", "letter") == "medial"
    assert word_position("letter", "namer-dot") == "medial"
    assert word_position("edge", "unknown") is None


# --- synthetic specs for the stages the real records leave unexercised ---------------------


def _synthetic_spec(prefer_a=(), prefer_b=(), contract_b=()) -> ResolvedSpec:
    """Three letters: A exits at the x-height toward anything; B enters at the x-height (entered B is exitless by pairing) and exits at the baseline only when unentered; C enters at the baseline. The A.B seam therefore ties join-vs-prospect at one window join each — the floor and prefer testbed."""
    a = Rune(
        name="A",
        codepoint=0xE001,
        ductus={"stroke": "synthetic"},
        stances={
            "stroke": Stance(
                "stroke",
                way="stroke",
                surface=Surface(
                    exits={"x-height": SurfaceRow("x-height", x=1, withdrawal="safe")},
                ),
            ),
            "flourish": Stance("flourish", way="stroke"),
        },
        policy=Policy(order=("stroke", "flourish"), prefer=tuple(prefer_a)),
    )
    b = Rune(
        name="B",
        codepoint=0xE002,
        ductus={"hook": "synthetic"},
        stances={
            "hook": Stance(
                "hook",
                way="hook",
                surface=Surface(
                    entries={"x-height": SurfaceRow("x-height", x=0)},
                    exits={"baseline": SurfaceRow("baseline", x=1, withdrawal="safe")},
                    pairings=Pairings(never=(Pairing("x-height", "baseline"),)),
                ),
            ),
        },
        policy=Policy(order=("hook",), prefer=tuple(prefer_b), contract=tuple(contract_b)),
    )
    c = Rune(
        name="C",
        codepoint=0xE003,
        ductus={"base": "synthetic"},
        stances={
            "base": Stance(
                "base",
                way="base",
                surface=Surface(entries={"baseline": SurfaceRow("baseline", x=0)}),
            ),
        },
        policy=Policy(order=("base",)),
    )
    registry = ScriptRegistry(
        heights={"baseline": 0, "x-height": 5, "y6": 6, "top": 8},
        boundary_tokens={
            "space": BoundaryToken(0x0020, splits_runs=True),
            "zwnj": BoundaryToken(0x200C, splits_runs=True),
            "namer-dot": BoundaryToken(0x00B7, splits_runs=False),
        },
        predicate_classes={},
        families={
            "A": FamilyInfo(codepoint=0xE001),
            "B": FamilyInfo(codepoint=0xE002),
            "C": FamilyInfo(codepoint=0xE003),
        },
    )
    return ResolvedSpec(runes={"A": a, "B": b, "C": c}, registry=registry)


def _labels(spec, codepoints, features=frozenset()):
    return tuple(cell_label(spec, settled.cell) for settled in settle(spec, codepoints, features))


def test_floor_breaks_realization_tie_toward_the_join_and_flags_joint():
    spec = _synthetic_spec()
    engine = Engine(spec, frozenset())
    trace = engine.transition_trace(
        LeftContext("edge"), RightToken("letter", "A"), RightToken("letter", "B"), RightToken("letter", "C")
    )
    assert trace.settled.cell == CellId("A", "stroke", None, "x-height")
    assert trace.decided_stage == "floor"
    assert trace.joint_floor


def test_follower_cell_grain_prefer_withholds_the_predecessor_exit():
    prefer = PolicyRecord(kind="prefer", cell={"exit": "baseline"}, over={"entry": "x-height"}, when=When())
    spec = _synthetic_spec(prefer_b=(prefer,))
    labels = _labels(spec, [0xE001, 0xE002, 0xE003])
    assert labels == ("A.stroke", "B.hook.ex-y0", "C.base.en-y0")


def test_absolute_prefer_outranks_join_count():
    prefer = PolicyRecord(
        kind="prefer",
        stance="flourish",
        mode="absolute",
        when=When(right=Condition(family=("B",))),
        why="taste over join, recorded",
    )
    spec = _synthetic_spec(prefer_a=(prefer,))
    labels = _labels(spec, [0xE001, 0xE002])
    assert labels[0] == "A.flourish"


def test_bind_contract_lands_in_the_adjustments_grammar():
    contract = PolicyRecord(
        kind="contract",
        stance="hook",
        entry="x-height",
        bind="hook-after-a",
        when=When(left=Condition(family=("A",), joined_at="x-height")),
    )
    spec = _synthetic_spec(contract_b=(contract,))
    labels = _labels(spec, [0xE001, 0xE002])
    assert labels == ("A.stroke.ex-y5", "B.hook.en-y5.en-bind-hook-after-a")
