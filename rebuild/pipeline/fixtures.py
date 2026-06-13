"""A hand-built mini ResolvedSpec over the real M1 rune data (the moral successor of prototype/spec.py, per M1-PLAN section 5's parallelization note).

Bitmaps, anchors, bindings, pairings, and the policy records below are transcribed from glyph_data/runes/*.yaml so Group 2/3 tests run against the four families' real geometry without depending on Group 1's spec_load. Once spec_load lands, integration swaps this module for the real loader; divergence between the two is a Phase 5 finding, not a license to edit either side silently.
"""

from __future__ import annotations

from rebuild.pipeline.model import (
    Bitmap,
    BoundaryToken,
    CellBinding,
    Condition,
    FamilyInfo,
    FeatureInfo,
    Pairing,
    Pairings,
    Policy,
    PolicyRecord,
    Provenance,
    ResolvedSpec,
    Rune,
    ScriptRegistry,
    Stance,
    Stub,
    Surface,
    SurfaceRow,
    Unlock,
    When,
)

PIXEL = 50
INK_X_OFFSET = 1


def _prov(file: str, path: str) -> Provenance:
    return Provenance(file=file, path=path)


_IT_FILE = "glyph_data/runes/qsIt.yaml"
_TEA_FILE = "glyph_data/runes/qsTea.yaml"
_PEA_FILE = "glyph_data/runes/qsPea.yaml"
_MAY_FILE = "glyph_data/runes/qsMay.yaml"
_OY_FILE = "glyph_data/runes/qsOy.yaml"
_TEA_OY_FILE = "glyph_data/runes/qsTea_qsOy.yaml"

_IT_BAR = Bitmap(("#",) * 6)
_TEA_BAR = Bitmap(("#",) * 9)
_TEA_HALF = Bitmap(("#", "#", "#", "#", " ", " ", " ", " ", " "))
_PEA_FULL = Bitmap((" ## ", "#  #", "#  #", "   #", "   #", "   #", "   #", "   #", "   #"))
_PEA_HALF = Bitmap((" ## ", "#  #", "#  #", "    ", "    ", "    ", "    ", "    ", "    "))
_PEA_HALF_BOTH_DIPS = Bitmap((" ## ", "#  #", "#  #", "#  #", "    ", "    ", "    ", "    ", "    "))
_MAY_LOOP = Bitmap(
    ("   ##", "  #  ", "  #  ", " #   ", " #   ", "#### ", " #  #", " #  #", "  ## "), y_offset=-3
)
_MAY_PULLED_BACK = Bitmap(
    ("   # ", "  #  ", "  #  ", " #   ", " #   ", "#### ", " #  #", " #  #", "  ## "), y_offset=-3
)
_MAY_PULLED_BACK_STUBLESS = Bitmap(
    ("  # ", " #  ", " #  ", "#   ", "#   ", "### ", "#  #", "#  #", " ## "), y_offset=-3
)
_MAY_GROUNDED = Bitmap(("  ##", " #  ", " #  ", "#   ", "#   ", "# ##", "#  #", "#  #", " ## "), y_offset=-3)
_MAY_PULLED_BACK_GROUNDED = Bitmap(
    ("  # ", " #  ", " #  ", "#   ", "#   ", "# ##", "#  #", "#  #", " ## "), y_offset=-3
)
_OY_LOOP = Bitmap((" ###    ", "#  ##   ", "#  # #  ", " ##   # ", "       #", "       #"))
_OY_OPEN_LEFT = Bitmap(("##   ", "  #  ", " # # ", "  # #", "    #", "    #"))
_TEA_OY = Bitmap(
    (
        "   #    ",
        "   #    ",
        "   #    ",
        " ####   ",
        "#  # #  ",
        "#  #  # ",
        " ##    #",
        "       #",
        "       #",
    )
)

_HALVES = Condition(klass=("halves-that-exit-at-x-height",))


