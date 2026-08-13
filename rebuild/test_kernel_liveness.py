"""The liveness-grain harness and the world reflection beside it: the two things sub-issue #45 asks of the Python side before any Rust exists.

What the liveness harness has to be trusted about is narrow and load-bearing. The keys file is a contract with a binary that does not answer it yet, so its three shapes are spelled here rather than discovered when the verb lands; a fibre answer's compact JSON is compared against the deriver's own partition, because the byte spelling — key order, separators, the label each token wears — is what the Rust side has to reproduce and no later gate would catch a spelling drift as anything but a wall of divergences. The seeded quad draw is a function of its seed alone, or two builds asked different questions and the cross-diff between them means nothing. And the arms have to line up with each other: the fibre arm asks exactly where the third sweep said live, which is what makes a missing fibre line a reported divergence rather than a silently shorter file.

The verdicts themselves are asserted to come through `third_slot_filter` and `fourth_slot_filter` rather than through `_ProspectLiveness`, which is the one place a plausible harness silently answers a different question: the filter is where the own-rune chain arm and the probe arm are ORed together, and the chain arm alone decides plenty of triples the probe would never be asked about.

The fixpoint harness's share is smaller and entirely pure: which kernel flags one Python world calls for. It stopped being a constant when the shipping world landed, and a mapping that drifts would compare two different fixpoints and call the difference a port defect.
"""

import dataclasses
import json

import pytest

from rebuild.pipeline import conform, fixtures, settle, table
from rebuild.pipeline.model import Condition, PolicyRecord, When
from rebuild.tools import kernel_fixpoint, kernel_liveness
from rebuild.tools.kernel_liveness import World

CONFIG = "default"
FEATURES = conform.features_for_config(CONFIG)
QUADS = 40
SPEC = fixtures.mini_spec()
SHIPPING = World(True, True)
PINNED = World(False, False)
# A feature the configuration under test does not turn on, which is what makes the chained fixture below a pure census edit.
DORMANT_FEATURE = "ss10"
# The right-slot families the fixture's chain names, hop by hop: three `then:` hops, so `right_chain_reach` is 3 and the owning rune enters both `depth3_inputs` and `depth4_inputs` at once. The deepest hop's family is never matched against anything — the filters hold that slot to UNKNOWN, which is exactly what makes the verdict unknown and the slot live.
CHAIN_HOPS = ("qsPea", "qsTea", "qsMay", "qsOy")
CHAIN_OWNER = "qsIt"
# The two keys the chain arm alone opens: the third-slot filter reads the window one hop short of the chain's reach, the fourth-slot filter one hop further in.
CHAIN_TRIPLE = (CHAIN_OWNER, *CHAIN_HOPS[:2])
CHAIN_QUAD = (CHAIN_OWNER, *CHAIN_HOPS[:3])
ANSWERS_BESIDE_THE_KEYS = '"$(dirname "$3")/$(basename "$3" | sed \'s/^keys-/python-/\')"'
ECHO_STUB = f'if [ "$1" = "liveness-cases" ]; then cat {ANSWERS_BESIDE_THE_KEYS}; exit 0; fi\nexit 2'
MANGLE_STUB = (
    f'if [ "$1" = "liveness-cases" ]; then cat {ANSWERS_BESIDE_THE_KEYS}'
    " | sed -E '1s/(live|dead)$/mangled/'; exit 0; fi\nexit 2"
)


def _stub_kernel(tmp_path, body: str):
    path = tmp_path / "stub-kernel"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def _chained_spec():
    """The mini fixture with a depth-3-reach prefer chain hung on one rune, gated on a feature this configuration does not turn on.

    The mini fixture's own `depth3_inputs` and `depth4_inputs` are empty, so on it the filters' chain arm is vacuous and their answers are the liveness probe's answers at every key — which is precisely the confusion the harness must not be allowed to make, and precisely what an assertion on the plain fixture cannot see. The chain is a census edit and nothing more: `third_slot_filter` reads `when.right` without consulting the feature gate, while `Engine.when_matches` refuses a dormant-feature record before it reads anything else, so the chain arm opens keys the probe calls dead and no settlement outcome moves. The tests below assert both halves of that rather than trusting it.
    """
    chain = Condition(family=(CHAIN_HOPS[-1],))
    for family in reversed(CHAIN_HOPS[:-1]):
        chain = Condition(family=(family,), then=chain)
    record = PolicyRecord(kind="prefer", when=When(right=chain, feature=DORMANT_FEATURE))
    rune = SPEC.runes[CHAIN_OWNER]
    policy = dataclasses.replace(rune.policy, prefer=rune.policy.prefer + (record,))
    runes = dict(SPEC.runes)
    runes[CHAIN_OWNER] = dataclasses.replace(rune, policy=policy)
    return dataclasses.replace(SPEC, runes=runes)


