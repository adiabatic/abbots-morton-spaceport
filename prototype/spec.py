"""Hand-encoded subset spec for the week-one de-risking prototype (doc/rebuild-design.md section 7, prototype/PLAN.md section 1).

Three families (qsIt, qsTea, qsMay) plus the qsTea_qsOy ligature, transcribed from today's glyph_data/quikscript.yaml facts with allowlist polarity (section 13.3: every join scope here is a positive from:/toward: list restricted to the subset, so absent-from-allowlist pairs stay unjoined). Every record carries a provenance comment naming its YAML source (line ranges against commit a7fabef, mirrored in prototype/recon/families.md section 1) or the probe that pinned it (prototype/recon/probe_families.py, prototype/probe_supplementary.py).

qsOy is modeled as an inert rune per PLAN.md section 2: bare glyph only, no entry/exit rows, present only as formation input. Today's qsOy joins (qsOy then qsIt/qsTea/qsMay at the baseline, qsMay then qsOy at the x-height — probed in probe_supplementary.py) are deliberately not reproduced; see the Deviations section of PLAN.md.

Two records are SYNTHETIC ENCODING PROBES (PLAN.md deviation 13), not transcriptions of today's font. They exist because the natural subset never produced a rule with a second lookahead slot or a backtrack-classed rule on a never-locked input, which left two of the four ZWNJ slots of the section 7 row shape challenged only vacuously. Probe 1 is the refusal in REFUSALS (a window-decidable right-square refuse per rebuild-design section 3.3/6.1.3); probe 2 is the qsTea_qsOy.after-it settled-pair cell (the section 7 "small settled-pair substitution stage" shape, the section 9 collision-refuse promotion target). Both are flagged with "synthetic encoding probe" provenance so no one mistakes them for today's behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CODEPOINT_TO_TOKEN = {
    0xE652: "qsTea",
    0xE665: "qsMay",
    0xE670: "qsIt",
    0xE679: "qsOy",
    0x0020: "space",
    0x200C: "zwnj",
}
ALPHABET = tuple(chr(cp) for cp in CODEPOINT_TO_TOKEN)

FEATURE_CONFIGURATIONS = (frozenset(), frozenset({"ss03"}))

# quikscript.yaml:708-724 — sequence [qsTea, qsOy], exit (8, 0), no entry (emergent: inheritance from the lead fails, recon/families.md section 3).
FORMATION = (("qsTea", "qsOy", "qsTea_qsOy"),)

# The ss03 capability marker twin (PLAN.md section 4 item 2). Only qsTea carries one: ss03 is the single stylistic set with any effect inside the subset (recon/families.md section 2).
MARKER_FAMILIES = {"qsTea": "qsTea.ss03"}

# Chokepoint live set: the entry-bearing bare runes (plus the marker twin, added in emit.py). qsOy and qsTea_qsOy have no entry and need no twin (PLAN.md section 4 item 3).
ENTRY_BEARING_FAMILIES = ("qsTea", "qsMay", "qsIt")

# When a ZWNJ-locked rune settles its exit side, qsIt and qsMay reuse the plain entryless cell (probed today: "zwnj qsIt qsMay" yields qsIt.ex-y0, "zwnj qsMay qsIt" yields qsMay.ex-ext-1 — probe_supplementary.py), while qsTea mints .noentry cell twins (PLAN.md section 4 mandates qsTea.noentry.half.ex-y5 for "ZWNJ qsTea qsIt").
LOCKED_EXIT_REUSES_PLAIN = {"qsIt": True, "qsTea": False, "qsMay": True}


@dataclass(frozen=True)
class Selector:
    """A positive scope entry: matches a neighbor by family, optionally narrowed to a committed exit height and gated behind a stylistic set."""

    family: str
    exit_y: int | None = None
    feature: str | None = None

    def matches(self, family: str, exit_y: int | None, features: frozenset[str]) -> bool:
        if self.family != family:
            return False
        if self.exit_y is not None and self.exit_y != exit_y:
            return False
        if self.feature is not None and self.feature not in features:
            return False
        return True


@dataclass(frozen=True)
class RefusalRecord:
    """A window-decidable refuse (rebuild-design section 6.1.3): kills the (family, exit_height) candidate toward `toward` when the raw rune two positions to the right is in `when_right2`. Boundary right-square values never match, so the join survives at run edges, before space, and before ZWNJ."""

    family: str
    exit_height: int
    toward: str
    when_right2: tuple[str, ...]
    provenance: str = ""


@dataclass(frozen=True)
class EntryRow:
    height: int
    x: int
    from_scope: tuple[Selector, ...]
    feature: str | None = None
    half: bool = False
    extend_after: tuple[Selector, ...] = ()
    extension_suppressed_when_left_extended: bool = False
    provenance: str = ""


@dataclass(frozen=True)
class ExitRow:
    height: int
    x: int
    toward_scope: tuple[Selector, ...]
    intrinsic: bool = False
    anchor_kept_at_boundary: bool = False
    half: bool = False
    extend_toward: tuple[Selector, ...] = ()
    extend_when_entered: bool = False
    provenance: str = ""


@dataclass(frozen=True)
class FamilySpec:
    name: str
    entries: tuple[EntryRow, ...] = ()
    exits: tuple[ExitRow, ...] = ()
    # Allowed (entry height, exit height) combinations for two-sided cells. Empty means entered cells never carry an exit.
    pairings_only: tuple[tuple[int, int], ...] = ()

    def entry_row(self, height: int) -> EntryRow | None:
        for row in self.entries:
            if row.height == height:
                return row
        return None


FAMILIES: dict[str, FamilySpec] = {
    "qsIt": FamilySpec(
        name="qsIt",
        # Hard pairing rule from the ductus (quikscript.yaml:2727-2731): joined-at-x-height exits at the baseline and vice versa; same-height pass-through is ss04-gated and out of subset scope.
        pairings_only=((5, 0), (0, 5)),
        entries=(
            # quikscript.yaml:2751-2761 entry_xheight_exit_baseline: en (0,5), not_after qsIt. In-subset allowlist: qsTea half ex-y5 and qsMay ex-y5 (probe rows 6, 7). The YAML's extend_entry_after toward halves_exit_xheight_no_pea (line 2759) includes qsTea.half.ex-y5, but the probed Tea-It join carries no en-ext-1 (recon/families.md section 2), so no extend_after is recorded here.
            EntryRow(
                height=5,
                x=0,
                from_scope=(Selector("qsTea", exit_y=5), Selector("qsMay", exit_y=5)),
                provenance="quikscript.yaml:2751-2761",
            ),
            # quikscript.yaml:2762-2773 entry_baseline_exit_xheight: en (0,0). In-subset the only baseline exit that reaches qsIt is the ligature's (Tea/It/May baseline exits all carry not_before qsIt); probed: "qsTea qsOy qsIt" yields qsIt.en-y0.ex-y5 (probe_supplementary.py).
            EntryRow(
                height=0,
                x=0,
                from_scope=(Selector("qsTea_qsOy"),),
                provenance="quikscript.yaml:2762-2773 + probe_supplementary.py",
            ),
        ),
        exits=(
            # quikscript.yaml:2843-2853 entry_nowhere_exit_baseline and 2751-2761: ex (1,0); not_before {qsTea, qsIt, qsRoe}. In-subset toward-allowlist: qsMay only (probe row 4). extend_exit_when_entered by 1 (line 2760) puts the connector pixel on an entered qsIt's exit (probe rows 11-13).
            ExitRow(
                height=0,
                x=1,
                toward_scope=(Selector("qsMay"),),
                anchor_kept_at_boundary=True,
                extend_when_entered=True,
                provenance="quikscript.yaml:2751-2761,2843-2853",
            ),
            # quikscript.yaml:2835-2842 entry_nowhere_exit_xheight: ex (1,5). No in-subset acceptor (qsIt refuses qsIt, qsTea's half entry is not scoped from qsIt, qsMay's en-y5 after-list has nothing in the subset), so this row's toward-scope is empty and divergence 1 (It-It first letter settles bare) falls out of row scope.
            ExitRow(
                height=5,
                x=1,
                toward_scope=(),
                anchor_kept_at_boundary=True,
                provenance="quikscript.yaml:2835-2842",
            ),
        ),
    ),
    "qsTea": FamilySpec(
        name="qsTea",
        # Entered qsTea never carries an exit in-subset: en-y0+ex-y0 is ss05-gated (quikscript.yaml:684-692), the entered half is exitless (half_entry_xheight, 610-619), and en-y8 is unreachable.
        pairings_only=(),
        entries=(
            # quikscript.yaml:668-683 entry_baseline: en (0,0), not_after {qsPea,qsTea,qsYe,qsHe,qsExam,qsIt,qsEat}. In-subset allowlist: qsTea_qsOy only (qsMay/qsTea baseline exits don't reach qsTea today — probe row 10 and probe_supplementary.py "qsTea qsOy qsTea" -> qsTea.en-y0).
            EntryRow(
                height=0,
                x=0,
                from_scope=(Selector("qsTea_qsOy"),),
                provenance="quikscript.yaml:668-683 + probe_supplementary.py",
            ),
            # quikscript.yaml:620-633 half_entry_xheight_ss03: the subset's single unlock — half en (0,5) behind ss03, scoped from x-height exits; in-subset that is qsMay exit_y 5 (line 624). The default half_entry_xheight row (610-619) has no in-subset trigger and is folded into this unlock.
            EntryRow(
                height=5,
                x=0,
                from_scope=(Selector("qsMay", exit_y=5),),
                feature="ss03",
                half=True,
                provenance="quikscript.yaml:610-633",
            ),
            # quikscript.yaml:655-659 entry_top: en (0,8). Nothing in the subset exits at y8, so the scope is empty and no en-y8 cell is reachable; the height still exists for GPOS via the half cell's entry_curs_only anchor (line 637).
            EntryRow(height=8, x=0, from_scope=(), provenance="quikscript.yaml:655-659"),
        ),
        exits=(
            # quikscript.yaml:602-609 exit_baseline: ex (1,0), not_before {qsThaw,qsExcite,qsExam,qsIt}. In-subset toward-allowlist: qsMay (probe row 5).
            ExitRow(
                height=0,
                x=1,
                toward_scope=(Selector("qsMay"),),
                provenance="quikscript.yaml:602-609",
            ),
            # quikscript.yaml:634-645 half_exit_xheight: half shape, ex (1,5), entry_curs_only (0,8), not_before followers_that_reject_tea_half_xheight_exit (includes qsTea). In-subset toward-allowlist: qsIt (probe row 6).
            ExitRow(
                height=5,
                x=1,
                half=True,
                toward_scope=(Selector("qsIt"),),
                provenance="quikscript.yaml:634-645",
            ),
        ),
    ),
    "qsMay": FamilySpec(
        name="qsMay",
        # quikscript.yaml:2121-2123 prose pairing notes, structurally honored: never both baseline, never both x-height. Two-sided cells: (0,5) via entry_baseline (2259-2267) and (5,0) via entry_xheight_exit_baseline (2234-2253, unreachable in-subset).
        pairings_only=((0, 5), (5, 0)),
        entries=(
            # quikscript.yaml:2259-2267 entry_baseline: en (0,0), no select restriction. In-subset allowlist: every subset baseline exit reaches it (probe rows 4, 5, 8 and probe_supplementary.py "qsTea qsOy qsMay"). extend_entry_after via vie_may_entry_extend_triggers (context_sets line 17-22: qsPea,qsTea,qsYe,qsHe,qsIt) restricted to the subset, plus qsTea_qsOy (probed: "qsTea qsOy qsMay" -> qsMay.en-y0.ex-y5.en-ext-1). The en-ext-1 is suppressed when the predecessor's exit already carries the seam's extension pixel — divergence 3 of PLAN.md section 7 (same-seam extensions don't sum).
            EntryRow(
                height=0,
                x=0,
                from_scope=(
                    Selector("qsTea"),
                    Selector("qsIt"),
                    Selector("qsMay"),
                    Selector("qsTea_qsOy"),
                ),
                extend_after=(Selector("qsTea"), Selector("qsIt"), Selector("qsTea_qsOy")),
                extension_suppressed_when_left_extended=True,
                provenance="quikscript.yaml:2259-2267 + context_sets:17-22 + probe_supplementary.py",
            ),
            # quikscript.yaml:2204-2228 entry_xheight: en x=3 alone (2207) but x=2 in the combined (en-y5, ex-y0) cell (2238) — the per-cell anchor-x override of rebuild-design section 3.2. The after-list (eight qsX_qsUtter ligatures plus the reaches_up_and_way_over context set) contains nothing in this subset, so the scope is empty and no en-y5 cell is reachable; the row is kept to document the override.
            EntryRow(height=5, x=3, from_scope=(), provenance="quikscript.yaml:2204-2228,2234-2253"),
        ),
        exits=(
            # quikscript.yaml:2138-2153 prop: exit (5,5) lives on the isolated drawing (the col-4 connector stub is part of the bare bitmap), so the anchor is intrinsic — an unjoined qsMay keeps it (probe rows 3, 10, 16, 24). extend_exit_before toward qsIt (2142-2151) and, gated behind ss03, toward qsTea (2152-2153); the toward-scope is the same pair, so every realized x-height exit carries ex-ext-1. extend_exit_when_entered by 1 (2265) duplicates the same single pixel for entered cells.
            ExitRow(
                height=5,
                x=5,
                intrinsic=True,
                toward_scope=(Selector("qsIt"), Selector("qsTea", feature="ss03")),
                extend_toward=(Selector("qsIt"), Selector("qsTea", feature="ss03")),
                extend_when_entered=True,
                provenance="quikscript.yaml:2138-2153,2265",
            ),
            # quikscript.yaml:2277-2292 exit_baseline: grounded-loop shape, ex (4,0), not_before {qsDay,qsZoo,qsHe,qsNo,qsRoe,qsIt,qsEat,qsUtter,qsOoze}. In-subset toward-allowlist: qsMay only — qsTea is absent from the blocklist yet today's font never grounds qsMay before qsTea (probe row 10), so the empirical allowlist is the faithful transcription.
            ExitRow(
                height=0,
                x=4,
                toward_scope=(Selector("qsMay"),),
                provenance="quikscript.yaml:2277-2292 + probe row 10",
            ),
        ),
    ),
    "qsTea_qsOy": FamilySpec(
        name="qsTea_qsOy",
        pairings_only=(),
        entries=(),
        exits=(
            # quikscript.yaml:708-724: exit (8,0) on the ligature's only form, so the anchor is intrinsic. Unrestricted in the YAML; in-subset all three families accept it at the baseline (probe_supplementary.py: qsIt.en-y0.ex-y5, qsTea.en-y0, qsMay.en-y0.ex-y5.en-ext-1).
            ExitRow(
                height=0,
                x=8,
                intrinsic=True,
                toward_scope=(Selector("qsIt"), Selector("qsTea"), Selector("qsMay")),
                provenance="quikscript.yaml:708-724 + probe_supplementary.py",
            ),
        ),
    ),
    "qsOy": FamilySpec(name="qsOy"),
}

# Probe 1 (synthetic encoding probe, PLAN.md deviation 13): qsIt refuses its baseline exit toward qsMay when the raw rune after qsMay is qsOy. qsOy is the load-bearing choice: it is never locked by the ZWNJ chokepoint, so it legitimately sits in raw second-lookahead classes, and a shaper that skips ZWNJ inside a two-position-forward match (the live leak class _add_zwnj_guards_for_two_position_forward_rules fixes today, quikscript_fea.py:2166) would match it across the break and settle the wrong cell — unless the boundary row with explicit uni200C is emitted and ordered first.
REFUSALS: tuple[RefusalRecord, ...] = (
    RefusalRecord(
        family="qsIt",
        exit_height=0,
        toward="qsMay",
        when_right2=("qsOy",),
        provenance="synthetic encoding probe (PLAN.md deviation 13, probe 1)",
    ),
)

# Probe 2 (synthetic encoding probe, PLAN.md deviation 13): the entryless ligature takes a settled-pair cell when its resolved left is any qsIt cell (locked twins included — they are settled glyphs and may sit in backtrack classes). qsTea_qsOy is the load-bearing choice: it is never locked by the chokepoint, so it can appear immediately after ZWNJ as its raw self, and a shaper that skips ZWNJ in backtrack matching would see the qsIt before the break and fire the contextual cell — unless the identity guard with explicit uni200C in the backtrack slot is emitted and ordered first.
SETTLED_PAIR_CELLS: dict[tuple[str, str], str] = {("qsTea_qsOy", "qsIt"): "qsTea_qsOy.after-it"}


@dataclass(frozen=True)
class GlyphRecord:
    name: str
    bitmap: tuple[str, ...]
    y_offset: int = 0
    entry: tuple[int, int] | None = None
    exit: tuple[int, int] | None = None
    entry_curs_only: tuple[int, int] | None = None
    advance_width: int | None = None
    provenance: str = ""


_IT_BAR = ("#",) * 6  # quikscript.yaml:2740-2747 qsIt prop
_IT_BAR_EX_EXT = ("# ", "# ", "# ", "# ", "# ", "##")  # ex-ext-1: one connector pixel at the y0 exit row
_TEA_BAR = ("#",) * 9  # quikscript.yaml:576-586 qsTea prop
_TEA_HALF = ("#", "#", "#", "#", " ", " ", " ", " ", " ")  # quikscript.yaml:587-598 half shape
# quikscript.yaml:2126-2137 qsMay mono (the isolated drawing, col-4 stub kept while the exit is live)
_MAY_MONO = ("   ##", "  #  ", "  #  ", " #   ", " #   ", "#### ", " #  #", " #  #", "  ## ")
_MAY_MONO_EX_EXT = ("   ###", "  #   ", "  #   ", " #    ", " #    ", "####  ", " #  # ", " #  # ", "  ##  ")
_MAY_MONO_EN_EXT = ("    ##", "   #  ", "   #  ", "  #   ", "  #   ", "##### ", "  #  #", "  #  #", "   ## ")
_MAY_MONO_EN_EXT_EX_EXT = (
    "    ###",
    "   #   ",
    "   #   ",
    "  #    ",
    "  #    ",
    "#####  ",
    "  #  # ",
    "  #  # ",
    "   ##  ",
)
# quikscript.yaml:2167-2178 exits_at_baseline (the grounded loop)
_MAY_GROUNDED = ("  ##", " #  ", " #  ", "#   ", "#   ", "# ##", "#  #", "#  #", " ## ")
# quikscript.yaml:3098-3105 qsOy prop
_OY_PROP = (" ###    ", "#  ##   ", "#  # #  ", " ##   # ", "       #", "       #")
# quikscript.yaml:712-724 qsTea_qsOy prop
_TEA_OY = (
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


def _glyphs() -> dict[str, GlyphRecord]:
    records = [
        GlyphRecord("space", (), advance_width=7, provenance="glyph_data/punctuation.yaml space"),
        GlyphRecord("uni200C", (), advance_width=0, provenance="glyph_data/punctuation.yaml:52-54"),
        GlyphRecord("qsOy", _OY_PROP, provenance="quikscript.yaml:3098-3105 (inert rune, PLAN.md section 2)"),
        GlyphRecord("qsTea_qsOy", _TEA_OY, exit=(8, 0), provenance="quikscript.yaml:708-724"),
        GlyphRecord(
            "qsTea_qsOy.after-it",
            _TEA_OY,
            exit=(8, 0),
            provenance="synthetic encoding probe (PLAN.md deviation 13, probe 2): bitmap-identical settled-pair cell after a qsIt left",
        ),
        # qsIt
        GlyphRecord("qsIt", _IT_BAR, provenance="quikscript.yaml:2740-2747"),
        GlyphRecord(
            "qsIt.noentry", _IT_BAR, provenance="ZWNJ chokepoint twin (quikscript_fea.py:2503-2519 shape)"
        ),
        GlyphRecord("qsIt.ex-y0", _IT_BAR, exit=(1, 0), provenance="quikscript.yaml:2843-2853"),
        GlyphRecord(
            "qsIt.en-y5.ex-y0",
            _IT_BAR,
            entry=(0, 5),
            exit=(1, 0),
            provenance="quikscript.yaml:2751-2761 (boundary-kept dangling exit, probe rows 6/7/18)",
        ),
        GlyphRecord(
            "qsIt.en-y5.ex-y0.ex-ext-1",
            _IT_BAR_EX_EXT,
            entry=(0, 5),
            exit=(2, 0),
            provenance="quikscript.yaml:2760 extend_exit_when_entered, probe rows 11-12",
        ),
        GlyphRecord(
            "qsIt.en-y5.ex-noentry",
            _IT_BAR,
            entry=(0, 5),
            provenance="exit-withdrawn cell, PLAN.md section 7 divergence 2",
        ),
        GlyphRecord(
            "qsIt.en-y0.ex-y5",
            _IT_BAR,
            entry=(0, 0),
            exit=(1, 5),
            provenance="quikscript.yaml:2762-2773 (boundary-kept dangling exit, probe_supplementary.py qsTea qsOy qsIt)",
        ),
        GlyphRecord(
            "qsIt.en-y0.ex-noentry",
            _IT_BAR,
            entry=(0, 0),
            provenance="exit-withdrawn cell, PLAN.md section 7 divergence 2",
        ),
        # qsTea
        GlyphRecord("qsTea", _TEA_BAR, provenance="quikscript.yaml:576-586"),
        GlyphRecord("qsTea.noentry", _TEA_BAR, provenance="ZWNJ chokepoint twin"),
        GlyphRecord("qsTea.ss03", _TEA_BAR, provenance="ss03 marker twin, PLAN.md section 4 item 2"),
        GlyphRecord("qsTea.ss03.noentry", _TEA_BAR, provenance="ZWNJ chokepoint twin of the marker"),
        GlyphRecord("qsTea.ex-y0", _TEA_BAR, exit=(1, 0), provenance="quikscript.yaml:602-609"),
        GlyphRecord(
            "qsTea.noentry.ex-y0",
            _TEA_BAR,
            exit=(1, 0),
            provenance="locked twin's settled exit cell (PLAN.md section 4 item 4)",
        ),
        GlyphRecord(
            "qsTea.half.ex-y5",
            _TEA_HALF,
            exit=(1, 5),
            entry_curs_only=(0, 8),
            provenance="quikscript.yaml:634-645",
        ),
        GlyphRecord(
            "qsTea.noentry.half.ex-y5",
            _TEA_HALF,
            exit=(1, 5),
            provenance="locked twin's settled exit cell, the PLAN.md section 4 'ZWNJ qsTea qsIt' example",
        ),
        GlyphRecord("qsTea.en-y0", _TEA_BAR, entry=(0, 0), provenance="quikscript.yaml:668-683"),
        GlyphRecord(
            "qsTea.half.en-y5",
            _TEA_HALF,
            entry=(0, 5),
            provenance="quikscript.yaml:620-633 (today's qsTea.half.en-y5.after-xheight-exit)",
        ),
        # qsMay
        GlyphRecord("qsMay", _MAY_MONO, y_offset=-3, exit=(5, 5), provenance="quikscript.yaml:2126-2141"),
        GlyphRecord(
            "qsMay.noentry",
            _MAY_MONO,
            y_offset=-3,
            exit=(5, 5),
            provenance="ZWNJ chokepoint twin; keeps the live exit (recon/families.md section 1)",
        ),
        GlyphRecord(
            "qsMay.ex-ext-1",
            _MAY_MONO_EX_EXT,
            y_offset=-3,
            exit=(6, 5),
            provenance="quikscript.yaml:2142-2153",
        ),
        GlyphRecord(
            "qsMay.ex-y0", _MAY_GROUNDED, y_offset=-3, exit=(4, 0), provenance="quikscript.yaml:2277-2292"
        ),
        GlyphRecord(
            "qsMay.en-y0.ex-y5",
            _MAY_MONO,
            y_offset=-3,
            entry=(0, 0),
            exit=(5, 5),
            provenance="quikscript.yaml:2259-2267",
        ),
        GlyphRecord(
            "qsMay.en-y0.ex-y5.en-ext-1",
            _MAY_MONO_EN_EXT,
            y_offset=-3,
            entry=(0, 0),
            exit=(6, 5),
            provenance="quikscript.yaml:2266 extend_entry_after",
        ),
        GlyphRecord(
            "qsMay.en-y0.ex-y5.ex-ext-1",
            _MAY_MONO_EX_EXT,
            y_offset=-3,
            entry=(0, 0),
            exit=(6, 5),
            provenance="quikscript.yaml:2265 extend_exit_when_entered",
        ),
        GlyphRecord(
            "qsMay.en-y0.ex-y5.en-ext-1.ex-ext-1",
            _MAY_MONO_EN_EXT_EX_EXT,
            y_offset=-3,
            entry=(0, 0),
            exit=(7, 5),
            provenance="quikscript.yaml:2265-2266, probe row 13",
        ),
    ]
    return {record.name: record for record in records}


GLYPHS: dict[str, GlyphRecord] = _glyphs()

PIXEL = 50
# Default advance is bitmap width + 2 pixels with the ink centered, and senior_tighten then shaves the right sidebearing only (build_font.py:1032-1048), so drawn ink — and therefore every anchor — sits exactly one pixel right of the glyph origin.
INK_X_OFFSET = 1


def anchors_in_font_units(glyph_name: str) -> dict[str, tuple[int, int] | None] | None:
    """Entry/exit anchors in font units relative to the glyph origin, in the same frame emit.py writes into the curs feature. Returns None for glyphs outside the spec. Used by conform.py's gap-0 pen-position check."""
    record = GLYPHS.get(glyph_name)
    if record is None:
        return None

    def in_font_units(anchor: tuple[int, int] | None) -> tuple[int, int] | None:
        if anchor is None:
            return None
        return ((anchor[0] + INK_X_OFFSET) * PIXEL, anchor[1] * PIXEL)

    return {"entry": in_font_units(record.entry), "exit": in_font_units(record.exit)}


