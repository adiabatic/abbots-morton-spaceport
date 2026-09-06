"""Settlement tests over the real M1 rune data (the mini spec of rebuild/pipeline/fixtures.py), the synthetic specs for the stages the real records leave unexercised (fixtures.synthetic_spec and fixtures.prospect_spec), and round-1 verdict pins that load glyph_data/runes/*.yaml directly (the fixture transcription is frozen at M1 and predates the verdict records). Every window here settles in the crate: each table below rides one kernel_exec.settle_sequences call behind one guard sweep, so a worker pays a handful of invocations for the whole module rather than one per row, and a window a test asks for that its table never listed comes back as a KeyError rather than as a quiet extra spawn.

Expectations marked AUTHORED-DATA FINDING assert the authored rune files' actual semantics where they knowingly diverge from today's font (the qsMay grounded exit is unscoped and its refusal list lacks qsTea; the qsMay baseline entry extension's trigger list lacks qsTea_qsOy; qsMay withdraws its exit stub mid-word). Those rows are divergence-ledger material for Phase 5, not kernel bugs — see the Deviations section appended to rebuild/M1-PLAN.md.

Two claims about the retired Python engine's own internals went with it rather than moving to the crate. The candidate cache's aliasing — a warm engine handing back the very list it built before, unmutated by a settlement in between — is a statement about a shared Python list the crate has no counterpart for; what the memo owes is pinned instead by engine.rs's only_a_trace_memo_engine_memoizes_an_enumeration_and_a_hit_replays_its_delta and a_warm_engine_fires_exactly_what_a_cold_one_fires. The pairing-set cache's bound went with the cache: the crate keys pairing sets on StanceId, so a spec has exactly as many as it has stances and there is nothing left to cap.
"""

import itertools

import pytest

from rebuild.pipeline import conform, fixtures, kernel_exec, spec_load
from rebuild.pipeline.model import CellId, Condition, PolicyRecord, Settled, When
from rebuild.pipeline.settle import (
    EDGE,
    LeftContext,
    RightToken,
    SettleError,
    cell_label,
    form_ligatures,
    guard_blocks,
    is_entry_bearing,
    tokens_from_codepoints,
    word_position,
)

SPEC = fixtures.mini_spec()


def _name_to_codepoint(spec) -> dict[str, int]:
    mapping = {
        name: info.codepoint for name, info in spec.registry.families.items() if info.codepoint is not None
    }
    mapping.update({name: token.codepoint for name, token in spec.registry.boundary_tokens.items()})
    return mapping


def _traces(spec, requests, *, modes=None):
    """One kernel batch for a whole table of windows: sweep the guard once, form each request's ligatures against that one surface, and hand every already-formed token sequence to `kernel_exec.settle_sequences` in a single call, which spends one invocation per wave per feature configuration rather than one per row. `modes` names a settlement world other than this process's."""
    guard = kernel_exec.guard_sweep(spec)
    formed = [
        (form_ligatures(spec, tokens_from_codepoints(spec, codepoints), guard), frozenset(features))
        for codepoints, features in requests
    ]
    answered = kernel_exec.settle_sequences(spec, formed, modes=modes)
    traces = []
    for row in answered:
        assert row is not None
        traces.append(row)
    return traces


def _settled(spec, requests, *, modes=None):
    return [tuple(trace.settled for trace in row) for row in _traces(spec, requests, modes=modes)]


def _labels(spec, requests, *, modes=None):
    return [
        tuple(cell_label(spec, settled.cell) for settled in row)
        for row in _settled(spec, requests, modes=modes)
    ]


def _requests(spec, windows):
    codepoints = _name_to_codepoint(spec)
    return [([codepoints[name] for name in names.split()], features) for names, features in windows]


def _window_settled(spec, windows, *, modes=None):
    """Every named window of `windows` settled in one batch, keyed by the `(names, features)` pair that asked for it."""
    return dict(zip(windows, _settled(spec, _requests(spec, windows), modes=modes)))