def _it() -> Rune:
    surface = Surface(
        entries={
            "baseline": SurfaceRow("baseline", x=0, stroke="vertical"),
            "x-height": SurfaceRow("x-height", x=0, stroke="vertical"),
        },
        exits={
            "baseline": SurfaceRow("baseline", x=1, stroke="vertical", withdrawal="safe"),
            "x-height": SurfaceRow("x-height", x=1, stroke="vertical", withdrawal="safe"),
        },
        pairings=Pairings(
            only=(
                Pairing("x-height", "baseline"),
                Pairing("x-height", "none"),
                Pairing("baseline", "x-height"),
                Pairing("baseline", "none"),
                Pairing("none", "x-height"),
                Pairing("none", "baseline"),
                Pairing("none", "none"),
            )
        ),
        unlocks=(
            Unlock(
                feature="ss04",
                pairing=Pairing("baseline", "baseline"),
                when=When(
                    left=Condition(family=("qsDay",)),
                    right=Condition(except_=(Condition(family=("qsDay",)),)),
                ),
                provenance=_prov(_IT_FILE, "stances.bar.surface.unlocks[0]"),
            ),
        ),
    )
    policy = Policy(
        order=("bar",),
        refuse=(
            PolicyRecord(
                kind="refuse",
                entry="x-height",
                when=When(left=Condition(family=("qsIt",))),
                provenance=_prov(_IT_FILE, "policy.refuse[0]"),
            ),
            PolicyRecord(
                kind="refuse",
                stance="bar",
                exit="x-height",
                when=When(right=Condition(family=("qsDay",))),
                provenance=_prov(_IT_FILE, "policy.refuse[1]"),
            ),
            PolicyRecord(
                kind="refuse",
                stance="bar",
                exit="baseline",
                when=When(self_entry="none", right=Condition(family=("qsTea", "qsRoe", "qsIt"))),
                why="Two adjacent verticals joined at the baseline render as one extra-thick stroke.",
                provenance=_prov(_IT_FILE, "policy.refuse[5]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                stance="bar",
                entry="x-height",
                by=1,
                when=When(
                    left=Condition(
                        klass=("halves-that-exit-at-x-height",),
                        except_=(Condition(family=("qsPea",)),),
                        joined_at="x-height",
                    )
                ),
                provenance=_prov(_IT_FILE, "policy.extend[0]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="bar",
                exit="baseline",
                by=1,
                when=When(self_entry="live"),
                provenance=_prov(_IT_FILE, "policy.extend[2]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="bar",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsZoo", "qsJai", "qsCheer", "qsOwe"))),
                provenance=_prov(_IT_FILE, "policy.extend[5]"),
            ),
        ),
        groups={
            "utter-pass-through-vetoes": frozenset({"qsDay", "qsZoo", "qsShe", "qsYe", "qsOwe"}),
        },
    )
    return Rune(
        name="qsIt",
        codepoint=0xE670,
        ductus={"bar": "- Either written from top to bottom or bottom to top."},
        stances={"bar": Stance("bar", way="bar", bitmap=_IT_BAR, surface=surface)},
        policy=policy,
    )