# Coverage-parity NULL/NULL curs registrations for locked twins, mirroring today's emitter (quikscript_fea.py:6542-6556): a twin with no exit is registered at each height where its base family declares an entry row.
NOENTRY_PARITY_HEIGHTS = {
    "qsIt.noentry": (0, 5),
    "qsTea.noentry": (0, 5, 8),
    "qsTea.ss03.noentry": (0, 5, 8),
    "qsMay.noentry": (0,),
    "qsTea.noentry.half.ex-y5": (8,),
}


@dataclass(frozen=True)
class SubsetSpec:
    codepoint_to_token: dict[int, str] = field(default_factory=lambda: dict(CODEPOINT_TO_TOKEN))
    families: dict[str, FamilySpec] = field(default_factory=lambda: dict(FAMILIES))
    glyphs: dict[str, GlyphRecord] = field(default_factory=lambda: dict(GLYPHS))
    formation: tuple[tuple[str, str, str], ...] = FORMATION
    marker_families: dict[str, str] = field(default_factory=lambda: dict(MARKER_FAMILIES))
    entry_bearing_families: tuple[str, ...] = ENTRY_BEARING_FAMILIES
    locked_exit_reuses_plain: dict[str, bool] = field(default_factory=lambda: dict(LOCKED_EXIT_REUSES_PLAIN))
    feature_configurations: tuple[frozenset[str], ...] = FEATURE_CONFIGURATIONS
    noentry_parity_heights: dict[str, tuple[int, ...]] = field(
        default_factory=lambda: dict(NOENTRY_PARITY_HEIGHTS)
    )
    refusals: tuple[RefusalRecord, ...] = REFUSALS
    settled_pair_cells: dict[tuple[str, str], str] = field(default_factory=lambda: dict(SETTLED_PAIR_CELLS))


SPEC = SubsetSpec()
