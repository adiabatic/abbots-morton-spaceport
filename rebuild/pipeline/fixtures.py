"""A hand-built mini ResolvedSpec over the real M1 rune data (the moral successor of prototype/spec.py, per M1-PLAN section 5's parallelization note).

Bitmaps, anchors, bindings, pairings, and the policy records below are transcribed from glyph_data/runes/*.yaml so Group 2/3 tests run against the fixture families' real geometry without depending on Group 1's spec_load. spec_load is the real loader; divergence between the two is a finding, not a license to edit either side silently.

What every rune here transcribes is the loader's resolved output for this rune set, not the raw YAML — so left-facing family transparency (spec_load's `_expand_ligature_lefts`) is applied throughout, over the mini world's own ligature inventory rather than the live alphabet's. A `when.left` or entry `from:` scope naming qsUtter therefore also names qsDay_qsUtter, in the older records as much as the newer ones. Adding a ligature to this fixture means walking the left-facing conditions of every rune already here.

Two ligature worlds live here. qsTea_qsOy forms unconditionally, so it keeps the plain type-4 formation shape; qsDay/qsUtter/qsLow/qsSee/qsDay_qsUtter carry the section 5.7 late-formation guard's worked example, so the guard's own FEA rows and the raw labels it reshapes have a mini world to assert against instead of the loaded spec. That second world is transcribed with one deliberate omission: every policy record whose `when:` carries a `then:` chain is left out, because a chained record opens a depth-3/4 window and the mini world stays at depth 2 so its table builds stay cheap and rebuild/test_conform.py's deep-slot tests keep the real spec as their deep-window authority. Kept records hold their real YAML indices in `Provenance.path`, which is how a subset announces itself here.

`synthetic_spec` and `prospect_spec` beside the mini world are a different kind of fixture: three and four invented letters carrying no geometry anyone renders, built to isolate one ranking stage each where the real records leave it unexercised. They live here rather than in a test file because the crate keeps twins of both under the same names, and because more than one caller now settles them.
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
_DAY_FILE = "glyph_data/runes/qsDay.yaml"
_UTTER_FILE = "glyph_data/runes/qsUtter.yaml"
_LOW_FILE = "glyph_data/runes/qsLow.yaml"
_SEE_FILE = "glyph_data/runes/qsSee.yaml"
_DAY_UTTER_FILE = "glyph_data/runes/qsDay_qsUtter.yaml"

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

_DAY_FULL = Bitmap(("#   ", "#   ", "#   ", "#   ", "#   ", "#  #", "# # ", "##  ", "#   "), y_offset=-3)
_DAY_HALF = Bitmap(("    ", "    ", "    ", "    ", "    ", "#  #", "# # ", "##  ", "#   "), y_offset=-3)
_UTTER_MONO = Bitmap(("   ##", "  #  ", " #   ", " #   ", "#    ", "#    "))
_UTTER_ALTERNATE = Bitmap(("  ## ", " #   ", "#    ", "#    ", " #   ", "  ###"))
_UTTER_REACHES_WAY_BACK = Bitmap(("##### ", "  #   ", " #    ", " #    ", "  #   ", "   ###"))
_UTTER_REACHES_WAY_BACK_WITHDRAWN = Bitmap(("##### ", "  #   ", " #    ", " #    ", "  #   ", "   ## "))
_LOW_HAPAX = Bitmap(("    #", "    #", "   # ", " ##  ", "   # ", "###  "))
_SEE_NORMAL = Bitmap(("   ##", "  #  ", " #   ", " #   ", "  #  ", "  #  ", "   # ", "   # ", "###  "))
_SEE_CURLED_OVER = Bitmap(
    ("   ## ", "  #  #", " #   #", " #    ", "  #   ", "  #   ", "   #  ", "   #  ", "###   ")
)
_SEE_STRAIGHTER = Bitmap(("  #", " # ", "#  ", "#  ", " # ", " # ", "  #", "  #", " # "))
_SEE_STRAIGHTEST = Bitmap(("  #", " # ", "#  ", "#  ", " # ", " # ", "  #", "  #", "  #"))
_DAY_UTTER_FULL = Bitmap(
    ("#    ##", "#   #  ", "#  #   ", "#  #   ", "# #    ", "# #    ", "##     ", "##     ", "#      "),
    y_offset=-3,
)
_DAY_UTTER_HALF = Bitmap(
    (
        "     ###",
        "    #   ",
        "   #    ",
        "   #    ",
        "  #     ",
        "# #     ",
        "##      ",
        "##      ",
        "#       ",
    ),
    y_offset=-3,
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
                provenance=_prov(_IT_FILE, "stances.hapax.surface.unlocks[0]"),
            ),
        ),
    )
    policy = Policy(
        order=("hapax",),
        refuse=(
            PolicyRecord(
                kind="refuse",
                entry="x-height",
                when=When(left=Condition(family=("qsIt",))),
                provenance=_prov(_IT_FILE, "policy.refuse[0]"),
            ),
            PolicyRecord(
                kind="refuse",
                stance="hapax",
                exit="x-height",
                when=When(right=Condition(family=("qsDay",))),
                provenance=_prov(_IT_FILE, "policy.refuse[1]"),
            ),
            PolicyRecord(
                kind="refuse",
                stance="hapax",
                exit="baseline",
                when=When(self_entry="none", right=Condition(family=("qsTea", "qsRoe", "qsIt"))),
                why="Two adjacent verticals joined at the baseline render as one extra-thick stroke.",
                provenance=_prov(_IT_FILE, "policy.refuse[5]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                stance="hapax",
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
                stance="hapax",
                exit="baseline",
                by=1,
                when=When(self_entry="live"),
                provenance=_prov(_IT_FILE, "policy.extend[2]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="hapax",
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
        ductus={"hapax": "- Either written from top to bottom or bottom to top."},
        stances={"hapax": Stance("hapax", motion="hapax", bitmap=_IT_BAR, surface=surface)},
        policy=policy,
    )


def _tea() -> Rune:
    full = Stance(
        "full",
        motion="full",
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
        motion="half",
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
                            family=(
                                "qsMay",
                                "qsLow",
                                "qsI",
                                "qsAh",
                                "qsUtter",
                                "qsOut",
                                "qsOwe",
                                "qsFoot",
                                "qsDay_qsUtter",
                            ),
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
        motion="full",
        bitmap=_PEA_FULL,
        surface=Surface(
            entries={
                "y6": SurfaceRow("y6", x=0, stroke="vertical"),
                "x-height": SurfaceRow(
                    "x-height",
                    x=0,
                    stroke="vertical",
                    stub=Stub(cols=(0,), inks_when="joined"),
                    scope=(
                        Condition(family=("qsMay",), joined_at="x-height"),
                        Condition(family=("qsUtter", "qsDay_qsUtter"), joined_at="x-height"),
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
        motion="half",
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
                    stub=Stub(cols=(0,), inks_when="joined"),
                    scope=(
                        Condition(family=("qsMay",), joined_at="x-height"),
                        Condition(family=("qsUtter", "qsDay_qsUtter"), joined_at="x-height"),
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
                    stub=Stub(cols=(3,), inks_when="joined"),
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
                why="These join ·Pea through the half motion's x-height dip instead.",
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
        motion="loop",
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
                        Condition(family=("qsUtter", "qsDay_qsUtter")),
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
        motion="grounded-loop",
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
                        Condition(family=("qsUtter", "qsDay_qsUtter")),
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
        "hapax",
        motion="hapax",
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
                    provenance=_prov(_OY_FILE, "stances.hapax.surface.cells[0]"),
                ),
            ),
        ),
    )
    return Rune(
        name="qsOy",
        codepoint=0xE679,
        ductus={"hapax": "draft"},
        stances={"hapax": loop},
        policy=Policy(order=("hapax",)),
    )


def _tea_oy() -> Rune:
    stance = Stance(
        "hapax",
        motion="hapax",
        bitmap=_TEA_OY,
        surface=Surface(
            entries={},
            exits={"baseline": SurfaceRow("baseline", x=8, stroke="vertical", withdrawal="safe")},
        ),
    )
    return Rune(
        name="qsTea_qsOy",
        sequence=("qsTea", "qsOy"),
        ductus={"hapax": "draft"},
        stances={"hapax": stance},
        policy=Policy(order=("hapax",)),
    )


def _day() -> Rune:
    full = Stance(
        "full",
        motion="full",
        bitmap=_DAY_FULL,
        surface=Surface(
            entries={"x-height": SurfaceRow("x-height", x=0, stroke="vertical")},
            exits={"baseline": SurfaceRow("baseline", x=4, stroke="diagonal")},
        ),
    )
    half = Stance(
        "half",
        motion="half",
        traits=("half",),
        bitmap=_DAY_HALF,
        surface=Surface(
            entries={"baseline": SurfaceRow("baseline", x=0, stroke="diagonal")},
            exits={"baseline": SurfaceRow("baseline", x=4, stroke="diagonal")},
        ),
    )
    policy = Policy(
        order=("full", "half"),
        refuse=(
            PolicyRecord(
                kind="refuse",
                entry="baseline",
                when=When(left=Condition(family=("qsWay",))),
                provenance=_prov(_DAY_FILE, "policy.refuse[0]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                entry="baseline",
                by=1,
                when=When(left=Condition(family=("qsTea", "qsYe"))),
                provenance=_prov(_DAY_FILE, "policy.extend[0]"),
            ),
        ),
    )
    return Rune(
        name="qsDay",
        codepoint=0xE653,
        ductus={"full": "draft", "half": "draft"},
        stances={"full": full, "half": half},
        policy=policy,
    )


def _utter() -> Rune:
    mono = Stance(
        "mono",
        motion="mono",
        bitmap=_UTTER_MONO,
        surface=Surface(
            entries={"baseline": SurfaceRow("baseline", x=0, stroke="vertical")},
            exits={"x-height": SurfaceRow("x-height", x=5, stroke="horizontal")},
        ),
    )
    alternate = Stance(
        "alternate",
        motion="alternate",
        traits=("alt",),
        bitmap=_UTTER_ALTERNATE,
        bitmaps={
            "reaches-way-back": _UTTER_REACHES_WAY_BACK,
            "reaches-way-back-withdrawn": _UTTER_REACHES_WAY_BACK_WITHDRAWN,
        },
        surface=Surface(
            entries={
                "x-height": SurfaceRow(
                    "x-height",
                    x=0,
                    stroke="horizontal",
                    joined="reaches-way-back",
                    scope=(Condition(family=("qsMay", "qsFee", "qsNo", "qsUtter", "qsDay_qsUtter")),),
                ),
            },
            exits={"baseline": SurfaceRow("baseline", x=5, stroke="horizontal")},
            cells=(
                CellBinding(
                    entry="x-height",
                    exit="baseline",
                    bitmap="reaches-way-back",
                    exit_x=6,
                    provenance=_prov(_UTTER_FILE, "stances.alternate.surface.cells[0]"),
                ),
                CellBinding(
                    entry="x-height",
                    exit="baseline-withdrawn",
                    bitmap="reaches-way-back-withdrawn",
                    provenance=_prov(_UTTER_FILE, "stances.alternate.surface.cells[1]"),
                ),
            ),
            require=("exit",),
        ),
    )
    policy = Policy(
        order=("mono", "alternate"),
        refuse=(
            PolicyRecord(
                kind="refuse",
                exit="baseline",
                when=When(right=Condition(family=("qsYe", "qsHe", "qsRoe", "qsUtter"))),
                provenance=_prov(_UTTER_FILE, "policy.refuse[0]"),
            ),
            PolicyRecord(
                kind="refuse",
                exit="baseline",
                when=When(left=Condition(is_token="boundary"), right=Condition(family=("qsNo",))),
                why="“un-”, as a prefix, should not use the alternate ·Utter. It says so in The Manual, on page 21.",
                provenance=_prov(_UTTER_FILE, "policy.refuse[1]"),
            ),
            PolicyRecord(
                kind="refuse",
                exit="x-height",
                when=When(
                    left=Condition(family=("qsJai",), joined_at="none"),
                    right=Condition(family=("qsUtter",)),
                ),
                provenance=_prov(_UTTER_FILE, "policy.refuse[2]"),
            ),
        ),
        prefer=(
            PolicyRecord(
                kind="prefer",
                cell={"exit": "baseline"},
                over={"exit": "x-height"},
                when=When(self_entry="none", right=Condition(family=("qsMay",))),
                why="A bare ·Utter before ·May keeps the alternate baseline join; the mono x-height reach into a pulled-back ·May wins only when it buys a better join, as before ·May·May where the grounded ·May carries the chain on at the baseline.",
                provenance=_prov(_UTTER_FILE, "policy.prefer[1]"),
            ),
            PolicyRecord(
                kind="prefer",
                cell={"exit": "baseline"},
                over={"entry": "baseline"},
                when=When(right=Condition(family=("qsLow",))),
                why="Claude figured that this would be the best (least disruptive) way to have `·Day | ·Utter.alt ~b~ ·Low` happen; The Manual requires this",
                provenance=_prov(_UTTER_FILE, "policy.prefer[2]"),
            ),
            PolicyRecord(
                kind="prefer",
                cell={"entry": "x-height"},
                over={"entry": "baseline"},
                when=When(left=Condition(joined_at="x-height"), right=Condition(family=("qsMay",))),
                why="if `·Utter.alt ~b~ ·May` works, then `·No ~x~ ·Utter.alt ~b~ ·May` should work too; we should have an ·Utter.alt that can connect to the previous letter at the x-height",
                provenance=_prov(_UTTER_FILE, "policy.prefer[5]"),
            ),
            PolicyRecord(
                kind="prefer",
                cell={"entry": "baseline"},
                over={"entry": "x-height"},
                when=When(left=Condition(family=("qsNo",)), right=Condition(family=("qsNo", "qsIt"))),
                why="I would rather have ·Utter.!alt here when the letters around it can connect at either the baseline or x-height and don’t look better or worse one way or the other\n",
                provenance=_prov(_UTTER_FILE, "policy.prefer[6]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsBay", "qsGay", "qsThey", "qsFee"))),
                provenance=_prov(_UTTER_FILE, "policy.extend[0]"),
            ),
            PolicyRecord(
                kind="extend",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsTea",)), feature="ss03"),
                provenance=_prov(_UTTER_FILE, "policy.extend[1]"),
            ),
        ),
    )
    return Rune(
        name="qsUtter",
        codepoint=0xE67A,
        ductus={"mono": "draft", "alternate": "draft"},
        stances={"mono": mono, "alternate": alternate},
        policy=policy,
    )


def _low() -> Rune:
    hapax = Stance(
        "hapax",
        motion="hapax",
        bitmap=_LOW_HAPAX,
        surface=Surface(
            entries={"baseline": SurfaceRow("baseline", x=0, stroke="horizontal")},
            exits={"x-height": SurfaceRow("x-height", x=5, stroke="vertical")},
        ),
    )
    policy = Policy(
        order=("hapax",),
        extend=(
            PolicyRecord(
                kind="extend",
                entry="baseline",
                by=1,
                when=When(left=Condition(family=("qsSee",))),
                provenance=_prov(_LOW_FILE, "policy.extend[0]"),
            ),
            PolicyRecord(
                kind="extend",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsFee",))),
                provenance=_prov(_LOW_FILE, "policy.extend[1]"),
            ),
            PolicyRecord(
                kind="extend",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsTea",)), feature="ss03"),
                provenance=_prov(_LOW_FILE, "policy.extend[2]"),
            ),
        ),
    )
    return Rune(
        name="qsLow",
        codepoint=0xE667,
        ductus={"hapax": "draft"},
        stances={"hapax": hapax},
        policy=policy,
    )


def _see() -> Rune:
    normal = Stance(
        "normal",
        motion="normal",
        bitmap=_SEE_NORMAL,
        surface=Surface(
            entries={"baseline": SurfaceRow("baseline", x=0, stroke="horizontal")},
            exits={"top": SurfaceRow("top", x=5, stroke="horizontal")},
        ),
    )
    curled_over = Stance(
        "curled-over",
        motion="curled-over",
        bitmap=_SEE_CURLED_OVER,
        surface=Surface(
            entries={"baseline": SurfaceRow("baseline", x=0, stroke="horizontal")},
            exits={"y6": SurfaceRow("y6", x=6, stroke="vertical")},
        ),
    )
    straighter = Stance(
        "straighter",
        motion="straighter",
        bitmap=_SEE_STRAIGHTER,
        surface=Surface(
            exits={
                "baseline": SurfaceRow(
                    "baseline",
                    x=2,
                    stroke="diagonal",
                    scope=(Condition(family=("qsLow", "qsAt", "qsOut", "qsOut_qsTea")),),
                ),
            },
        ),
    )
    straightest = Stance(
        "straightest",
        motion="straightest",
        bitmap=_SEE_STRAIGHTEST,
        surface=Surface(
            exits={
                "baseline": SurfaceRow(
                    "baseline", x=3, stroke="vertical", scope=(Condition(family=("qsOoze",)),)
                ),
            },
        ),
    )
    policy = Policy(
        order=("normal", "curled-over", "straighter", "straightest"),
        refuse=(
            PolicyRecord(
                kind="refuse",
                entry="baseline",
                when=When(left=Condition(family=("qsYe",))),
                provenance=_prov(_SEE_FILE, "policy.refuse[0]"),
            ),
        ),
        prefer=(
            PolicyRecord(
                kind="prefer",
                cell={"entry": "none", "exit": "baseline"},
                over={"entry": "baseline", "exit": "none"},
                when=When(
                    left=Condition(klass=("can-exit-at-baseline",), except_=(Condition(family=("qsIt",)),)),
                    right=Condition(family=("qsLow", "qsAt", "qsOut", "qsOut_qsTea", "qsOoze")),
                ),
                provenance=_prov(_SEE_FILE, "policy.prefer[0]"),
            ),
        ),
        extend=(
            PolicyRecord(
                kind="extend",
                stance="straighter",
                exit="baseline",
                by=2,
                when=When(right=Condition(family=("qsLow",))),
                provenance=_prov(_SEE_FILE, "policy.extend[0]"),
            ),
        ),
    )
    return Rune(
        name="qsSee",
        codepoint=0xE65A,
        ductus={
            "normal": "draft",
            "curled-over": "draft",
            "straighter": "draft",
            "straightest": "draft",
        },
        stances={
            "normal": normal,
            "curled-over": curled_over,
            "straighter": straighter,
            "straightest": straightest,
        },
        policy=policy,
    )


def _day_utter() -> Rune:
    full = Stance(
        "full",
        motion="full",
        bitmap=_DAY_UTTER_FULL,
        surface=Surface(
            entries={"x-height": SurfaceRow("x-height", x=0, stroke="vertical")},
            exits={"x-height": SurfaceRow("x-height", x=7, stroke="horizontal")},
        ),
    )
    half = Stance(
        "half",
        motion="half",
        traits=("half",),
        bitmap=_DAY_UTTER_HALF,
        surface=Surface(
            entries={"baseline": SurfaceRow("baseline", x=0, stroke="diagonal")},
            exits={"x-height": SurfaceRow("x-height", x=8, stroke="horizontal")},
        ),
    )
    policy = Policy(
        order=("full", "half"),
        extend=(
            PolicyRecord(
                kind="extend",
                stance="full",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsFee",))),
                provenance=_prov(_DAY_UTTER_FILE, "policy.extend[0]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="full",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsTea",)), feature="ss03"),
                provenance=_prov(_DAY_UTTER_FILE, "policy.extend[1]"),
            ),
            PolicyRecord(
                kind="extend",
                entry="baseline",
                by=1,
                when=When(
                    left=Condition(family=("qsTea", "qsYe")),
                    right=Condition(except_=(Condition(family=("qsFee",)),)),
                ),
                why="I want the shorter ·Tea·Day (lack of) extension in the old way",
                provenance=_prov(_DAY_UTTER_FILE, "policy.extend[2]"),
            ),
            PolicyRecord(
                kind="extend",
                entry="baseline",
                by=1,
                when=When(
                    left=Condition(family=("qsTea", "qsYe")),
                    right=Condition(family=("qsFee",)),
                    self_exit="none",
                ),
                provenance=_prov(_DAY_UTTER_FILE, "policy.extend[3]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="half",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsFee",))),
                provenance=_prov(_DAY_UTTER_FILE, "policy.extend[4]"),
            ),
            PolicyRecord(
                kind="extend",
                stance="half",
                exit="x-height",
                by=1,
                when=When(right=Condition(family=("qsTea",)), feature="ss03"),
                provenance=_prov(_DAY_UTTER_FILE, "policy.extend[5]"),
            ),
        ),
    )
    return Rune(
        name="qsDay_qsUtter",
        sequence=("qsDay", "qsUtter"),
        ductus={"full": "draft", "half": "draft"},
        stances={"full": full, "half": half},
        policy=policy,
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
            "halves-that-exit-at-x-height": frozenset({"qsPea", "qsTea", "qsDay_qsUtter"}),
            "can-enter-at-baseline": frozenset(
                {"qsPea", "qsTea", "qsDay", "qsDay_qsUtter", "qsSee", "qsMay", "qsLow", "qsIt", "qsUtter"}
            ),
            "can-enter-at-x-height": frozenset(
                {"qsPea", "qsTea", "qsDay", "qsDay_qsUtter", "qsMay", "qsIt", "qsOy", "qsUtter"}
            ),
            "can-exit-at-baseline": frozenset(
                {
                    "qsPea",
                    "qsTea",
                    "qsTea_qsOy",
                    "qsDay",
                    "qsSee",
                    "qsMay",
                    "qsIt",
                    "qsOy",
                    "qsUtter",
                }
            ),
            "can-exit-at-x-height": frozenset(
                {"qsPea", "qsTea", "qsDay_qsUtter", "qsMay", "qsLow", "qsIt", "qsUtter"}
            ),
            "talls": frozenset({"qsPea", "qsTea", "qsSee"}),
            "shorts": frozenset({"qsLow", "qsIt", "qsOy", "qsUtter"}),
            "deeps": frozenset({"qsDay", "qsMay"}),
        },
        families={
            "qsPea": FamilyInfo(codepoint=0xE650),
            "qsTea": FamilyInfo(codepoint=0xE652),
            "qsTea_qsOy": FamilyInfo(sequence=("qsTea", "qsOy")),
            "qsDay": FamilyInfo(codepoint=0xE653),
            "qsDay_qsUtter": FamilyInfo(sequence=("qsDay", "qsUtter")),
            "qsSee": FamilyInfo(codepoint=0xE65A),
            "qsMay": FamilyInfo(codepoint=0xE665),
            "qsLow": FamilyInfo(codepoint=0xE667),
            "qsIt": FamilyInfo(codepoint=0xE670),
            "qsOy": FamilyInfo(codepoint=0xE679),
            "qsUtter": FamilyInfo(codepoint=0xE67A),
            # Unmodeled families named by M1 conditions, registered so scopes validate.
            "qsBay": FamilyInfo(codepoint=0xE651),
            "qsKey": FamilyInfo(codepoint=0xE654),
            "qsGay": FamilyInfo(codepoint=0xE655),
            "qsThaw": FamilyInfo(codepoint=0xE656),
            "qsThey": FamilyInfo(codepoint=0xE657),
            "qsFee": FamilyInfo(codepoint=0xE658),
            "qsZoo": FamilyInfo(codepoint=0xE65B),
            "qsShe": FamilyInfo(codepoint=0xE65C),
            "qsJai": FamilyInfo(codepoint=0xE65D),
            "qsCheer": FamilyInfo(codepoint=0xE65E),
            "qsJay": FamilyInfo(codepoint=0xE65F),
            "qsYe": FamilyInfo(codepoint=0xE660),
            "qsWay": FamilyInfo(codepoint=0xE661),
            "qsHe": FamilyInfo(codepoint=0xE662),
            "qsNo": FamilyInfo(codepoint=0xE666),
            "qsRoe": FamilyInfo(codepoint=0xE668),
            "qsLlan": FamilyInfo(codepoint=0xE66A),
            "qsExcite": FamilyInfo(codepoint=0xE66B),
            "qsExam": FamilyInfo(codepoint=0xE66C),
            "qsEat": FamilyInfo(codepoint=0xE671),
            "qsEt": FamilyInfo(codepoint=0xE672),
            "qsEight": FamilyInfo(codepoint=0xE673),
            "qsAt": FamilyInfo(codepoint=0xE674),
            "qsI": FamilyInfo(codepoint=0xE675),
            "qsAh": FamilyInfo(codepoint=0xE676),
            "qsAwe": FamilyInfo(codepoint=0xE677),
            "qsOx": FamilyInfo(codepoint=0xE678),
            "qsOut": FamilyInfo(codepoint=0xE67B),
            "qsOut_qsTea": FamilyInfo(sequence=("qsOut", "qsTea")),
            "qsOwe": FamilyInfo(codepoint=0xE67C),
            "qsFoot": FamilyInfo(codepoint=0xE67D),
            "qsOoze": FamilyInfo(codepoint=0xE67E),
        },
    )


def mini_spec() -> ResolvedSpec:
    runes = {
        rune.name: rune
        for rune in (
            _pea(),
            _tea(),
            _tea_oy(),
            _day(),
            _day_utter(),
            _see(),
            _may(),
            _low(),
            _it(),
            _oy(),
            _utter(),
        )
    }
    return ResolvedSpec(runes=runes, registry=_registry())


def synthetic_spec(prefer_a=(), prefer_b=(), contract_b=()) -> ResolvedSpec:
    """Three letters: A exits at the x-height toward anything; B enters at the x-height (entered B is exitless by pairing) and exits at the baseline only when unentered; C enters at the baseline. The A.B seam therefore ties join-vs-prospect at one window join each — the floor and prefer testbed. The crate states the same testbed in its own four-family vocabulary as `engine.rs`'s `ranking_spec`."""
    a = Rune(
        name="A",
        codepoint=0xE001,
        ductus={"stroke": "synthetic"},
        stances={
            "stroke": Stance(
                "stroke",
                motion="stroke",
                surface=Surface(
                    exits={"x-height": SurfaceRow("x-height", x=1, withdrawal="safe")},
                ),
            ),
            "flourish": Stance("flourish", motion="stroke"),
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
                motion="hook",
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
                motion="base",
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


def prospect_spec() -> ResolvedSpec:
    """Four letters replaying the issue-28 signature (the ·No·No·Tea·Day shape). A exits at both heights and prefers x-height over baseline as a yielding tie-break; B enters at both heights, is exitless when entered at the x-height, and yields its baseline exit before C·D; entered C is exitless, so B joining C forecloses C·D while B declining buys it. The optimistic prospect therefore scores A's baseline candidate as if B's onward join will happen, but B's own cascade provably yields it one seat later — the simulated prospect sees the yield from A's seat. The crate states the same shape in its own vocabulary as `engine.rs`'s `prospect_spec`."""
    a = Rune(
        name="A",
        codepoint=0xE011,
        ductus={"stroke": "synthetic"},
        stances={
            "stroke": Stance(
                "stroke",
                motion="stroke",
                surface=Surface(
                    exits={
                        "x-height": SurfaceRow("x-height", x=1, withdrawal="safe"),
                        "baseline": SurfaceRow("baseline", x=1, withdrawal="safe"),
                    },
                ),
            ),
        },
        policy=Policy(
            order=("stroke",),
            prefer=(
                PolicyRecord(
                    kind="prefer", cell={"exit": "x-height"}, over={"exit": "baseline"}, when=When()
                ),
            ),
        ),
    )
    b = Rune(
        name="B",
        codepoint=0xE012,
        ductus={"hook": "synthetic"},
        stances={
            "hook": Stance(
                "hook",
                motion="hook",
                surface=Surface(
                    entries={
                        "x-height": SurfaceRow("x-height", x=0),
                        "baseline": SurfaceRow("baseline", x=0),
                    },
                    exits={"baseline": SurfaceRow("baseline", x=1, withdrawal="safe")},
                    pairings=Pairings(never=(Pairing("x-height", "baseline"),)),
                ),
            ),
        },
        policy=Policy(
            order=("hook",),
            prefer=(
                PolicyRecord(
                    kind="prefer",
                    cell={"exit": "none"},
                    over={"exit": "baseline"},
                    when=When(right=Condition(family=("C",), then=Condition(family=("D",)))),
                ),
            ),
        ),
    )
    c = Rune(
        name="C",
        codepoint=0xE013,
        ductus={"base": "synthetic"},
        stances={
            "base": Stance(
                "base",
                motion="base",
                surface=Surface(
                    entries={"baseline": SurfaceRow("baseline", x=0)},
                    exits={"baseline": SurfaceRow("baseline", x=1, withdrawal="safe")},
                    pairings=Pairings(never=(Pairing("baseline", "baseline"),)),
                ),
            ),
        },
        policy=Policy(order=("base",)),
    )
    d = Rune(
        name="D",
        codepoint=0xE014,
        ductus={"base": "synthetic"},
        stances={
            "base": Stance(
                "base",
                motion="base",
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
            "A": FamilyInfo(codepoint=0xE011),
            "B": FamilyInfo(codepoint=0xE012),
            "C": FamilyInfo(codepoint=0xE013),
            "D": FamilyInfo(codepoint=0xE014),
        },
    )
    return ResolvedSpec(runes={"A": a, "B": b, "C": c, "D": d}, registry=registry)