CHAINED = _chained_spec()


def _sweep(world: World, fibre_cap: int | None = None, spec=SPEC, exhaustive: bool = False):
    quads = kernel_liveness.quad_keys(spec, QUADS, kernel_liveness.DEFAULT_SEED, exhaustive)
    return kernel_liveness.sweep_python(spec, FEATURES, world, quads, fibre_cap)


def _engine(spec, world: World):
    return settle.Engine(
        spec,
        FEATURES,
        trace_memo=True,
        simulated_prospect=world.simulated_prospect,
        vote_slots=world.vote_slots,
    )


@pytest.fixture(scope="module")
def shipping():
    return _sweep(SHIPPING)


@pytest.fixture(scope="module")
def chained():
    """The chained fixture's sweep asks the whole quad space rather than a sample of it, so the one key its chain arm opens is certain to be in the file — which is what makes the fourth-slot assertion a statement about an emitted line and not only about a closure."""
    return _sweep(SHIPPING, spec=CHAINED, exhaustive=True)


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    """The whole tool run twice over one spec, into two directories, with no binary to ask — the form the cross-build comparison is cut from and the only form testable before the verb exists."""
    out = []
    for name in ("liveness-first", "liveness-second"):
        directory = tmp_path_factory.mktemp(name)
        status = kernel_liveness.main(
            ["--specs", "mini", "--python-only", "--count", str(QUADS), "--out", str(directory)]
        )
        assert status == 0
        out.append(directory)
    return tuple(out)


# --- the keys file ------------------------------------------------------------


def test_two_runs_at_one_seed_write_the_same_keys(written):
    """A keys file that moved between runs would make two candidate kernels' answer files incomparable, which is the whole point of writing it down rather than piping it."""
    first, second = written
    names = sorted(path.name for path in first.iterdir())
    assert names == sorted(path.name for path in second.iterdir())
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_the_third_sweep_asks_every_triple_in_sorted_rune_order():
    """Exhaustive rather than sampled: the surface is cubic in a couple of dozen runes, so there is no triple a port can be lucky about and no sampling to argue over."""
    names = sorted(SPEC.runes)
    triples = kernel_liveness.triple_keys(SPEC)
    assert len(triples) == len(names) ** 3
    assert len(set(triples)) == len(triples)
    assert triples[0] == (names[0], names[0], names[0])
    assert triples[1] == (names[0], names[0], names[1])
    assert triples[-1] == (names[-1], names[-1], names[-1])


def test_the_quad_draw_is_without_replacement_and_a_function_of_its_seed():
    drawn = kernel_liveness.quad_keys(SPEC, QUADS, kernel_liveness.DEFAULT_SEED, False)
    assert len(drawn) == QUADS
    assert len(set(drawn)) == QUADS
    assert drawn == kernel_liveness.quad_keys(SPEC, QUADS, kernel_liveness.DEFAULT_SEED, False)
    assert drawn != kernel_liveness.quad_keys(SPEC, QUADS, kernel_liveness.DEFAULT_SEED + 1, False)
    assert all(name in SPEC.runes for quad in drawn for name in quad)


def test_a_draw_wider_than_the_space_is_the_whole_space():
    """The mini fixture's quad space is smaller than the live alphabet's default draw, so the sampled path has to degrade to the exhaustive one rather than raise."""
    size = len(SPEC.runes)
    whole = kernel_liveness.quad_keys(SPEC, size**4 * 2, kernel_liveness.DEFAULT_SEED, False)
    assert len(whole) == size**4
    assert whole == kernel_liveness.quad_keys(SPEC, QUADS, kernel_liveness.DEFAULT_SEED, True)


