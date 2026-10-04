"""Settlement tests over three kinds of spec: the mini spec (`fixtures.mini_spec`, a hand transcription of rune data for a few letters), synthetic specs for ranking stages the real records do not exercise (`fixtures.synthetic_spec`, `fixtures.prospect_spec`), and the loaded rune YAML (`load_default_spec`) for records the mini spec does not transcribe, such as the round-1 verdict records. Every window settles in the crate. Each table of windows below is settled by one guard sweep and one `kernel_exec.settle_sequences` call, so the module costs a few kernel invocations instead of one per row. A test that asks for a window its table does not list gets a KeyError.

Rows marked AUTHORED-DATA FINDING assert what the authored rune data does where it differs from the old font. They are divergence-ledger material, not kernel bugs.
"""

import itertools

import pytest

from rebuild.pipeline import conform, fixtures, geometry, kernel_exec, spec_load, surface
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
    """Settles a table of windows in one batch: one guard sweep, ligature formation against it, then one `kernel_exec.settle_sequences` call. That call invokes the kernel once per token position per feature configuration for every `SETTLE_CASE_BATCH_SIZE` windows, instead of once per row. `modes` selects a settlement mode set other than the process's own."""
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
    """Settles every window in `windows` in one batch, keyed by its `(names, features)` pair."""
    return dict(zip(windows, _settled(spec, _requests(spec, windows), modes=modes)))