def _tea() -> Rune:
    full = Stance(
        "full",
        way="full",
        bitmap=_TEA_BAR,
        surface=Surface(
            entries={
                "baseline": SurfaceRow("baseline", x=0, stroke="vertical"),
                "top": SurfaceRow("top", x=0, stroke="vertical"),
            },
            exits={"baseline": SurfaceRow("baseline", x=1, stroke="vertical", withdrawal="safe")},
            pairings=Pairings(never=(Pairing("baseline", "baseline"),)),
            unlocks=(
                Unlock(
                    feature="ss05",
                    pairing=Pairing("baseline", "baseline"),
                    when=When(left=Condition(family=("qsEt",))),
                    provenance=_prov(_TEA_FILE, "stances.full.surface.unlocks[0]"),
                ),
                Unlock(
                    feature="ss02",
                    entry="x-height",
                    when=When(left=Condition(family=("qsI",))),
                    provenance=_prov(_TEA_FILE, "stances.full.surface.unlocks[1]"),
                ),
            ),
        ),
    )
    half = Stance(
        "half",
        way="half",
        traits=("half",),
        bitmap=_TEA_HALF,
        surface=Surface(
            entries={
                "x-height": SurfaceRow("x-height", x=0, stroke="vertical", scope=(_HALVES,)),
                "top": SurfaceRow("top", x=0, selectable=False),
            },
            exits={"x-height": SurfaceRow("x-height", x=1, stroke="vertical", withdrawal="safe")},
            pairings=Pairings(never=(Pairing("x-height", "x-height"),)),
            unlocks=(
                Unlock(
                    feature="ss03",
                    entry="x-height",
                    when=When(
                        left=Condition(
                            family=("qsMay", "qsLow", "qsI", "qsAh", "qsUtter", "qsOut", "qsOwe", "qsFoot"),
                            joined_at="x-height",
                        )
                    ),
                    provenance=_prov(_TEA_FILE, "stances.half.surface.unlocks[0]"),
                ),
            ),
        ),
    )
    policy = Policy(
        order=("full", "half"),
        refuse=(
            # Load-bearing inside the M1 alphabet (Tea·Tea, Pea·Tea, and the entered-It·Tea windows): full ·Tea never enters at the baseline after these predecessors.
            PolicyRecord(
                kind="refuse",
                stance="full",
                entry="baseline",
                when=When(
                    left=Condition(family=("qsPea", "qsTea", "qsYe", "qsHe", "qsExam", "qsIt", "qsEat"))
                ),
                provenance=_prov(_TEA_FILE, "policy.refuse[0]"),
            ),
            PolicyRecord(
                kind="refuse",
                stance="full",
                exit="baseline",
                when=When(right=Condition(family=("qsThaw", "qsExcite", "qsExam", "qsIt"))),
                provenance=_prov(_TEA_FILE, "policy.refuse[2]"),
            ),
            PolicyRecord(
                kind="refuse",
                stance="half",
                entry="x-height",
                when=When(right=Condition(family=("qsTea",))),
                provenance=_prov(_TEA_FILE, "policy.refuse[3]"),
            ),
            PolicyRecord(
                kind="refuse",
                stance="half",
                exit="x-height",
                when=When(right=Condition(family=("qsTea", "qsFee", "qsCheer", "qsYe", "qsOwe", "qsFoot"))),
                provenance=_prov(_TEA_FILE, "policy.refuse[5]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                stance="half",
                entry="x-height",
                by=1,
                when=When(left=Condition(klass=("halves-that-exit-at-x-height",), joined_at="x-height")),
                provenance=_prov(_TEA_FILE, "policy.extend[2]"),
            ),
        ),
        contract=(
            PolicyRecord(
                kind="contract",
                stance="half",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsZoo",))),
                provenance=_prov(_TEA_FILE, "policy.contract[0]"),
            ),
        ),
    )
    return Rune(
        name="qsTea",
        codepoint=0xE652,
        ductus={"full": "draft", "half": "draft"},
        stances={"full": full, "half": half},
        policy=policy,
    )


def _pea() -> Rune:
    full = Stance(
        "full",
        way="full",
        bitmap=_PEA_FULL,
        surface=Surface(
            entries={
                "y6": SurfaceRow("y6", x=0, stroke="vertical"),
                "x-height": SurfaceRow(
                    "x-height",
                    x=0,
                    stroke="vertical",
                    stub=Stub(cols=(0,), when="withdrawn"),
                    scope=(
                        Condition(family=("qsMay",), joined_at="x-height"),
                        Condition(family=("qsUtter",), joined_at="x-height"),
                    ),
                ),
                "baseline": SurfaceRow(
                    "baseline",
                    x=3,
                    stroke="vertical",
                    scope=(Condition(family=("qsEt",)), Condition(family=("qsAwe",))),
                ),
            },
            exits={"baseline": SurfaceRow("baseline", x=4, stroke="vertical", withdrawal="safe")},
            pairings=Pairings(never=(Pairing("baseline", "baseline"),)),
        ),
    )
    half = Stance(
        "half",
        way="half",
        traits=("half",),
        bitmap=_PEA_HALF,
        bitmaps={"half-dips-both-sides": _PEA_HALF_BOTH_DIPS},
        surface=Surface(
            entries={
                "y6": SurfaceRow("y6", x=0, stroke="vertical"),
                "x-height": SurfaceRow(
                    "x-height",
                    x=0,
                    stroke="vertical",
                    stub=Stub(cols=(0,), when="withdrawn"),
                    scope=(
                        Condition(family=("qsMay",), joined_at="x-height"),
                        Condition(family=("qsUtter",), joined_at="x-height"),
                    ),
                ),
            },
            exits={
                "y6": SurfaceRow(
                    "y6", x=4, stroke="vertical", withdrawal="safe", scope=(Condition(family=("qsPea",)),)
                ),
                "x-height": SurfaceRow(
                    "x-height",
                    x=4,
                    ink_y=6,
                    stroke="vertical",
                    withdrawal="safe",
                    stub=Stub(cols=(3,), when="withdrawn"),
                    scope=(
                        Condition(
                            klass=("can-enter-at-x-height",),
                            except_=(Condition(family=("qsTea", "qsDay", "qsFee", "qsYe", "qsOwe")),),
                        ),
                    ),
                ),
            },
            pairings=Pairings(never=(Pairing("x-height", "y6"),)),
            cells=(
                CellBinding(
                    entry="x-height",
                    exit="x-height",
                    bitmap="half-dips-both-sides",
                    provenance=_prov(_PEA_FILE, "stances.half.surface.cells[0]"),
                ),
            ),
        ),
    )
    policy = Policy(
        order=("full", "half"),
        refuse=(
            PolicyRecord(
                kind="refuse",
                stance="full",
                exit="baseline",
                when=When(
                    right=Condition(
                        family=(
                            "qsZoo",
                            "qsCheer",
                            "qsJay",
                            "qsNo",
                            "qsRoe",
                            "qsLlan",
                            "qsIt",
                            "qsEt",
                            "qsEight",
                            "qsAwe",
                            "qsOx",
                            "qsFoot",
                        )
                    )
                ),
                why="These join ·Pea through the half way's x-height dip instead.",
                provenance=_prov(_PEA_FILE, "policy.refuse[0]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                stance="full",
                entry="x-height",
                by=1,
                when=When(left=Condition(klass=("halves-that-exit-at-x-height",), joined_at="x-height")),
                provenance=_prov(_PEA_FILE, "policy.extend[0]"),
            ),
        ),
    )
    return Rune(
        name="qsPea",
        codepoint=0xE650,
        ductus={"full": "draft", "half": "draft"},
        stances={"full": full, "half": half},
        policy=policy,
    )


def _may() -> Rune:
    loop = Stance(
        "loop",
        way="loop",
        bitmap=_MAY_LOOP,
        bitmaps={"pulled-back": _MAY_PULLED_BACK, "pulled-back-stubless": _MAY_PULLED_BACK_STUBLESS},
        surface=Surface(
            entries={
                "baseline": SurfaceRow("baseline", x=0, stroke="horizontal"),
                "x-height": SurfaceRow(
                    "x-height",
                    x=3,
                    stroke="horizontal",
                    joined="pulled-back",
                    scope=(
                        Condition(family=("qsI",)),
                        Condition(family=("qsAh",)),
                        Condition(family=("qsUtter",)),
                    ),
                ),
            },
            exits={"x-height": SurfaceRow("x-height", x=5, stroke="horizontal", withdrawal="pulled-back")},
            pairings=Pairings(never=(Pairing("baseline", "baseline"), Pairing("x-height", "x-height"))),
            cells=(
                CellBinding(
                    entry="x-height",
                    exit="x-height-withdrawn",
                    bitmap="pulled-back",
                    provenance=_prov(_MAY_FILE, "stances.loop.surface.cells[0]"),
                ),
            ),
        ),
    )
    grounded = Stance(
        "grounded-loop",
        way="grounded-loop",
        bitmap=_MAY_GROUNDED,
        bitmaps={"pulled-back-grounded": _MAY_PULLED_BACK_GROUNDED},
        surface=Surface(
            entries={
                "x-height": SurfaceRow(
                    "x-height",
                    x=3,
                    stroke="horizontal",
                    joined="pulled-back-grounded",
                    joined_x=2,
                    scope=(
                        Condition(family=("qsI",)),
                        Condition(family=("qsAh",)),
                        Condition(family=("qsUtter",)),
                    ),
                ),
            },
            exits={"baseline": SurfaceRow("baseline", x=4, stroke="horizontal")},
        ),
    )
    policy = Policy(
        order=("loop", "grounded-loop"),
        refuse=(
            PolicyRecord(
                kind="refuse",
                stance="grounded-loop",
                exit="baseline",
                when=When(
                    right=Condition(
                        family=(
                            "qsTea",
                            "qsDay",
                            "qsZoo",
                            "qsHe",
                            "qsNo",
                            "qsRoe",
                            "qsIt",
                            "qsEat",
                            "qsUtter",
                            "qsOoze",
                        )
                    )
                ),
                why="These never receive ·May's grounded baseline exit.",
                provenance=_prov(_MAY_FILE, "policy.refuse[0]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                stance="loop",
                exit="x-height",
                by=1,
                ok=(1, 1),
                when=When(right=Condition(family=("qsDay", "qsFee", "qsJai", "qsJay", "qsRoe", "qsIt"))),
                provenance=_prov(_MAY_FILE, "policy.extend[0]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="loop",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsTea",)), feature="ss03"),
                provenance=_prov(_MAY_FILE, "policy.extend[1]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="loop",
                exit="x-height",
                by=1,
                when=When(self_entry="live"),
                provenance=_prov(_MAY_FILE, "policy.extend[2]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="loop",
                entry="baseline",
                by=1,
                when=When(
                    left=Condition(
                        family=("qsPea", "qsTea", "qsTea_qsOy", "qsYe", "qsHe", "qsIt"),
                        joined_at="baseline",
                    )
                ),
                provenance=_prov(_MAY_FILE, "policy.extend[3]"),
            ),
        ),
        contract=(
            PolicyRecord(
                kind="contract",
                stance="loop",
                entry="x-height",
                bind="pulled-back-stubless",
                when=When(left=Condition(family=("qsFee",), joined_at="x-height")),
                why="·Fee's long reach-over absorbs the baseline stub.",
                provenance=_prov(_MAY_FILE, "policy.contract[0]"),
            ),
        ),
    )
    return Rune(
        name="qsMay",
        codepoint=0xE665,
        ductus={"loop": "draft", "grounded-loop": "draft"},
        stances={"loop": loop, "grounded-loop": grounded},
        policy=policy,
    )


def _oy() -> Rune:
    loop = Stance(
        "loop",
        way="loop",
        bitmap=_OY_LOOP,
        bitmaps={"open-on-the-left": _OY_OPEN_LEFT},
        surface=Surface(
            entries={
                "x-height": SurfaceRow(
                    "x-height",
                    x=0,
                    stroke="horizontal",
                    joined="open-on-the-left",
                    scope=(Condition(family=("qsMay",), joined_at="x-height"),),
                ),
            },
            exits={"baseline": SurfaceRow("baseline", x=8, stroke="vertical", withdrawal="safe")},
            cells=(
                CellBinding(
                    entry="x-height",
                    exit="baseline",
                    bitmap="open-on-the-left",
                    exit_x=5,
                    provenance=_prov(_OY_FILE, "stances.loop.surface.cells[0]"),
                ),
            ),
        ),
    )
    return Rune(
        name="qsOy",
        codepoint=0xE679,
        ductus={"loop": "draft"},
        stances={"loop": loop},
        policy=Policy(order=("loop",)),
    )


def _tea_oy() -> Rune:
    stance = Stance(
        "bar-into-loop",
        way="bar-into-loop",
        bitmap=_TEA_OY,
        surface=Surface(
            entries={},
            exits={"baseline": SurfaceRow("baseline", x=8, stroke="vertical", withdrawal="safe")},
        ),
    )
    return Rune(
        name="qsTea_qsOy",
        sequence=("qsTea", "qsOy"),
        ductus={"bar-into-loop": "draft"},
        stances={"bar-into-loop": stance},
        policy=Policy(order=("bar-into-loop",)),
    )


def _registry() -> ScriptRegistry:
    return ScriptRegistry(
        heights={"baseline": 0, "x-height": 5, "y6": 6, "top": 8},
        boundary_tokens={
            "space": BoundaryToken(0x0020, splits_runs=True),
            "zwnj": BoundaryToken(0x200C, splits_runs=True),
            "namer-dot": BoundaryToken(0x00B7, splits_runs=False),
        },
        features={
            "ss02": FeatureInfo("capability", "·Tea x-height entry after ·I"),
            "ss03": FeatureInfo("capability", "x-height exiters reach half-·Tea"),
            "ss04": FeatureInfo("capability", "·It same-height baseline pass-through"),
            "ss05": FeatureInfo("capability", "·Tea both-baseline after ·Et"),
            "ss10": FeatureInfo("taste", "isolated forms overlay", overlay="isolated"),
        },
        interactions=(("ss02", "ss03"), ("ss02", "ss03", "ss05")),
        predicate_classes={
            "halves-that-exit-at-x-height": frozenset({"qsPea", "qsTea"}),
            "can-enter-at-baseline": frozenset({"qsPea", "qsTea", "qsMay", "qsIt"}),
            "can-enter-at-x-height": frozenset({"qsPea", "qsTea", "qsMay", "qsIt", "qsOy"}),
            "can-exit-at-baseline": frozenset({"qsPea", "qsTea", "qsMay", "qsIt", "qsOy", "qsTea_qsOy"}),
            "can-exit-at-x-height": frozenset({"qsPea", "qsTea", "qsMay", "qsIt"}),
            "talls": frozenset({"qsPea", "qsTea"}),
            "shorts": frozenset({"qsIt", "qsOy"}),
            "deeps": frozenset({"qsMay"}),
        },
        families={
            "qsPea": FamilyInfo(codepoint=0xE650),
            "qsTea": FamilyInfo(codepoint=0xE652),
            "qsTea_qsOy": FamilyInfo(sequence=("qsTea", "qsOy")),
            "qsMay": FamilyInfo(codepoint=0xE665),
            "qsIt": FamilyInfo(codepoint=0xE670),
            "qsOy": FamilyInfo(codepoint=0xE679),
            # Unmodeled families named by M1 conditions, registered so scopes validate.
            "qsBay": FamilyInfo(codepoint=0xE651),
            "qsDay": FamilyInfo(codepoint=0xE653),
            "qsKey": FamilyInfo(codepoint=0xE654),
            "qsGay": FamilyInfo(codepoint=0xE655),
            "qsThaw": FamilyInfo(codepoint=0xE656),
            "qsFee": FamilyInfo(codepoint=0xE658),
            "qsZoo": FamilyInfo(codepoint=0xE65B),
            "qsShe": FamilyInfo(codepoint=0xE65C),
            "qsJai": FamilyInfo(codepoint=0xE65D),
            "qsCheer": FamilyInfo(codepoint=0xE65E),
            "qsJay": FamilyInfo(codepoint=0xE65F),
            "qsYe": FamilyInfo(codepoint=0xE660),
            "qsHe": FamilyInfo(codepoint=0xE662),
            "qsNo": FamilyInfo(codepoint=0xE666),
            "qsLow": FamilyInfo(codepoint=0xE667),
            "qsRoe": FamilyInfo(codepoint=0xE668),
            "qsLlan": FamilyInfo(codepoint=0xE66A),
            "qsExcite": FamilyInfo(codepoint=0xE66B),
            "qsExam": FamilyInfo(codepoint=0xE66C),
            "qsEat": FamilyInfo(codepoint=0xE671),
            "qsEt": FamilyInfo(codepoint=0xE672),
            "qsEight": FamilyInfo(codepoint=0xE673),
            "qsI": FamilyInfo(codepoint=0xE675),
            "qsAh": FamilyInfo(codepoint=0xE676),
            "qsAwe": FamilyInfo(codepoint=0xE677),
            "qsOx": FamilyInfo(codepoint=0xE678),
            "qsUtter": FamilyInfo(codepoint=0xE67A),
            "qsOut": FamilyInfo(codepoint=0xE67B),
            "qsOwe": FamilyInfo(codepoint=0xE67C),
            "qsFoot": FamilyInfo(codepoint=0xE67D),
            "qsOoze": FamilyInfo(codepoint=0xE67E),
        },
    )


def mini_spec() -> ResolvedSpec:
    runes = {rune.name: rune for rune in (_pea(), _tea(), _tea_oy(), _may(), _it(), _oy())}
    return ResolvedSpec(runes=runes, registry=_registry())