def test_the_keys_file_is_the_answers_with_the_verdicts_stripped(written, shipping):
    """The verb echoes the key it was asked and then its answer, so one file is the other's prefix column — which is what lets the comparison be positional and the keys file be handed to any number of builds."""
    keys = (written[0] / f"keys-mini-{CONFIG}-{SHIPPING.label}.txt").read_text().splitlines()
    answers = (written[0] / f"python-mini-{CONFIG}-{SHIPPING.label}.txt").read_text().splitlines()
    assert keys == shipping.keys
    assert answers == shipping.answers
    assert [line.rsplit("\t", 1)[0] for line in answers] == keys


# --- the three answer shapes --------------------------------------------------


def test_every_key_wears_its_shape_and_its_answer(shipping):
    counts = dict.fromkeys(kernel_liveness.ARMS, 0)
    for line in shipping.answers:
        fields = line.split("\t")
        shape, answer = fields[0], fields[-1]
        if shape == "3":
            assert len(fields) == 5
            assert answer in {"live", "dead"}
            counts["triple"] += 1
        elif shape == "4":
            assert len(fields) == 6
            assert answer in {"live", "dead"}
            counts["quad"] += 1
        else:
            assert shape == "fibres"
            assert len(fields) == 5
            assert set(json.loads(answer)) == {"boundaries", "fibres"}
            counts["fibre"] += 1
        assert all(name in SPEC.runes for name in fields[1:-1])
    assert counts == shipping.counts
    assert all(count for count in counts.values())


def test_the_chained_fixture_moves_the_census_and_nothing_else():
    """The fixture's own premise, asserted before anything is built on it: the dormant-feature record puts its rune into both deep censuses, and leaves every liveness verdict exactly where the plain fixture had it. Without the first half the tests below are vacuous; without the second half a disagreement between the filter and the probe would prove nothing about which one the harness read."""
    assert not table.depth3_inputs(SPEC) and not table.depth4_inputs(SPEC)
    assert table.depth3_inputs(CHAINED) == frozenset({CHAIN_OWNER})
    assert table.depth4_inputs(CHAINED) == frozenset({CHAIN_OWNER})
    plain = table.third_slot_filter(SPEC, FEATURES, _engine(SPEC, SHIPPING))
    probe = table._liveness_probe(CHAINED, _engine(CHAINED, SHIPPING))
    triples = kernel_liveness.triple_keys(SPEC)
    assert [probe.third_live(*key) for key in triples] == [plain(*key) for key in triples]


def test_a_third_slot_answer_is_the_whole_filter_verdict(chained):
    """The trap this harness exists to avoid walking into: liveness has to be read through the filter closure, where the own-rune chain arm and the probe arm are ORed together, and never off `_ProspectLiveness` directly — the chain arm alone decides triples the probe is never asked about, and a harness that read the probe would compare Python's probe against the kernel's filter. The assertion is on the chained fixture because on the plain one the two are the same function."""
    engine = _engine(CHAINED, SHIPPING)
    third_slot_matters = table.third_slot_filter(CHAINED, FEATURES, engine)
    probe = table._liveness_probe(CHAINED, engine)
    triples = kernel_liveness.triple_keys(CHAINED)
    through_the_filter = [third_slot_matters(*key) for key in triples]
    through_the_probe = [probe.third_live(*key) for key in triples]
    pairs = list(zip(triples, through_the_filter, through_the_probe))
    assert [key for key, whole, arm in pairs if whole and not arm] == [CHAIN_TRIPLE]
    assert not [key for key, whole, arm in pairs if arm and not whole]
    assert chained.answers[: chained.counts["triple"]] == [
        f"3\t{a}\t{b}\t{c}\t{'live' if whole else 'dead'}"
        for (a, b, c), whole in zip(triples, through_the_filter)
    ]
    assert f"3\t{"\t".join(CHAIN_TRIPLE)}\tlive" in chained.answers


def test_a_fourth_slot_answer_is_the_whole_filter_verdict(chained):
    engine = _engine(CHAINED, SHIPPING)
    fourth_slot_matters = table.fourth_slot_filter(CHAINED, FEATURES, engine)
    probe = table._liveness_probe(CHAINED, engine)
    assert fourth_slot_matters(*CHAIN_QUAD)
    assert not probe.fourth_live(*CHAIN_QUAD)
    start = chained.counts["triple"]
    quads = kernel_liveness.quad_keys(CHAINED, QUADS, kernel_liveness.DEFAULT_SEED, True)
    assert CHAIN_QUAD in quads
    assert chained.answers[start : start + chained.counts["quad"]] == [
        f"4\t{a}\t{b}\t{c}\t{d}\t{'live' if fourth_slot_matters(a, b, c, d) else 'dead'}"
        for a, b, c, d in quads
    ]
    assert f"4\t{"\t".join(CHAIN_QUAD)}\tlive" in chained.answers