ROWS = (
    ("qsIt", (), ("qsIt.sole",)),
    ("qsTea", (), ("qsTea.full",)),
    ("qsMay", (), ("qsMay.loop",)),
    ("qsPea", (), ("qsPea.full",)),
    ("qsOy", (), ("qsOy.sole",)),
    # Half ·Tea joins ·It at the x-height, and qsIt's x-height entry extension (policy.extend[0]) fires.
    ("qsTea qsIt", (), ("qsTea.half.ex-y5", "qsIt.sole.en-y5.en-ext-1")),
    ("qsIt qsMay", (), ("qsIt.sole.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    ("qsMay qsIt", (), ("qsMay.loop.ex-y5.ex-ext-1", "qsIt.sole.en-y5")),
    ("qsMay qsMay", (), ("qsMay.grounded-loop.ex-y0", "qsMay.loop.en-y0")),
    ("qsTea qsMay", (), ("qsTea.full.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    # qsMay's baseline exit refuses qsTea (the old font breaks ·May·Tea while ·May·May joins, and the loop top touching the bar is an off-anchor contact), so ·May does not join and renders its pulled-back unjoined drawing.
    ("qsMay qsTea", (), ("qsMay.loop.ex-bind-pulled-back", "qsTea.full")),
    # Under ss03, half ·Tea's x-height entry unlocks after ·May, so ·May ~x~ ·Tea.half is the window's only join and wins on the join count.
    ("qsMay qsTea", ("ss03",), ("qsMay.loop.ex-y5.ex-ext-1", "qsTea.half.en-y5")),
    # The optimistic third term of the join count gains the second join. qsMay's two one-pixel exit extensions (the self-entry-live one and the one toward a list that includes qsIt) both match without E-INCOMPARABLE.
    (
        "qsTea qsMay qsIt",
        (),
        ("qsTea.full.ex-y0", "qsMay.loop.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.sole.en-y5"),
    ),
    # Extensions on one junction do not add up: the middle qsIt's extended exit suppresses the following qsMay's entry extension.
    (
        "qsMay qsIt qsMay",
        (),
        ("qsMay.loop.ex-y5.ex-ext-1", "qsIt.sole.en-y5.ex-y0.ex-ext-1", "qsMay.loop.en-y0"),
    ),
    (
        "qsIt qsMay qsIt",
        (),
        ("qsIt.sole.ex-y0", "qsMay.loop.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.sole.en-y5"),
    ),
    # Full ·Tea refuses a baseline entry after an entered qsIt, so the middle qsIt does not exit, and its `unjoined: safe` leaves the plain exit-none cell.
    ("qsTea qsIt qsTea", (), ("qsTea.half.ex-y5", "qsIt.sole.en-y5.en-ext-1", "qsTea.full")),
    ("qsIt qsTea", (), ("qsIt.sole", "qsTea.full")),
    ("qsTea qsTea", (), ("qsTea.full", "qsTea.full")),
    ("qsIt qsIt", (), ("qsIt.sole", "qsIt.sole")),
    # qsPea joins followers through the half motion's x-height dip; the halves-class entry extension excepts qsPea, so qsIt takes no en-ext here.
    ("qsPea qsIt", (), ("qsPea.half.ex-y5", "qsIt.sole.en-y5")),
    # ·Pea.half ~6~ ·Pea is the only y6 join these rows check.
    ("qsPea qsPea", (), ("qsPea.half.ex-y6", "qsPea.full.en-y6")),
    ("qsPea qsPea qsIt", (), ("qsPea.half.ex-y6", "qsPea.half.en-y6.ex-y5", "qsIt.sole.en-y5")),
    ("qsMay qsPea", (), ("qsMay.loop.ex-y5", "qsPea.full.en-y5")),
    # The both-dipped half cell: entered at the x-height and exiting at the x-height in one explicit cells: composition.
    ("qsMay qsPea qsIt", (), ("qsMay.loop.ex-y5", "qsPea.half.en-y5.ex-y5", "qsIt.sole.en-y5")),
    ("qsPea qsOy", (), ("qsPea.full", "qsOy.sole")),
    ("qsMay qsOy", (), ("qsMay.loop.ex-y5", "qsOy.sole.en-y5")),
    ("qsMay qsOy qsIt", (), ("qsMay.loop.ex-y5", "qsOy.sole.en-y5.ex-y0", "qsIt.sole.en-y0")),
    ("qsOy qsIt", (), ("qsOy.sole.ex-y0", "qsIt.sole.en-y0")),
    ("qsOy qsTea", (), ("qsOy.sole.ex-y0", "qsTea.full.en-y0")),
    ("qsIt qsOy", (), ("qsIt.sole", "qsOy.sole")),
    # Formation runs first, and nothing blocks it in these windows. The ligature has no entry, so nothing joins it from the left; how the predecessor draws its unjoined exit is decided by the predecessor's own cells.
    ("qsTea qsOy", (), ("qsTea_qsOy.sole",)),
    ("qsTea qsOy qsIt", (), ("qsTea_qsOy.sole.ex-y0", "qsIt.sole.en-y0")),
    ("qsTea qsOy qsTea", (), ("qsTea_qsOy.sole.ex-y0", "qsTea.full.en-y0")),
    # qsMay's baseline entry extension lists qsTea_qsOy as a trigger, which matches the old font's en-ext-1 in the baseline.
    ("qsTea qsOy qsMay", (), ("qsTea_qsOy.sole.ex-y0", "qsMay.loop.en-y0.en-ext-1")),
    ("qsIt qsTea qsOy", (), ("qsIt.sole", "qsTea_qsOy.sole")),
    # AUTHORED-DATA FINDING (generalized unaccepted-exit-unjoined): qsMay's declined exit before a following letter renders with the pulled-back unjoined binding, which is part of the cell identity.
    ("qsMay qsTea qsOy", (), ("qsMay.loop.ex-bind-pulled-back", "qsTea_qsOy.sole")),
    ("qsTea qsOy qsTea qsOy", (), ("qsTea_qsOy.sole", "qsTea_qsOy.sole")),
    (
        "qsMay qsTea qsIt",
        (),
        ("qsMay.loop.ex-bind-pulled-back", "qsTea.half.ex-y5", "qsIt.sole.en-y5.en-ext-1"),
    ),
    # ZWNJ splits the run, and an entry-bearing letter after it settles as its locked copy with no entry.
    ("qsIt zwnj qsTea", (), ("qsIt.sole", "uni200C", "qsTea.full.locked")),
    ("zwnj qsTea qsIt", (), ("uni200C", "qsTea.half.ex-y5.locked", "qsIt.sole.en-y5.en-ext-1")),
    ("zwnj qsMay qsTea", ("ss03",), ("uni200C", "qsMay.loop.ex-y5.locked.ex-ext-1", "qsTea.half.en-y5")),
    # Under ss03, nothing joins across a ZWNJ.
    ("qsMay zwnj qsTea", ("ss03",), ("qsMay.loop", "uni200C", "qsTea.full.locked")),
    ("qsMay space qsTea", ("ss03",), ("qsMay.loop", "space", "qsTea.full")),
    ("qsIt zwnj qsTea qsOy", (), ("qsIt.sole", "uni200C", "qsTea_qsOy.sole")),
    # The namer dot does not split runs but has no join surface, so nothing joins across it and nothing after it is locked.
    ("qsMay namer-dot qsIt", (), ("qsMay.loop", "periodcentered", "qsIt.sole")),
    # ss05's only unlock needs a qsEt left, which the mini spec lacks, so ss05 settles these windows as default does.
    ("qsMay qsTea", ("ss05",), ("qsMay.loop.ex-bind-pulled-back", "qsTea.full")),
    # AUTHORED-DATA FINDING: qsIt's baseline-exit refusal toward [qsTea, qsRoe, qsIt] applies only to unentered cells, so an entered qsIt joins a following qsIt at the baseline, where the old font breaks. ss04 settles the same way: its baseline-to-baseline unlock needs a baseline entry, and the middle ·It enters at the x-height.
    (
        "qsTea qsIt qsIt",
        (),
        ("qsTea.half.ex-y5", "qsIt.sole.en-y5.ex-y0.en-ext-1.ex-ext-1", "qsIt.sole.en-y0"),
    ),
    (
        "qsTea qsIt qsIt",
        ("ss04",),
        ("qsTea.half.ex-y5", "qsIt.sole.en-y5.ex-y0.en-ext-1.ex-ext-1", "qsIt.sole.en-y0"),
    ),
)

ROW_WINDOWS = tuple(dict.fromkeys((sequence, features) for sequence, features, _expected in ROWS))


@pytest.fixture(scope="module")
def row_settled():
    """Every window ROWS names, settled in one batch over the mini spec."""
    return _window_settled(SPEC, ROW_WINDOWS)


@pytest.fixture(scope="module")
def row_labels(row_settled):
    """The same batch as cell labels, which most rows assert against."""
    return {key: tuple(cell_label(SPEC, settled.cell) for settled in row) for key, row in row_settled.items()}


@pytest.mark.parametrize(
    "sequence,features,expected", ROWS, ids=[f"{row[0]}|{'+'.join(row[1]) or 'default'}" for row in ROWS]
)
def test_settlement_rows(row_labels, sequence, features, expected):
    assert row_labels[(sequence, features)] == expected


def test_exit_extension_amount_is_recorded_on_the_junction(row_settled):
    settled = row_settled[("qsMay qsIt", ())]
    assert settled[0].extension == 1
    assert settled[0].junction == "x-height"
    assert settled[1].extension == 0


def test_entry_extension_suppressed_when_left_junction_already_extended(row_settled):
    settled = row_settled[("qsMay qsIt qsMay", ())]
    assert settled[1].extension == 1
    assert settled[2].cell.adjustments == ()


def test_a_committed_junction_nothing_accepts_is_unreachable():
    """A left context forged with an exit at `top`, a height qsIt cannot enter at, is a window the lookahead closure never builds, and the crate returns an error instead of settling it. The error arrives as `settle.SettleError` with bucket `E-UNREACHABLE` and the crate's message. `engine.rs`'s `a_left_that_committed_a_junction_nothing_accepts_is_an_unaccepted_exit` is the crate-side test. `ex-y8` is `top` in the mini registry."""
    forged = LeftContext("letter", Settled(CellId("qsTea", "full", None, "top"), junction="top", extension=0))
    case = kernel_exec.case_line(forged, RightToken("letter", "qsIt"), (EDGE, EDGE, EDGE, EDGE))
    with pytest.raises(SettleError) as caught:
        kernel_exec.settle_cases(SPEC, [case], frozenset(), decode=kernel_exec.trace_of)
    assert caught.value.bucket == "E-UNREACHABLE"
    assert str(caught.value) == (
        "E-UNACCEPTED-EXIT: qsTea.full.ex-y8 committed an exit at top but qsIt has no acceptor cell "
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


def test_final_tiebreak_breaks_realization_tie_toward_the_join_and_flags_joint():
    spec = fixtures.synthetic_spec()
    trace = _traces(spec, [([0xE001, 0xE002, 0xE003], ())])[0][0]
    assert trace.settled.cell == CellId("A", "stroke", None, "x-height")
    assert trace.decided_stage == "tiebreak"
    assert trace.joint_tiebreak


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


def test_bind_contract_is_included_in_the_adjustments_grammar():
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
    """Both prospect windows settled with the simulated prospect off and on, one batch each. `SettlementModes` sets the mode flags on each kernel invocation, so the test does not change the process defaults."""
    settled = {}
    for simulated in (False, True):
        modes = kernel_exec.SettlementModes(simulated_prospect=simulated, follower_prefer_slots=True)
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
    """With two letters and nothing after them, the simulated prospect has no follower transition to run, so both mode sets settle the window the same way. `engine.rs`'s `the_prospect_bottoms_out_at_the_window_edge_where_both_modes_agree` checks that the third term is zero there, which the settled cells returned here do not show."""
    assert prospect_settled[("A B", False)] == prospect_settled[("A B", True)]


# Round-1 verdict pins over the loaded rune YAML, which carries the round-1 verdict records the mini spec does not. They check the greedy ·May·May pairing of the round-1 verdict (u-0341, "the old way seems nicer to write out by hand"): a chain of ·May joins in pairs at the baseline with a break between pairs, as the old font does at every length. The four-letter chain is the verdicted window. The five- and six-letter chains are the only check on qsMay's chain-interior prefer (policy.prefer[2], scoped on an unjoined ·May to its left): the acceptance oracle's windows stop at four letters, where the word-start prefer alone gives every result, and without the chain-interior prefer, chains of five or more fall back to the rejected grouping that defers to the tail.


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
        "qsBay.sole.ex-y0",
        "qsMay.loop.en-y0.en-con-1",
    )


TEA_CLEARANCE_FEATURES = ((), ("ss03",), ("ss04",), ("ss05",), ("ss03", "ss05"))
MAY_TEA_JAI_LEADS = ("qsI", "qsAh")
MAY_TEA_JAI_WINDOWS = ("qsTea qsJai", "qsMay qsTea qsJai", "qsMay qsTea qsJai qsTea")


@pytest.mark.parametrize("lead", MAY_TEA_JAI_LEADS)
@pytest.mark.parametrize("features", TEA_CLEARANCE_FEATURES)
def test_may_tea_jai_keeps_the_narrow_crown(real_labels, lead, features):
    assert real_labels[(f"{lead} qsMay qsTea qsJai", features)] == (
        f"{lead}.{'loop' if lead == 'qsI' else 'sole'}.ex-y5.ex-ext-1",
        "qsMay.loop.en-y5",
        "qsTea.half.ex-y5.ex-bind-padded-left-1",
        "qsJai.sole.en-y5.en-con-1",
    )
    assert real_labels[("qsTea qsJai", features)] == (
        "qsTea.half.ex-y5",
        "qsJai.sole.en-y5.en-con-1",
    )
    assert real_labels[("qsMay qsTea qsJai", features)] == (
        ("qsMay.loop.ex-y5.ex-ext-1", "qsTea.full.en-y5", "qsJai.sole")
        if "ss03" in features
        else ("qsMay.loop", "qsTea.half.ex-y5.ex-bind-padded-left-1", "qsJai.sole.en-y5.en-con-1")
    )
    follower_labels = real_labels[("qsMay qsTea qsJai qsTea", features)]
    assert follower_labels[1] == (
        "qsTea.full.en-y5" if "ss03" in features else "qsTea.half.ex-y5.ex-bind-padded-left-1"
    )


TEA_OTHER_FOLLOWERS = (
    "qsAwe",
    "qsAwe qsAh",
    "qsEight",
    "qsEight qsAh",
    "qsEight qsYe",
    "qsEight qsIt",
    "qsEt",
    "qsEt qsAh",
    "qsEt qsGay",
    "qsIt",
    "qsIt qsDay",
    "qsIt qsAh",
    "qsJay",
    "qsJay qsAh",
    "qsJay qsI",
    "qsJay qsUtter",
    "qsNo qsAwe",
    "qsNo qsFee",
    "qsOx",
    "qsOx qsAh",
    "qsRoe",
    "qsRoe qsAh",
    "qsRoe qsI",
    "qsZoo",
    "qsZoo qsAh",
)
TEA_JAI_LEADS = ("qsWay qsGay", "qsGay", "qsI qsMay", "qsThey", "qsThey qsZoo", "qsVie", "qsZoo")
TEA_CLEARANCE_NAMES = (
    *(f"qsI qsMay qsTea {follower}" for follower in TEA_OTHER_FOLLOWERS),
    *(f"{lead} qsTea qsJai{tail}" for lead in TEA_JAI_LEADS for tail in ("", " qsDay", " qsI", " qsUtter")),
)
TEA_JAI_PAIR_NAMES = (
    "qsTea qsJai",
    "qsTea qsJai qsDay",
    "qsTea qsJai qsI",
    "qsTea qsJai qsUtter",
    "qsTea qsJai space",
    "qsTea qsJai zwnj",
    "qsTea qsJai qsUtter qsDay",
    "qsTea qsJai qsTea qsDay",
)
TEA_JAI_CONTEXT_NAMES = tuple(
    f"{lead} qsTea qsJai{tail}"
    for lead in TEA_JAI_LEADS
    for tail in ("", " qsDay", " qsI", " qsUtter", " qsUtter qsDay", " qsUtter space", " qsUtter zwnj")
)


@pytest.mark.parametrize("features", TEA_CLEARANCE_FEATURES)
@pytest.mark.parametrize("names", TEA_CLEARANCE_NAMES)
def test_half_tea_keeps_nonadjacent_ink_separate(real_spec, real_settled, names, features):
    settled = real_settled[(names, features)]
    index = next(i for i, item in enumerate(settled) if item.cell.rune == "qsTea")
    predecessor, tea, follower = settled[index - 1 : index + 2]
    assert tea.cell.stance == "half" and tea.cell.entry is None
    assert predecessor.junction is None and tea.junction == follower.cell.entry == "x-height"
    predecessor_record, tea_record, follower_record = (
        geometry.realize(real_spec, surface.resolve_cell(real_spec, item.cell))
        for item in (predecessor, tea, follower)
    )
    assert tea_record.exit is not None and follower_record.entry is not None
    assert geometry.junction_gap(tea_record, follower_record, "x-height") == 0
    tea_origin = max(map(len, predecessor_record.bitmap)) + 1
    follower_origin = tea_origin + tea_record.exit[0] - follower_record.entry[0]
    predecessor_ink = geometry.ink_cells(predecessor_record)
    follower_ink = geometry.ink_cells(follower_record, follower_origin)
    assert min(x for x, y in follower_ink if y < 5) >= tea_origin
    assert (
        min(
            max(abs(left_x - right_x), abs(left_y - right_y))
            for left_x, left_y in predecessor_ink
            for right_x, right_y in follower_ink
        )
        >= 2
    )
    assert tea.cell.adjustments == (
        ("ex-bind-padded-left-1",) if follower.cell.rune in ("qsJai", "qsJai_qsUtter") else ()
    )


@pytest.mark.parametrize("features", TEA_CLEARANCE_FEATURES)
@pytest.mark.parametrize("names", (*TEA_JAI_PAIR_NAMES, *TEA_JAI_CONTEXT_NAMES))
def test_half_tea_keeps_the_jai_crown_narrow_in_context(real_spec, real_settled, names, features):
    settled = real_settled[(names, features)]
    index = next(i for i, item in enumerate(settled) if item.cell.rune == "qsTea")
    tea, follower = settled[index : index + 2]
    assert tea.cell == CellId("qsTea", "half", None, "x-height", ("ex-bind-padded-left-1",) if index else ())
    assert follower.cell.rune in ("qsJai", "qsJai_qsUtter")
    assert "en-con-1" in follower.cell.adjustments
    assert tea.junction == follower.cell.entry == "x-height"
    if index:
        assert settled[index - 1].junction is None
    tea_record, follower_record = (
        geometry.realize(real_spec, surface.resolve_cell(real_spec, item.cell)) for item in (tea, follower)
    )
    assert geometry.junction_gap(tea_record, follower_record, "x-height") == 0
    assert tea_record.exit is not None and follower_record.entry is not None
    follower_origin = tea_record.exit[0] - follower_record.entry[0]
    tea_ink = geometry.ink_cells(tea_record)
    tea_x = min(x for x, y in tea_ink)
    ink = {(x - tea_x, y) for x, y in tea_ink | geometry.ink_cells(follower_record, follower_origin)}
    expected_crown = {0, 1, 2, 3} | ({7, 8} if follower.cell.rune == "qsJai_qsUtter" else set())
    assert {x for x, y in ink if y == 5} == expected_crown


@pytest.mark.parametrize("features", TEA_CLEARANCE_FEATURES)
def test_tea_jai_tea_day_keeps_its_joins(real_settled, features):
    settled = real_settled[("qsTea qsJai qsTea qsDay", features)]
    middle_join = "baseline" if "ss05" in features else None
    assert tuple(item.junction for item in settled) == ("x-height", middle_join, "baseline", None)
    assert settled[2].cell == CellId("qsTea", "full", middle_join, "baseline", ())


def test_tea_jai_clearance_does_not_enter_the_isolated_overlay(real_spec):
    from rebuild.pipeline.explain import explain_many

    reports = explain_many(
        real_spec,
        _requests(
            real_spec,
            (
                ("qsWay qsGay qsTea qsJai", frozenset(("ss10",))),
                ("qsGay qsTea qsJai qsUtter", frozenset(("ss10",))),
            ),
        ),
    )
    for report in reports:
        assert len(report.settled) == len(report.codepoints)
        for item in report.settled:
            assert item.cell.entry is None and item.cell.exit is None and item.junction is None
            assert item.cell.adjustments == ()


# The orphaned-·Tea windows (doc/rebuild-design.md §3.4). In ·Day·Tea·Utter·Low and ·Oy·Tea·Utter·Low the predecessor would leave its baseline exit unjoined expecting ·Tea to join forward into ·Utter, and qsUtter's ·Low-scoped prefer then refuses that entry, leaving ·Tea joined on neither side. The three-letter `then:` chains on qsDay.policy.prefer[1] and qsOy/qsTea_qsOy.policy.prefer[0] keep the predecessor's exit in those windows, which matches the old font's `·Day ~b~ ·Tea | ·Utter.alt ~b~ ·Low` grouping. The other windows check that the predecessor still leaves its exit unjoined everywhere else. The depth-4 rows show the same change one letter further on: the entry-live exception in qsDay.policy.prefer[5] reads the fourth raw glyph, so in ·Pea·Day·Tea·Utter·Tea·May ·Day leaves its exit unjoined and ·Tea joins forward into ·Utter when the fourth letter after ·Day is one that would otherwise leave ·Utter joined on neither side (the innermost `then:` list of qsDay.policy.prefer[5]). ·Day keeps its exit when the tail can still join (·Pea, or the end of the text), and under ss03 ·Utter joins the following ·Tea at the x-height. These windows are five and six letters long, past the acceptance oracle's four-letter maximum length, so only these rows check them.


ORPHANED_TEA_ROWS = (
    (
        "qsDay qsTea qsUtter qsLow",
        ("qsDay.full.ex-y0", "qsTea.full.en-y0", "qsUtter.alternate.ex-y0", "qsLow.sole.en-y0"),
    ),
    (
        "qsOy qsTea qsUtter qsLow",
        ("qsOy.sole.ex-y0", "qsTea.full.en-y0", "qsUtter.alternate.ex-y0", "qsLow.sole.en-y0"),
    ),
    ("qsDay qsTea qsUtter", ("qsDay.full", "qsTea.full.ex-y0", "qsUtter.mono.en-y0")),
    (
        "qsDay qsTea qsUtter qsMay",
        ("qsDay.full", "qsTea.full.ex-y0", "qsUtter.mono.en-y0.ex-y5", "qsMay.loop.en-y5"),
    ),
    ("qsTea qsUtter qsLow", ("qsTea.full", "qsUtter.alternate.ex-y0", "qsLow.sole.en-y0")),
    (
        "qsDay qsIt qsUtter qsLow",
        ("qsDay.full.ex-y0", "qsIt.sole.en-y0", "qsUtter.alternate.ex-y0", "qsLow.sole.en-y0"),
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


# The §5.7 late-formation guard over the loaded rune YAML. The Manual pin `·Day | ·Utter.alt ·Low` (site/the-manual.html) is the case against unconditional formation: the ·Day+Utter ligature exits only at the x-height, ·Low enters only at the baseline, and only the unformed alternate ·Utter can make the baseline join. The guard withholds formation there, and qsUtter.policy.prefer[2] (a follower prefer, §5.9) makes ·Day withhold its exit so the alternate ·Utter can join ·Low.


def test_late_formation_yields_before_low(real_labels):
    assert real_labels[("qsDay qsUtter qsLow", ())] == (
        "qsDay.full",
        "qsUtter.alternate.ex-y0",
        "qsLow.sole.en-y0",
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


def test_a_formed_followers_verdict_is_the_same_whatever_follows_it(real_spec, real_guard):
    """Where the guard's two raw slots form a follower ligature, the guard faces that ligature with the text ending after it, so the verdict equals the sweep's row for the ligature before the edge. The formation lookup cannot see the slot after the follower, so that row must also equal the row for every other second slot the sweep covers, with the follower ligature held formed. A future follower prefer whose verdict depends on that slot fails here instead of compiling a verdict only the text's end makes true."""
    formed = {tuple(rune.sequence): name for name, rune in real_spec.runes.items() if rune.sequence}
    seconds = {right2 for _liga, _right1, right2 in real_guard}
    checked = 0
    for (liga, right1, right2), blocked in real_guard.items():
        if right1.kind != "letter" or right2.kind != "letter":
            continue
        if (follower := formed.get((right1.rune, right2.rune))) is None:
            continue
        token = RightToken("letter", follower)
        assert real_guard[(liga, token, EDGE)] == blocked, (liga, follower)
        assert {real_guard[(liga, token, after)] for after in seconds} == {blocked}, (liga, follower)
        checked += 1
    assert checked, "the live alphabet has no formed follower, so this test checks nothing"


@pytest.fixture(scope="module")
def guard_by_configuration(real_spec) -> dict[frozenset[str], kernel_exec.FormationGuard]:
    """One single-configuration guard sweep for every subset of the capability features the quantified guard covers, plus every acceptance configuration, so ss10, which no stance unlocks, is swept too."""
    features = spec_load.capability_features(real_spec)
    subsets = {
        frozenset(subset)
        for size in range(len(features) + 1)
        for subset in itertools.combinations(features, size)
    }
    subsets |= {conform.features_for_config(config) for config in conform.ACCEPTANCE_CONFIGS}
    return {subset: kernel_exec.guard_sweep_under(real_spec, subset) for subset in subsets}


def test_no_configuration_frees_a_window_the_quantified_guard_blocks(real_guard, guard_by_configuration):
    """Every configuration's guard verdict map has the quantified map's keys and blocks wherever the quantified map blocks, so a single configuration's map is the same or stricter."""
    assert len(guard_by_configuration) > len(conform.ACCEPTANCE_CONFIGS)
    for features, verdict_map in guard_by_configuration.items():
        assert verdict_map.keys() == real_guard.keys(), sorted(features)
        assert all(verdict_map[key] for key, blocked in real_guard.items() if blocked), sorted(features)


def test_ss03_and_ss05_are_the_sets_the_guard_verdict_map_depends_on(
    real_spec, real_guard, guard_by_configuration
):
    """The guard verdict map is not the same in every configuration, and ss03 and ss05 are the only features that change it. Every configuration with ss03 sweeps the quantified map. Every configuration without it sweeps a stricter map, which also blocks windows `(X_qsUtter, ·Tea, r2)` for the qsUtter-trailing ligatures that qsTea's ss03 unlock names as lefts: there, the ss03 x-height entry into full ·Tea is the only join the ligature can offer the ·Tea. The configurations without ss03 or ss05 (default, ss04, ss10) share one such map. The ones with ss05 but not ss03 (ss05, ss04+ss05) share a second map that blocks every window the first blocks and more, because ss05's both-baseline pairing lets the ·Tea take the unformed ·Utter's baseline join and still join r2 at the baseline. The font is correct either way, because formation runs before the ss markers and `settle.form_ligatures` reads the quantified sweep in every configuration: ·Day·Utter·Tea forms the ligature under default with ·Tea unjoined, and ·Tea joins it only under ss03 (`test_formation_survives_where_the_ligature_serves_the_follower` checks both). So a configuration delta may assume that ss04 and ss10 change no formation verdict, and that ss03 and ss05 change only these."""
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
    for features, verdict_map in guard_by_configuration.items():
        disagreements = frozenset(key for key, blocked in verdict_map.items() if blocked != real_guard[key])
        if "ss03" in features:
            assert not disagreements, sorted(features)
        else:
            without_ss03[features] = disagreements
    assert {
        frozenset(),
        frozenset({"ss04"}),
        frozenset({"ss05"}),
        frozenset({"ss04", "ss05"}),
        frozenset({"ss10"}),
    } <= without_ss03.keys()
    (without_ss05,) = {
        disagreements for features, disagreements in without_ss03.items() if "ss05" not in features
    }
    (with_ss05,) = {disagreements for features, disagreements in without_ss03.items() if "ss05" in features}
    assert without_ss05, "the finding has dissolved: every configuration sweeps the quantified map"
    assert without_ss05 < with_ss05
    assert {right1 for _ligature, right1, _right2 in with_ss05} == {tea}
    assert {ligature for ligature, _right1, _right2 in without_ss05} == ligatures_ss03_names
    assert {ligature for ligature, _right1, _right2 in with_ss05} == ligatures_ss03_names
    assert not any(real_guard[key] for key in with_ss05)


def test_the_guard_reads_letters_only_and_indexes_the_verdict_map_it_was_given(real_guard):
    """`guard_blocks` never blocks when the first slot is not a letter, because the guard only protects a following letter's join, so the sweep has no rows for boundaries. Every other triple is an indexed read, so a window the verdict map does not cover raises KeyError. A `.get(key, False)` there would silently form every ligature the emitted lookup withholds."""
    utter = RightToken("letter", "qsUtter")
    assert not guard_blocks(real_guard, "qsDay_qsUtter", EDGE, utter)
    assert not guard_blocks(real_guard, "qsDay_qsUtter", RightToken("space"), utter)
    assert guard_blocks(real_guard, "qsDay_qsUtter", RightToken("letter", "qsLow"), EDGE)
    with pytest.raises(KeyError):
        guard_blocks(real_guard, "qsDay_qsUtter", RightToken("letter", "qsNotARune"), EDGE)


# Ligature-transparent left scopes (`spec_load._expand_ligature_lefts`): a family named in an entry `from:` scope also admits every registered ligature whose sequence ends in that family. So the windows the review rejected settle the half ·Pea after ·See+Utter as they do after a bare ·Utter, and the follower's join takes effect. The ·No row checks an approved divergence: full ·Pea joins flipped ·No at the baseline after every qsUtter-trailing left.


LIGATURE_TRANSPARENT_PEA_ROWS = (
    (
        "qsSee qsUtter qsPea qsRoe",
        ("qsSee_qsUtter.sole.ex-y5", "qsPea.half.en-y5.ex-y5", "qsRoe.sole.en-y5.en-con-1"),
    ),
    (
        "qsSee qsUtter qsPea qsIt",
        ("qsSee_qsUtter.sole.ex-y5", "qsPea.half.en-y5.ex-y5", "qsIt.sole.en-y5"),
    ),
    (
        "qsSee qsUtter qsPea qsEt",
        ("qsSee_qsUtter.sole.ex-y5", "qsPea.half.en-y5.ex-y5", "qsEt.sole.en-y5.en-ext-1"),
    ),
    (
        "qsSee qsUtter qsPea qsNo",
        ("qsSee_qsUtter.sole.ex-y5", "qsPea.full.en-y5.ex-y0", "qsNo.flipped.en-y0"),
    ),
    (
        "qsUtter qsPea qsRoe",
        ("qsUtter.mono.ex-y5", "qsPea.half.en-y5.ex-y5", "qsRoe.sole.en-y5.en-con-1"),
    ),
)


@pytest.mark.parametrize(
    "sequence,expected",
    LIGATURE_TRANSPARENT_PEA_ROWS,
    ids=[row[0].replace(" ", "|") for row in LIGATURE_TRANSPARENT_PEA_ROWS],
)
def test_ligature_left_admits_trailing_family_scopes(real_labels, sequence, expected):
    assert real_labels[(sequence, ())] == expected


def test_resolve_record_breaks_the_tea_oy_it_no_conflict(real_labels):
    """A resolve record against a named record (§5.8): qsTea_qsOy's resolve against qsIt's `withhold-before-no-after-oy` prefer picks the ligature's baseline exit in the tied ·It·No windows, so the ligature renders like the approved bare-·Oy case instead of raising E-INCOMPARABLE."""
    assert real_labels[("qsTea qsOy qsIt qsNo qsAh", ())] == (
        "qsTea_qsOy.sole.ex-y0",
        "qsIt.sole.en-y0",
        "qsNo.flipped.ex-y0",
        "qsAh.sole.en-y0",
    )


# Every window the real-spec tables above name, settled in one batch: one guard sweep and one `settle_sequences` call, six positions deep. A test that asks `real_labels` for a window not listed here gets a KeyError.
REAL_WINDOWS = tuple(
    dict.fromkeys(
        (
            *((" ".join(["qsMay"] * length), ()) for length, _expected in MAY_CHAIN_ROWS),
            *(
                (names, features)
                for features in TEA_CLEARANCE_FEATURES
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
            *(
                (names, features)
                for names in (*TEA_CLEARANCE_NAMES, *TEA_JAI_PAIR_NAMES, *TEA_JAI_CONTEXT_NAMES)
                for features in TEA_CLEARANCE_FEATURES
            ),
        )
    )
)


@pytest.fixture(scope="module")
def real_guard(real_spec):
    """The crate's complete late-formation guard verdict map for the loaded rune YAML, the same memoized sweep `_traces` forms every real-spec window against."""
    return kernel_exec.guard_sweep(real_spec)


@pytest.fixture(scope="module")
def real_settled(real_spec):
    """Every window of REAL_WINDOWS, settled in one batch over the loaded rune YAML."""
    return _window_settled(real_spec, REAL_WINDOWS)


@pytest.fixture(scope="module")
def real_labels(real_spec, real_settled):
    """The same real-spec batch as cell labels."""
    return {
        key: tuple(cell_label(real_spec, settled.cell) for settled in row)
        for key, row in real_settled.items()
    }