ROWS = (
    ("qsIt", (), ("qsIt.hapax",)),
    ("qsTea", (), ("qsTea.full",)),
    ("qsMay", (), ("qsMay.loop",)),
    ("qsPea", (), ("qsPea.full",)),
    ("qsOy", (), ("qsOy.hapax",)),
    # The half-·Tea x-height seam; qsIt's faithful-from-YAML entry extension fires (M1-PLAN section 5 authoring note: the gates and the ledger arbitrate, not the spec).
    ("qsTea qsIt", (), ("qsTea.half.ex-y5", "qsIt.hapax.en-y5.en-ext-1")),
    ("qsIt qsMay", (), ("qsIt.hapax.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    ("qsMay qsIt", (), ("qsMay.loop.ex-y5.ex-ext-1", "qsIt.hapax.en-y5")),
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
        ("qsTea.full.ex-y0", "qsMay.loop.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.hapax.en-y5"),
    ),
    # Same-seam non-summing: the middle qsIt's extended exit suppresses the follower qsMay's entry extension.
    (
        "qsMay qsIt qsMay",
        (),
        ("qsMay.loop.ex-y5.ex-ext-1", "qsIt.hapax.en-y5.ex-y0.ex-ext-1", "qsMay.loop.en-y0"),
    ),
    (
        "qsIt qsMay qsIt",
        (),
        ("qsIt.hapax.ex-y0", "qsMay.loop.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.hapax.en-y5"),
    ),
    # The entered middle qsIt withdraws its exit before a follower that refuses its baseline entry after qsIt; withdrawal: safe leaves the plain exit-none cell.
    ("qsTea qsIt qsTea", (), ("qsTea.half.ex-y5", "qsIt.hapax.en-y5.en-ext-1", "qsTea.full")),
    ("qsIt qsTea", (), ("qsIt.hapax", "qsTea.full")),
    ("qsTea qsTea", (), ("qsTea.full", "qsTea.full")),
    ("qsIt qsIt", (), ("qsIt.hapax", "qsIt.hapax")),
    # qsPea joins followers through the half motion's x-height dip; the halves-class entry extension excepts qsPea, so qsIt takes no en-ext here.
    ("qsPea qsIt", (), ("qsPea.half.ex-y5", "qsIt.hapax.en-y5")),
    # The y6 chain keeps all four heights live.
    ("qsPea qsPea", (), ("qsPea.half.ex-y6", "qsPea.full.en-y6")),
    ("qsPea qsPea qsIt", (), ("qsPea.half.ex-y6", "qsPea.half.en-y6.ex-y5", "qsIt.hapax.en-y5")),
    ("qsMay qsPea", (), ("qsMay.loop.ex-y5", "qsPea.full.en-y5")),
    # The both-dipped half cell: entered at the x-height and exiting at the x-height in one explicit cells: composition.
    ("qsMay qsPea qsIt", (), ("qsMay.loop.ex-y5", "qsPea.half.en-y5.ex-y5", "qsIt.hapax.en-y5")),
    ("qsPea qsOy", (), ("qsPea.full", "qsOy.hapax")),
    ("qsMay qsOy", (), ("qsMay.loop.ex-y5", "qsOy.hapax.en-y5")),
    ("qsMay qsOy qsIt", (), ("qsMay.loop.ex-y5", "qsOy.hapax.en-y5.ex-y0", "qsIt.hapax.en-y0")),
    ("qsOy qsIt", (), ("qsOy.hapax.ex-y0", "qsIt.hapax.en-y0")),
    ("qsOy qsTea", (), ("qsOy.hapax.ex-y0", "qsTea.full.en-y0")),
    ("qsIt qsOy", (), ("qsIt.hapax", "qsOy.hapax")),
    # Formation runs first, unconditionally; the entryless ligature severs left joins (predecessor withdrawal is cell semantics on the predecessor's side).
    ("qsTea qsOy", (), ("qsTea_qsOy.hapax",)),
    ("qsTea qsOy qsIt", (), ("qsTea_qsOy.hapax.ex-y0", "qsIt.hapax.en-y0")),
    ("qsTea qsOy qsTea", (), ("qsTea_qsOy.hapax.ex-y0", "qsTea.full.en-y0")),
    # Phase 5 authoring fix: qsTea_qsOy restored to qsMay's baseline entry-extension trigger list (the old pipeline's ligature expansion included it, and the baseline proves today's en-ext-1).
    ("qsTea qsOy qsMay", (), ("qsTea_qsOy.hapax.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    ("qsIt qsTea qsOy", (), ("qsIt.hapax", "qsTea_qsOy.hapax")),
    # AUTHORED-DATA FINDING (generalized stranded-exit-withdrawal): qsMay's declined exit mid-word renders with the pulled-back withdrawal binding, carried in the cell identity.
    ("qsMay qsTea qsOy", (), ("qsMay.loop.ex-bind-pulled-back", "qsTea_qsOy.hapax")),
    ("qsTea qsOy qsTea qsOy", (), ("qsTea_qsOy.hapax", "qsTea_qsOy.hapax")),
    (
        "qsMay qsTea qsIt",
        (),
        ("qsMay.loop.ex-bind-pulled-back", "qsTea.half.ex-y5", "qsIt.hapax.en-y5.en-ext-1"),
    ),
    # ZWNJ splits the run; entry-bearing letters after it settle as locked twins with the entry severed.
    ("qsIt zwnj qsTea", (), ("qsIt.hapax", "uni200C", "qsTea.full.locked")),
    ("zwnj qsTea qsIt", (), ("uni200C", "qsTea.half.ex-y5.locked", "qsIt.hapax.en-y5.en-ext-1")),
    ("zwnj qsMay qsTea", ("ss03",), ("uni200C", "qsMay.loop.ex-y5.locked.ex-ext-1", "qsTea.half.en-y5")),
    # The ss03 cross-ZWNJ leak, fixed structurally: no join across the break.
    ("qsMay zwnj qsTea", ("ss03",), ("qsMay.loop", "uni200C", "qsTea.full.locked")),
    ("qsMay space qsTea", ("ss03",), ("qsMay.loop", "space", "qsTea.full")),
    ("qsIt zwnj qsTea qsOy", (), ("qsIt.hapax", "uni200C", "qsTea_qsOy.hapax")),
    # The namer dot does not split runs but has no join surface, so adjacency breaks naturally and nothing locks after it.
    ("qsMay namer-dot qsIt", (), ("qsMay.loop", "periodcentered", "qsIt.hapax")),
    # ss05's trigger is out of the M1 alphabet: identical to default over these windows.
    ("qsMay qsTea", ("ss05",), ("qsMay.loop.ex-bind-pulled-back", "qsTea.full")),
    # AUTHORED-DATA FINDING: the qsIt baseline-exit refusal toward [qsTea, qsRoe, qsIt] is self-scoped to unentered cells, so an entered qsIt joins a following qsIt at the baseline (today's font breaks here); identical under ss04 because the middle ·It settles with an x-height entry, so the baseline-baseline pass-through grant never engages in this window.
    (
        "qsTea qsIt qsIt",
        (),
        ("qsTea.half.ex-y5", "qsIt.hapax.en-y5.ex-y0.en-ext-1.ex-ext-1", "qsIt.hapax.en-y0"),
    ),
    (
        "qsTea qsIt qsIt",
        ("ss04",),
        ("qsTea.half.ex-y5", "qsIt.hapax.en-y5.ex-y0.en-ext-1.ex-ext-1", "qsIt.hapax.en-y0"),
    ),
)

ROW_WINDOWS = tuple(dict.fromkeys((sequence, features) for sequence, features, _expected in ROWS))


@pytest.fixture(scope="module")
def row_settled():
    """Every window ROWS names, settled in one batch over the mini spec."""
    return _window_settled(SPEC, ROW_WINDOWS)


@pytest.fixture(scope="module")
def row_labels(row_settled):
    """The same batch as cell labels, which is what most rows assert."""
    return {key: tuple(cell_label(SPEC, settled.cell) for settled in row) for key, row in row_settled.items()}


@pytest.mark.parametrize(
    "sequence,features,expected", ROWS, ids=[f"{row[0]}|{'+'.join(row[1]) or 'default'}" for row in ROWS]
)
def test_settlement_rows(row_labels, sequence, features, expected):
    assert row_labels[(sequence, features)] == expected


def test_exit_extension_amount_rides_the_seam(row_settled):
    settled = row_settled[("qsMay qsIt", ())]
    assert settled[0].extension == 1
    assert settled[0].seam == "x-height"
    assert settled[1].extension == 0


def test_entry_extension_suppressed_when_left_seam_already_extended(row_settled):
    settled = row_settled[("qsMay qsIt qsMay", ())]
    assert settled[1].extension == 1
    assert settled[2].cell.adjustments == ()


def test_a_committed_seam_nothing_accepts_is_unreachable():
    """A left forged with an exit at the top — a height nothing in the mini alphabet enters at — is a window the lookahead closure would never have built, and the crate refuses it rather than settling something. The refusal crosses the seam as `settle.SettleError` carrying the corpus bucket beside the crate's own sentence, which `engine.rs`'s `a_left_that_committed_a_seam_nothing_accepts_is_stranded` states in the crate's vocabulary; `ex-y8` is the mini registry's `top`."""
    forged = LeftContext("letter", Settled(CellId("qsTea", "full", None, "top"), seam="top", extension=0))
    case = kernel_exec.case_row(forged, RightToken("letter", "qsIt"), (EDGE, EDGE, EDGE, EDGE))
    answer = kernel_exec.settle_cases(SPEC, [case], frozenset())[0]
    with pytest.raises(SettleError) as caught:
        kernel_exec.trace_of(answer["result"])
    assert caught.value.bucket == "E-UNREACHABLE"
    assert str(caught.value) == (
        "E-STRANDED: qsTea.full.ex-y8 committed an exit at top but qsIt has no acceptor cell "
        "(the lookahead closure should have prevented this commitment)"
    )


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


def test_floor_breaks_realization_tie_toward_the_join_and_flags_joint():
    spec = fixtures.synthetic_spec()
    trace = _traces(spec, [([0xE001, 0xE002, 0xE003], ())])[0][0]
    assert trace.settled.cell == CellId("A", "stroke", None, "x-height")
    assert trace.decided_stage == "floor"
    assert trace.joint_floor


def test_follower_cell_grain_prefer_withholds_the_predecessor_exit():
    prefer = PolicyRecord(kind="prefer", cell={"exit": "baseline"}, over={"entry": "x-height"}, when=When())
    spec = fixtures.synthetic_spec(prefer_b=(prefer,))
    labels = _labels(spec, [([0xE001, 0xE002, 0xE003], ())])[0]
    assert labels == ("A.stroke", "B.hook.ex-y0", "C.base.en-y0")


def test_absolute_prefer_outranks_join_count():
    prefer = PolicyRecord(
        kind="prefer",
        stance="flourish",
        mode="absolute",
        when=When(right=Condition(family=("B",))),
        why="taste over join, recorded",
    )
    spec = fixtures.synthetic_spec(prefer_a=(prefer,))
    labels = _labels(spec, [([0xE001, 0xE002], ())])[0]
    assert labels[0] == "A.flourish"


def test_bind_contract_lands_in_the_adjustments_grammar():
    contract = PolicyRecord(
        kind="contract",
        stance="hook",
        entry="x-height",
        bind="hook-after-a",
        when=When(left=Condition(family=("A",), joined_at="x-height")),
    )
    spec = fixtures.synthetic_spec(contract_b=(contract,))
    labels = _labels(spec, [([0xE001, 0xE002], ())])[0]
    assert labels == ("A.stroke.ex-y5", "B.hook.en-y5.en-bind-hook-after-a")


PROSPECT_SPEC = fixtures.prospect_spec()
PROSPECT_WINDOWS = ("A B C D", "A B")


@pytest.fixture(scope="module")
def prospect_settled():
    """Both prospect windows under both settlement worlds, one batch per world. `SettlementModes` names the world on the invocation instead of monkeypatching this process's defaults, so the optimistic arm and the simulated one are two argv spellings rather than two environments."""
    settled = {}
    for simulated in (False, True):
        modes = kernel_exec.SettlementModes(simulated_prospect=simulated, vote_slots=True)
        windows = tuple((names, ()) for names in PROSPECT_WINDOWS)
        for (names, _features), row in _window_settled(PROSPECT_SPEC, windows, modes=modes).items():
            settled[(names, simulated)] = row
    return settled


def test_simulated_prospect_sees_the_follower_yield_the_promised_join(prospect_settled):
    optimistic = tuple(cell_label(PROSPECT_SPEC, s.cell) for s in prospect_settled[("A B C D", False)])
    assert optimistic == ("A.stroke.ex-y0", "B.hook.en-y0", "C.base.ex-y0", "D.base.en-y0")
    simulated = tuple(cell_label(PROSPECT_SPEC, s.cell) for s in prospect_settled[("A B C D", True)])
    assert simulated == ("A.stroke.ex-y5", "B.hook.en-y5", "C.base.ex-y0", "D.base.en-y0")


def test_simulated_prospect_bottoms_out_at_the_window_edge(prospect_settled):
    """Two letters and nothing past them: the simulated prospect has no follower transition to run, so both worlds settle the window identically. That the third term is zero there rather than merely equal is `engine.rs`'s `the_prospect_bottoms_out_at_the_window_edge_where_both_modes_agree`, which reads the term off a ladder the settled cell does not carry across the seam."""
    assert prospect_settled[("A B", False)] == prospect_settled[("A B", True)]


# Round-1 verdict pins over the real loaded rune YAML. The fixture spec above is the frozen M1 transcription and intentionally predates the round-1 verdict records, so these rows load glyph_data/runes/*.yaml directly. They pin the greedy ·May·May pairing of the round-1 verdict (u-0341, "the old way seems nicer to write out by hand"): chains pair up y0 | break | y0 | break, like the shipped font does at every length. The quad is the verdicted window; the quint and sextet are the only gate that sees qsMay's chain-interior prefer (the one scoped on an unjoined ·May to its left) — the acceptance oracle's window universe tops out at four letters, where the word-start record alone reproduces every outcome, and without the chain-interior record chains of five or more regress to the rejected defer-to-the-tail grouping.


@pytest.fixture(scope="module")
def real_spec():
    import warnings

    from rebuild.pipeline.spec_load import load_default_spec

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load_default_spec()


MAY_CHAIN_ROWS = (
    (
        4,
        (
            "qsMay.grounded-loop.ex-y0",
            "qsMay.loop.en-y0",
            "qsMay.grounded-loop.ex-y0",
            "qsMay.loop.en-y0",
        ),
    ),
    (
        5,
        (
            "qsMay.grounded-loop.ex-y0",
            "qsMay.loop.en-y0",
            "qsMay.grounded-loop.ex-y0",
            "qsMay.loop.en-y0",
            "qsMay.loop",
        ),
    ),
    (
        6,
        (
            "qsMay.grounded-loop.ex-y0",
            "qsMay.loop.en-y0",
            "qsMay.grounded-loop.ex-y0",
            "qsMay.loop.en-y0",
            "qsMay.grounded-loop.ex-y0",
            "qsMay.loop.en-y0",
        ),
    ),
)


@pytest.mark.parametrize(
    "length,expected", MAY_CHAIN_ROWS, ids=[f"qsMay-x{row[0]}" for row in MAY_CHAIN_ROWS]
)
def test_round1_greedy_may_chain_pairing(real_labels, length, expected):
    assert real_labels[(" ".join(["qsMay"] * length), ())] == expected


def test_bay_may_contracts_mays_baseline_entry(real_labels):
    assert real_labels[("qsBay qsMay", ())] == (
        "qsBay.hapax.ex-y0",
        "qsMay.loop.en-y0.en-con-1",
    )


# The section 5.7 late-formation guard over the real loaded rune YAML. The Manual pin `·Day | ·Utter.alt ·Low` (site/the-manual.html) is the live counterexample to unconditional formation: the ligature exits only at the x-height, ·Low enters only at the baseline, and the unformed alternate ·Utter carries the baseline seam the ligature would destroy. The guard yields formation exactly there, and qsUtter.policy.prefer[2] (the section 5.9 follower one-liner) makes ·Day withhold its exit so the alternate is free to reach.

MAY_TEA_JAI_LEADS = ("qsI", "qsAh")
MAY_TEA_JAI_WINDOWS = ("qsTea qsJai", "qsMay qsTea qsJai qsTea")


@pytest.mark.parametrize("lead", MAY_TEA_JAI_LEADS)
@pytest.mark.parametrize("features", ((), ("ss03",)))
def test_may_tea_jai_keeps_a_baseline_gap(real_labels, lead, features):
    assert real_labels[(f"{lead} qsMay qsTea qsJai", features)] == (
        f"{lead}.{'loop' if lead == 'qsI' else 'hapax'}.ex-y5.ex-ext-1",
        "qsMay.loop.en-y5",
        "qsTea.half.ex-y5.ex-ext-1",
        "qsJai.hapax.en-y5.en-con-1",
    )
    assert real_labels[("qsTea qsJai", features)] == (
        "qsTea.half.ex-y5",
        "qsJai.hapax.en-y5.en-con-1",
    )
    follower_labels = real_labels[("qsMay qsTea qsJai qsTea", features)]
    assert follower_labels[1] == ("qsTea.full.en-y5" if features else "qsTea.half.ex-y5")


# The depth-3 regression pins (doc/rebuild-design.md section 3.4, the orphaned-·Tea windows): in ·Day·Tea·Utter·Low and ·Oy·Tea·Utter·Low the predecessor would withdraw its baseline exit on the prospect that ·Tea joins forward into ·Utter, and qsUtter's ·Low-scoped prefer then vetoes the entry, leaving ·Tea joined on neither side. The depth-3 chains on qsDay.policy.prefer[1] and qsOy/qsTea_qsOy.policy.prefer[0] keep the predecessor's exit exactly there, matching the old font's y0,break,y0 grouping; the contrast windows pin that the yield still fires everywhere else. The depth-4 sextet carries the same phenomenon one token deeper: qsDay.policy.prefer[5]'s entry-live carve-out reads the fourth raw glyph, so ·Pea·Day·Tea·Utter·Tea·May withdraws ·Day's exit and joins ·Tea forward into ·Utter when the fourth letter is an orphan follower, while the rescue still holds when the tail stays joinable (·Pea, or a word-final stop) and ss03 keeps the y5 ·Utter·Tea escape; these windows run five and six letters, past the acceptance oracle's four-letter horizon, so they are pinned here by hand.


ORPHANED_TEA_ROWS = (
    (
        "qsDay qsTea qsUtter qsLow",
        ("qsDay.full.ex-y0", "qsTea.full.en-y0", "qsUtter.alternate.ex-y0", "qsLow.hapax.en-y0"),
    ),
    (
        "qsOy qsTea qsUtter qsLow",
        ("qsOy.hapax.ex-y0", "qsTea.full.en-y0", "qsUtter.alternate.ex-y0", "qsLow.hapax.en-y0"),
    ),
    ("qsDay qsTea qsUtter", ("qsDay.full", "qsTea.full.ex-y0", "qsUtter.mono.en-y0")),
    (
        "qsDay qsTea qsUtter qsMay",
        ("qsDay.full", "qsTea.full.ex-y0", "qsUtter.mono.en-y0.ex-y5", "qsMay.loop.en-y5"),
    ),
    ("qsTea qsUtter qsLow", ("qsTea.full", "qsUtter.alternate.ex-y0", "qsLow.hapax.en-y0")),
    (
        "qsDay qsIt qsUtter qsLow",
        ("qsDay.full.ex-y0", "qsIt.hapax.en-y0", "qsUtter.alternate.ex-y0", "qsLow.hapax.en-y0"),
    ),
)


@pytest.mark.parametrize(
    "sequence,expected", ORPHANED_TEA_ROWS, ids=[row[0].replace(" ", "|") for row in ORPHANED_TEA_ROWS]
)
@pytest.mark.parametrize("features", ((), ("ss03",)), ids=["default", "ss03"])
def test_orphaned_tea_depth3_windows(real_labels, sequence, features, expected):
    assert real_labels[(sequence, features)] == expected


ORPHAN_DEPTH4_ROWS = (
    (
        "qsPea qsDay qsTea qsUtter qsTea qsMay",
        (),
        (
            "qsPea.full.ex-y0",
            "qsDay.half.en-y0",
            "qsTea.full.ex-y0",
            "qsUtter.mono.en-y0",
            "qsTea.full.ex-y0",
            "qsMay.loop.en-y0",
        ),
    ),
    (
        "qsPea qsDay qsTea qsUtter qsTea qsPea",
        (),
        (
            "qsPea.full.ex-y0",
            "qsDay.half.en-y0.ex-y0",
            "qsTea.full.en-y0",
            "qsUtter.alternate.ex-y0",
            "qsTea.full.en-y0",
            "qsPea.full",
        ),
    ),
    (
        "qsPea qsDay qsTea qsUtter qsTea",
        (),
        (
            "qsPea.full.ex-y0",
            "qsDay.half.en-y0.ex-y0",
            "qsTea.full.en-y0",
            "qsUtter.alternate.ex-y0",
            "qsTea.full.en-y0",
        ),
    ),
    (
        "qsPea qsDay qsTea qsUtter qsTea qsMay",
        ("ss03",),
        (
            "qsPea.full.ex-y0",
            "qsDay.half.en-y0",
            "qsTea.full.ex-y0",
            "qsUtter.mono.en-y0.ex-y5.ex-ext-1",
            "qsTea.full.en-y5.ex-y0",
            "qsMay.loop.en-y0",
        ),
    ),
)


@pytest.mark.parametrize(
    "sequence,features,expected",
    ORPHAN_DEPTH4_ROWS,
    ids=[
        row[0].replace(" ", "|") + ("-" + "-".join(row[1]) if row[1] else "-default")
        for row in ORPHAN_DEPTH4_ROWS
    ],
)
def test_orphaned_tea_depth4_windows(real_labels, sequence, features, expected):
    assert real_labels[(sequence, features)] == expected


def test_late_formation_yields_before_low(real_labels):
    assert real_labels[("qsDay qsUtter qsLow", ())] == (
        "qsDay.full",
        "qsUtter.alternate.ex-y0",
        "qsLow.hapax.en-y0",
    )


def test_formation_survives_where_the_ligature_serves_the_follower(real_labels):
    assert real_labels[("qsDay qsUtter", ())] == ("qsDay_qsUtter.full",)
    assert real_labels[("qsDay qsUtter qsMay", ())] == (
        "qsDay_qsUtter.full.ex-y5",
        "qsMay.loop.en-y5",
    )
    assert real_labels[("qsDay qsUtter qsTea", ())] == ("qsDay_qsUtter.full", "qsTea.full")
    assert real_labels[("qsDay qsUtter qsTea", ("ss03",))] == (
        "qsDay_qsUtter.full.ex-y5.ex-ext-1",
        "qsTea.full.en-y5",
    )


def test_formation_blocked_verdicts_are_config_blind(real_spec, real_guard):
    low = RightToken("letter", "qsLow")
    tea = RightToken("letter", "qsTea")
    utter = RightToken("letter", "qsUtter")
    assert real_guard[("qsDay_qsUtter", low, EDGE)]
    assert real_guard[("qsDay_qsUtter", low, tea)]
    assert not real_guard[("qsDay_qsUtter", tea, EDGE)]
    assert not real_guard[("qsDay_qsUtter", utter, EDGE)]
    assert not real_guard[("qsDay_qsUtter", utter, tea)]
    for follower in real_spec.runes:
        assert not real_guard[("qsTea_qsOy", RightToken("letter", follower), EDGE)]


@pytest.fixture(scope="module")
def guard_by_configuration(real_spec) -> dict[frozenset[str], kernel_exec.FormationGuard]:
    """One single-engine sweep per configuration: every subset of the capability features the quantified guard's engines walk, plus every acceptance configuration, so the taste set rides too."""
    features = spec_load.capability_features(real_spec)
    subsets = {
        frozenset(subset)
        for size in range(len(features) + 1)
        for subset in itertools.combinations(features, size)
    }
    subsets |= {conform.features_for_config(config) for config in conform.ACCEPTANCE_CONFIGS}
    return {subset: kernel_exec.guard_sweep_under(real_spec, subset) for subset in subsets}


def test_no_configuration_frees_a_window_the_quantified_guard_blocks(real_guard, guard_by_configuration):
    """Every configuration's surface walks the quantified surface's keys, and the quantified verdict blocks only where every configuration blocks, so a single configuration's own surface is stricter or the same and never looser."""
    assert len(guard_by_configuration) > len(conform.ACCEPTANCE_CONFIGS)
    for features, surface in guard_by_configuration.items():
        assert surface.keys() == real_guard.keys(), sorted(features)
        assert all(surface[key] for key, blocked in real_guard.items() if blocked), sorted(features)


def test_ss03_is_the_one_set_the_guard_surface_depends_on(real_spec, real_guard, guard_by_configuration):
    """Issue #185's claim that the surface is identical across configurations fails today, and the way it fails is what this pins. Every configuration with ss03 on sweeps exactly the quantified surface. Every configuration without it — default, ss04, ss05, ss04+ss05, ss10 — sweeps one and the same stricter surface, blocking exactly the windows where the ss03 x-height entry into full ·Tea is the only seam a qsUtter-trailing ligature can offer a ·Tea follower: `(X_qsUtter, ·Tea, r2)` for the ligatures qsTea's ss03 unlocks name as lefts, and no other. The font is right either way, because formation stages before the ss markers and `settle.form_ligatures` reads the quantified sweep in every configuration: ·Day·Utter·Tea forms the ligature under default with ·Tea unjoined, and ·Tea joins it only under ss03 (`real_labels` above pins both). What a configuration delta may lean on is therefore narrower than the issue stated and is pinned here: ss04, ss05 and ss10 move no formation verdict even at single-engine grain, and ss03 moves only these. If the disagreement ever empties, the claim as issued holds and this test is what tightens to say so."""
    tea = RightToken("letter", "qsTea")
    ligatures_ss03_names = {
        name
        for stance in real_spec.runes["qsTea"].stances.values()
        for unlock in stance.surface.unlocks
        if unlock.feature == "ss03" and unlock.when is not None and unlock.when.left is not None
        for name in unlock.when.left.family
        if name in real_spec.runes and real_spec.runes[name].sequence
    }
    assert ligatures_ss03_names
    without_ss03: dict[frozenset[str], frozenset] = {}
    for features, surface in guard_by_configuration.items():
        disagreements = frozenset(key for key, blocked in surface.items() if blocked != real_guard[key])
        if "ss03" in features:
            assert not disagreements, sorted(features)
        else:
            without_ss03[features] = disagreements
    assert {frozenset(), frozenset({"ss04"}), frozenset({"ss05"}), frozenset({"ss10"})} <= without_ss03.keys()
    (disagreements,) = set(without_ss03.values())
    assert disagreements, "the finding has dissolved: every configuration sweeps the quantified surface"
    assert {right1 for _ligature, right1, _right2 in disagreements} == {tea}
    assert {ligature for ligature, _right1, _right2 in disagreements} == ligatures_ss03_names
    assert not any(real_guard[key] for key in disagreements)


def test_the_guard_reads_letters_only_and_indexes_the_surface_it_was_given(real_guard):
    """The two ends of `guard_blocks`. A first slot that is not a letter never blocks — the guard exists to keep a ligature from stranding a follower, and a boundary is no follower — so the sweep carries no rows for one and none are asked for. Every other triple is an indexed read, so a surface that does not cover the window says so instead of quietly reading as free; `.get(key, False)` here would form every ligature the emitted lookup withholds, silently."""
    utter = RightToken("letter", "qsUtter")
    assert not guard_blocks(real_guard, "qsDay_qsUtter", EDGE, utter)
    assert not guard_blocks(real_guard, "qsDay_qsUtter", RightToken("space"), utter)
    assert guard_blocks(real_guard, "qsDay_qsUtter", RightToken("letter", "qsLow"), EDGE)
    with pytest.raises(KeyError):
        guard_blocks(real_guard, "qsDay_qsUtter", RightToken("letter", "qsNotARune"), EDGE)


# Ligature-transparent left scopes (spec_load._expand_ligature_lefts): a family named in an entry from-scope also admits every registered ligature whose sequence ends in that family, so the sitting's rejected windows u-121942/u-121944 settle the half ·Pea after ·See+Utter exactly as they do after bare ·Utter, and the follower's own join lands. The ·No arm deliberately pins the approved divergence (u-119404/u-135614): full ·Pea takes the baseline join into flipped ·No behind every qsUtter-trailing left alike.


LIGATURE_TRANSPARENT_PEA_ROWS = (
    (
        "qsSee qsUtter qsPea qsRoe",
        ("qsSee_qsUtter.hapax.ex-y5", "qsPea.half.en-y5.ex-y5", "qsRoe.hapax.en-y5.en-con-1"),
    ),
    (
        "qsSee qsUtter qsPea qsIt",
        ("qsSee_qsUtter.hapax.ex-y5", "qsPea.half.en-y5.ex-y5", "qsIt.hapax.en-y5"),
    ),
    (
        "qsSee qsUtter qsPea qsEt",
        ("qsSee_qsUtter.hapax.ex-y5", "qsPea.half.en-y5.ex-y5", "qsEt.hapax.en-y5.en-ext-1"),
    ),
    (
        "qsSee qsUtter qsPea qsNo",
        ("qsSee_qsUtter.hapax.ex-y5", "qsPea.full.en-y5.ex-y0", "qsNo.flipped.en-y0"),
    ),
    (
        "qsUtter qsPea qsRoe",
        ("qsUtter.mono.ex-y5", "qsPea.half.en-y5.ex-y5", "qsRoe.hapax.en-y5.en-con-1"),
    ),
)


@pytest.mark.parametrize(
    "sequence,expected",
    LIGATURE_TRANSPARENT_PEA_ROWS,
    ids=[row[0].replace(" ", "|") for row in LIGATURE_TRANSPARENT_PEA_ROWS],
)
def test_ligature_left_admits_trailing_family_scopes(real_labels, sequence, expected):
    assert real_labels[(sequence, ())] == expected


def test_resolve_record_breaks_the_tea_oy_it_no_crossing(real_labels):
    """The section 5.8 against-a-named-record slice, live: qsTea_qsOy's resolve against qsIt's withhold-before-no-after-oy vote picks the ligature's baseline exit at the tied (·It, ·No, live-third) windows, so the ligature arm renders like the approved bare-·Oy arm instead of raising E-INCOMPARABLE."""
    assert real_labels[("qsTea qsOy qsIt qsNo qsAh", ())] == (
        "qsTea_qsOy.hapax.ex-y0",
        "qsIt.hapax.en-y0",
        "qsNo.flipped.ex-y0",
        "qsAh.hapax.en-y0",
    )


# Every window the real-spec tables above name, gathered so the whole file settles them in one batch: one guard sweep and one settle_sequences call, six waves deep, rather than a kernel spawn per row. The tuple is that batch's entire universe on purpose — `real_labels` keys on the window itself, so a test asking for a window nobody listed here raises KeyError instead of quietly going unsettled.
REAL_WINDOWS = tuple(
    dict.fromkeys(
        (
            *((" ".join(["qsMay"] * length), ()) for length, _expected in MAY_CHAIN_ROWS),
            *(
                (names, features)
                for features in ((), ("ss03",))
                for lead in MAY_TEA_JAI_LEADS
                for names in (f"{lead} qsMay qsTea qsJai", *MAY_TEA_JAI_WINDOWS)
            ),
            *((names, features) for features in ((), ("ss03",)) for names, _expected in ORPHANED_TEA_ROWS),
            *((names, features) for names, features, _expected in ORPHAN_DEPTH4_ROWS),
            ("qsDay qsUtter qsLow", ()),
            ("qsDay qsUtter", ()),
            ("qsDay qsUtter qsMay", ()),
            ("qsDay qsUtter qsTea", ()),
            ("qsDay qsUtter qsTea", ("ss03",)),
            *((names, ()) for names, _expected in LIGATURE_TRANSPARENT_PEA_ROWS),
            ("qsTea qsOy qsIt qsNo qsAh", ()),
            ("qsBay qsMay", ()),
        )
    )
)


@pytest.fixture(scope="module")
def real_guard(real_spec):
    """The crate's complete late-formation verdict surface for the loaded rune YAML — the same memoized sweep `_traces` forms every real-spec window against."""
    return kernel_exec.guard_sweep(real_spec)


@pytest.fixture(scope="module")
def real_labels(real_spec):
    """Every window of REAL_WINDOWS as cell labels, settled in one batch over the loaded rune YAML."""
    return {
        key: tuple(cell_label(real_spec, settled.cell) for settled in row)
        for key, row in _window_settled(real_spec, REAL_WINDOWS).items()
    }