def test_a_fibre_line_spells_the_partition_the_deriver_built(shipping):
    """The byte spelling is the contract, not the partition as a value: compact separators, the four keys in this order, boundary and member tokens through `table._right_token_label`, and `r4_groups` empty exactly where the fourth slot is dead."""
    engine = settle.Engine(SPEC, FEATURES, trace_memo=True, simulated_prospect=True, vote_slots=True)
    fourth_slot_matters = table.fourth_slot_filter(SPEC, FEATURES, engine)
    deriver = table._DeepFibreDeriver(
        SPEC, engine, table._WindowOptions(SPEC), table._liveness_probe(SPEC, engine), fourth_slot_matters
    )
    lines = shipping.answers[-shipping.counts["fibre"] :]
    assert lines
    for line in lines:
        shape, input_family, right1, right2, answer = line.split("\t")
        assert shape == "fibres"
        assert answer == kernel_liveness.fibre_answer(deriver.context(input_family, right1, right2))
        assert " " not in answer
        payload = json.loads(answer)
        assert list(payload) == ["boundaries", "fibres"]
        for fibre in payload["fibres"]:
            assert list(fibre) == ["members", "fourth_matters", "r4_groups"]
            assert fibre["members"]
            assert bool(fibre["r4_groups"]) == fibre["fourth_matters"]
            assert all(group for group in fibre["r4_groups"])


def test_a_fibre_answer_spells_the_boundary_labels_the_decision_table_uses(shipping):
    """The one thing comparing `fibre_answer` against the deriver cannot catch, because both sides would be wrong in lockstep: the label map itself. `table._right_token_label` spells boundaries the way the decision table's columns do, and *not* the way the neighbouring `guard-sweep` verb prints them — that verb's `zwnj` and `namer-dot` are the spellings a port reaches for by mistake, and nothing else in this file would notice."""
    answer = next(
        line for line in shipping.answers if line.startswith("fibres\tqsMay\tqsMay\tqsMay\t")
    ).split("\t")[-1]
    assert answer.startswith('{"boundaries":["#EDGE","space","uni200C","periodcentered"],"fibres":[')
    assert '"r4_groups":[["#EDGE"],["space"],["uni200C"],["periodcentered"]' in answer
    for line in shipping.answers[-shipping.counts["fibre"] :]:
        assert "zwnj" not in line
        assert "namer-dot" not in line


def test_the_fibre_arm_asks_exactly_where_the_third_sweep_said_live(shipping):
    """A fibre is only defined for a live letter-letter context, so the third arm's answers are the fibre arm's key list — which makes the fibre arm a second reading of the first: a port that judged a context dead is never asked for its fibres, and the lines it did not write are the divergence."""
    live = [
        tuple(line.split("\t")[1:4])
        for line in shipping.answers[: shipping.counts["triple"]]
        if line.endswith("\tlive")
    ]
    asked = [tuple(line.split("\t")[1:4]) for line in shipping.answers[-shipping.counts["fibre"] :]]
    assert asked == live


def test_the_fibre_cap_clips_its_own_arm_and_nothing_else(chained):
    """The cap is an iteration-loop knob, so what it must not do is change the questions: the third and fourth arms answer the same keys with it as without. Asserted on the chained fixture, whose shipping world has more than one live context — on the plain one a cap of one is the whole arm and an implementation that ignored the flag would pass."""
    assert chained.counts["fibre"] > 1
    capped = _sweep(SHIPPING, fibre_cap=1, spec=CHAINED, exhaustive=True)
    assert capped.counts["fibre"] == 1
    assert capped.counts["triple"] == chained.counts["triple"]
    assert capped.counts["quad"] == chained.counts["quad"]
    kept = chained.counts["triple"] + chained.counts["quad"]
    assert capped.answers[:kept] == chained.answers[:kept]
    assert capped.answers[kept:] == chained.answers[kept : kept + 1]


def test_a_world_with_no_probe_arm_derives_no_fibres():
    """With both semantics flags off no engine grows a `_ProspectLiveness`, so there is no fibre source and the filters answer from the own-rune chain census alone; asking for fibres there would be asking the kernel to build a deriver its own world has no instance for."""
    pinned = _sweep(PINNED)
    assert pinned.counts["fibre"] == 0
    assert not any(line.startswith("fibres\t") for line in pinned.answers)
    assert pinned.counts["triple"] == len(SPEC.runes) ** 3


# --- the invocation and the comparison ----------------------------------------


def test_the_world_flags_name_only_what_is_off():
    """Off is what carries a flag, so the shipping world at the default configuration invokes the verb with nothing at all."""
    assert SHIPPING.flags(CONFIG) == []
    assert PINNED.flags("ss03+ss05") == ["--features=ss03,ss05", "--candidacy-prospect", "--vote-slots-off"]
    assert World(True, False).flags(CONFIG) == ["--vote-slots-off"]
    assert World(False, True).flags(CONFIG) == ["--candidacy-prospect"]
    assert [world.label for world in kernel_liveness.WORLDS] == ["sp1vs1", "sp0vs1", "sp1vs0", "sp0vs0"]
    assert [world.deep for world in kernel_liveness.WORLDS] == [True, True, True, False]


def test_a_kernel_that_echoes_pythons_answers_diverges_nowhere(tmp_path, capsys):
    """The plumbing under the comparison, proved without the verb existing: keys written, a binary invoked with this world's flags, its stdout captured beside Python's own answers, and the two compared per arm."""
    status = kernel_liveness.main(
        [
            "--specs",
            "mini",
            "--count",
            str(QUADS),
            "--out",
            str(tmp_path),
            "--binary",
            str(_stub_kernel(tmp_path, ECHO_STUB)),
        ]
    )
    printed = capsys.readouterr().out
    assert status == 0
    assert "answers identical" in printed
    assert printed.count("OK") == len(kernel_liveness.WORLDS)
    assert (tmp_path / f"kernel-mini-{CONFIG}-{SHIPPING.label}.txt").read_bytes() == (
        tmp_path / f"python-mini-{CONFIG}-{SHIPPING.label}.txt"
    ).read_bytes()


def test_one_flipped_verdict_is_reported_as_one_divergence(tmp_path, capsys):
    status = kernel_liveness.main(
        [
            "--specs",
            "mini",
            "--count",
            str(QUADS),
            "--out",
            str(tmp_path),
            "--binary",
            str(_stub_kernel(tmp_path, MANGLE_STUB)),
        ]
    )
    printed = capsys.readouterr().out
    assert status == 1
    assert f"{len(kernel_liveness.WORLDS)} divergences" in printed
    assert "triple line 1 diverged" in printed


def test_a_kernel_without_the_verb_fails_cleanly_rather_than_diffing_against_nothing(tmp_path, capsys):
    """The harness lands before the verb does, so the target has to be wired and testable meanwhile: exit 2 from the usage check is the verb being absent, and it reads as one line rather than as every answer diverging."""
    status = kernel_liveness.main(
        [
            "--specs",
            "mini",
            "--count",
            str(QUADS),
            "--out",
            str(tmp_path),
            "--binary",
            str(_stub_kernel(tmp_path, "exit 2")),
        ]
    )
    assert status == 1
    assert "kernel does not support liveness-cases yet" in capsys.readouterr().err


def test_a_binary_that_is_not_there_is_said_so_before_any_sweep_runs(tmp_path, capsys):
    status = kernel_liveness.main(
        ["--specs", "mini", "--out", str(tmp_path), "--binary", str(tmp_path / "absent")]
    )
    assert status == 1
    assert "run `make kernel-build` first" in capsys.readouterr().err


def test_asking_no_kernel_and_naming_one_is_refused(tmp_path):
    """Two ways of saying which binary to use, one of them `none`: silently honouring whichever came last would let a cross-build run think it had compared a build it never invoked."""
    with pytest.raises(SystemExit) as refused:
        kernel_liveness.main(
            ["--specs", "mini", "--python-only", "--out", str(tmp_path), "--binary", str(tmp_path / "k")]
        )
    assert refused.value.code == 2


# --- the floors ---------------------------------------------------------------


def test_a_sweep_that_compared_nothing_is_not_a_pass(tmp_path, monkeypatch, capsys):
    """An empty arm is the failure mode a green tally hides best: every world answers, every answer agrees, and the surface the harness exists to compare was never reached. Both floors are checked without a binary in play, because a Python-only run that swept nothing is exactly as worthless as a compared one."""
    empty = dataclasses.replace(SPEC, runes={})
    monkeypatch.setattr(kernel_liveness.fixtures, "mini_spec", lambda: empty)
    status = kernel_liveness.main(["--specs", "mini", "--python-only", "--out", str(tmp_path)])
    captured = capsys.readouterr()
    assert status == 1
    assert "the third-slot arm asked nothing" in captured.err
    assert "no deep world found a live context" in captured.err
    assert "which is not a pass" in captured.out


def test_a_spec_whose_deep_worlds_find_no_live_context_fails_on_the_fibre_floor(
    tmp_path, monkeypatch, capsys
):
    """The reachable half of the floor: an alphabet with keys to ask but no live context anywhere leaves the class-grain partition uncompared, and the fibre arm has nothing to say about a port that got it wrong."""
    lone = dataclasses.replace(SPEC, runes={"qsPea": SPEC.runes["qsPea"]})
    monkeypatch.setattr(kernel_liveness.fixtures, "mini_spec", lambda: lone)
    status = kernel_liveness.main(["--specs", "mini", "--python-only", "--out", str(tmp_path)])
    captured = capsys.readouterr()
    assert status == 1
    assert "the third-slot arm asked nothing" not in captured.err
    assert "no deep world found a live context" in captured.err


def test_the_pinned_world_is_allowed_its_empty_fibre_arm(shipping):
    """The floor is per spec and not per world, because the world with both flags off has no fibre source at all — refusing its zero would refuse the one world whose zero is the correct answer."""
    assert _sweep(PINNED).counts["fibre"] == 0
    assert shipping.counts["fibre"]


# --- the fixpoint harness's world reflection ----------------------------------


@pytest.mark.parametrize(
    "world,expected",
    [
        ((True, True, True), []),
        ((False, True, True), ["--candidacy-prospect"]),
        ((True, False, True), ["--vote-slots-off"]),
        ((True, True, False), ["--deep-classes-off"]),
        ((False, False, True), ["--candidacy-prospect", "--vote-slots-off"]),
        ((False, False, False), ["--candidacy-prospect", "--vote-slots-off", "--deep-classes-off"]),
    ],
)
def test_the_fixpoint_tells_the_kernel_which_world_python_is_in(monkeypatch, world, expected):
    """Each exit-bar arm is one environment away from the last, and the harness's whole job at the boundary is to say so: a flag per default that is off, in the CLI's own spelling, and nothing at all for the shipping world."""
    simulated_prospect, vote_slots, deep_classes = world
    monkeypatch.setattr(settle, "SIMULATED_PROSPECT_DEFAULT", simulated_prospect)
    monkeypatch.setattr(settle, "VOTE_SLOTS_DEFAULT", vote_slots)
    monkeypatch.setattr(table, "DEEP_CLASSES_DEFAULT", deep_classes)
    assert kernel_fixpoint.world_flags() == expected
    assert kernel_fixpoint.enumerate_flags(CONFIG) == expected
    assert kernel_fixpoint.enumerate_flags("ss03+ss05") == ["--features=ss03,ss05", *expected]
    named = " ".join(expected) if expected else "shipping defaults"
    grain = "class grain" if deep_classes and (simulated_prospect or vote_slots) else "label grain"
    assert kernel_fixpoint.world_label() == f"{named}, {grain}"


def test_the_pinned_world_is_named_label_grain_however_the_class_flag_is_set(monkeypatch):
    """The coincidence a flag list cannot state: with both semantics flags off there is no `_ProspectLiveness` instance and hence no fibre source, so enumeration is label-grain whether or not `AMS_DEEP_CLASSES` was ever touched. `make kernel-fixpoint-pinned` is therefore a label-grain run too, and the header says so rather than leaving a reader to infer it from `table.py`."""
    monkeypatch.setattr(settle, "SIMULATED_PROSPECT_DEFAULT", False)
    monkeypatch.setattr(settle, "VOTE_SLOTS_DEFAULT", False)
    monkeypatch.setattr(table, "DEEP_CLASSES_DEFAULT", True)
    assert kernel_fixpoint.world_flags() == ["--candidacy-prospect", "--vote-slots-off"]
    assert kernel_fixpoint.world_label().endswith("label grain")
