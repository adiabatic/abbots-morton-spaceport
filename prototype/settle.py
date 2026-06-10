"""The pure-Python settlement function for the prototype subset (doc/rebuild-design.md section 6.1 restricted to the spec in prototype/spec.py).

Public API (fixed by prototype/PLAN.md section 1): `settle(sequence, features) -> list[Settled]`, where `sequence` is a string over the six-symbol alphabet and `features` is an iterable of feature tags (or a mapping tag -> bool). `Settled` carries the chosen cell as a structured tuple (family, entry height or none, exit height or none, modifiers, locked flag), the seam state toward the next position, and the display glyph name. Boundary characters (space, ZWNJ) yield boundary `Settled` entries with their own glyph names.

This module contains no OpenType anywhere: it is the conformance oracle. `transition` is the per-position kernel that `settle` folds over runs; prototype/table.py tabulates the same kernel to build the decision table.

Run as a script to self-check against the probe rows of prototype/recon/families.md section 4 with the PLAN.md section 7 divergence register applied (plus the supplementary probes and the documented deviations appended to PLAN.md).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spec import SPEC, EntryRow, ExitRow, FamilySpec, SubsetSpec

BOUNDARY_GLYPHS = {"space": "space", "zwnj": "uni200C"}


@dataclass(frozen=True)
class Settled:
    family: str
    entry: int | None
    exit: int | None
    modifiers: tuple[str, ...]
    locked: bool
    seam_toward_next: int | None
    glyph_name: str

    @property
    def is_boundary(self) -> bool:
        return self.family in ("space", "uni200C")


@dataclass(frozen=True)
class LeftContext:
    kind: str  # "edge" | "space" | "zwnj" | "letter"
    family: str | None = None
    committed: int | None = None
    extended: bool = False
    glyph_name: str | None = None


@dataclass(frozen=True)
class RightToken:
    kind: str  # "edge" | "space" | "zwnj" | "letter"
    family: str | None = None


EDGE = RightToken("edge")
SPACE = RightToken("space")
ZWNJ = RightToken("zwnj")


def _normalize_features(features) -> frozenset[str]:
    if features is None:
        return frozenset()
    if isinstance(features, Mapping):
        return frozenset(tag for tag, on in features.items() if on)
    if isinstance(features, str):
        return frozenset((features,))
    return frozenset(features)


def _toward_matches(row: ExitRow, right_family: str, features: frozenset[str]) -> bool:
    return any(sel.matches(right_family, None, features) for sel in row.toward_scope)


def _refused(family: str, exit_height: int, right1_family: str, right2: RightToken, spec: SubsetSpec) -> bool:
    """Window-decidable refusals (section 6.1.3). Only the candidates loop consults this: a refusal's right-square slot sits outside the window of the position one further left, so `_prospect` stays deliberately optimistic about it (section 6.1.4.2) and the joint flag absorbs the divergence."""
    for record in spec.refusals:
        if (
            record.family == family
            and record.exit_height == exit_height
            and record.toward == right1_family
            and right2.kind == "letter"
            and right2.family in record.when_right2
        ):
            return True
    return False


def _acceptor_exists(
    our_family: str, exit_row: ExitRow, right_family: str, features: frozenset[str], spec: SubsetSpec
) -> bool:
    right = spec.families.get(right_family)
    if right is None:
        return False
    for entry in right.entries:
        if entry.height != exit_row.height:
            continue
        if entry.feature is not None and entry.feature not in features:
            continue
        if any(sel.matches(our_family, exit_row.height, features) for sel in entry.from_scope):
            return True
    return False


def _allowed_exit_rows(fam: FamilySpec, entered_height: int | None) -> list[ExitRow]:
    if entered_height is None:
        return list(fam.exits)
    return [row for row in fam.exits if (entered_height, row.height) in fam.pairings_only]


def _prospect(
    seam: int | None, right1_family: str, right2: RightToken, features: frozenset[str], spec: SubsetSpec
) -> int:
    if right2.kind != "letter":
        return 0
    right1 = spec.families[right1_family]
    if seam is not None and right1.entry_row(seam) is None:
        return 0
    for row in _allowed_exit_rows(right1, seam):
        if _toward_matches(row, right2.family, features) and _acceptor_exists(
            right1_family, row, right2.family, features, spec
        ):
            return 1
    return 0


def _glyph_name(
    family: str,
    entered: int | None,
    anchor: int | None,
    half: bool,
    en_ext: bool,
    ex_ext: bool,
    locked: bool,
    seam: int | None,
    spec: SubsetSpec,
) -> str:
    if family in ("qsOy", "qsTea_qsOy"):
        return family
    is_bare_cell = (
        entered is None
        and not en_ext
        and not ex_ext
        and (anchor is None or seam is None and _intrinsic_only(family, anchor, spec))
    )
    if locked and (seam is None and not ex_ext):
        return f"{family}.noentry"
    prefix = family
    if locked and not spec.locked_exit_reuses_plain.get(family, True):
        prefix = f"{family}.noentry"
    parts = [prefix]
    if half:
        parts.append("half")
    if entered is not None:
        parts.append(f"en-y{entered}")
    if family == "qsMay":
        if entered is not None:
            parts.append("ex-y5")
        elif anchor == 0:
            parts.append("ex-y0")
    else:
        if anchor is not None:
            parts.append(f"ex-y{anchor}")
        elif entered is not None and family == "qsIt":
            # qsIt's entered cells normally carry the paired exit anchor, so the withdrawn cell is named after the withdrawal; entered qsTea cells are entry-only by design and take no suffix.
            parts.append("ex-noentry")
    if en_ext:
        parts.append("en-ext-1")
    if ex_ext:
        parts.append("ex-ext-1")
    name = ".".join(parts)
    if name == family and is_bare_cell:
        return family
    return name


def _intrinsic_only(family: str, anchor: int | None, spec: SubsetSpec) -> bool:
    for row in spec.families[family].exits:
        if row.height == anchor:
            return row.intrinsic
    return False


def transition(
    left: LeftContext,
    family: str,
    locked: bool,
    features: frozenset[str],
    right1: RightToken,
    right2: RightToken,
    spec: SubsetSpec = SPEC,
) -> tuple[Settled, bool, tuple[str, ...]]:
    """Settle one position: returns (Settled, joint flag, provenance notes). The joint flag marks rows where the structural floor broke a join-count tie between candidates that differ in seam realization (section 6.1.4.5) — the table routes these to the expensive test tier."""
    fam = spec.families[family]
    notes: list[str] = []

    entered: int | None = None
    entry_row: EntryRow | None = None
    if not locked and left.kind == "letter" and left.committed is not None:
        entry_row = None
        for row in fam.entries:
            if row.height != left.committed:
                continue
            if row.feature is not None and row.feature not in features:
                continue
            if any(sel.matches(left.family, left.committed, features) for sel in row.from_scope):
                entry_row = row
                break
        if entry_row is None:
            raise RuntimeError(
                f"E-STRANDED: {left.glyph_name} committed an exit at y={left.committed} but {family} has no acceptor row (lookahead closure should have prevented this)"
            )
        entered = entry_row.height
        notes.append(entry_row.provenance)

    en_ext = False
    if entry_row is not None and entry_row.extend_after:
        en_ext = any(sel.matches(left.family, left.committed, features) for sel in entry_row.extend_after)
        if en_ext and entry_row.extension_suppressed_when_left_extended and left.extended:
            en_ext = False
            notes.append(
                "en-ext-1 suppressed: the predecessor's exit already carries the seam's extension pixel (PLAN.md section 7 divergence 3)"
            )

    allowed = _allowed_exit_rows(fam, entered)
    candidates: list[tuple[ExitRow | None, int | None, int]] = []
    for index, row in enumerate(allowed):
        realizable = (
            right1.kind == "letter"
            and _toward_matches(row, right1.family, features)
            and _acceptor_exists(family, row, right1.family, features, spec)
            and not _refused(family, row.height, right1.family, right2, spec)
        )
        if realizable:
            candidates.append((row, row.height, index))
        if row.intrinsic:
            candidates.append((row, None, index))
        elif right1.kind != "letter" and row.anchor_kept_at_boundary and entered is not None:
            candidates.append((row, None, index))
    if not any(row is not None and row.intrinsic for row, _seam, _idx in candidates):
        candidates.append((None, None, len(fam.exits)))

    scored = []
    for row, seam, index in candidates:
        join_count = (1 if seam is not None else 0) + (
            _prospect(seam, right1.family, right2, features, spec) if right1.kind == "letter" else 0
        )
        scored.append((-join_count, seam if seam is not None else 99, index, row, seam))
    scored.sort(key=lambda item: item[:3])

    joint = False
    if len(scored) >= 2 and scored[0][0] == scored[1][0]:
        top_realizes = scored[0][4] is not None
        runner_realizes = scored[1][4] is not None
        joint = top_realizes != runner_realizes

    _neg, _h, _idx, exit_row, seam = scored[0]
    ex_ext = False
    if exit_row is not None and seam is not None:
        ex_ext = (exit_row.extend_when_entered and entered is not None) or any(
            sel.matches(right1.family, None, features) for sel in exit_row.extend_toward
        )
        notes.append(exit_row.provenance)

    anchor = exit_row.height if exit_row is not None else None
    half = (entry_row.half if entry_row is not None else False) or (
        exit_row.half if exit_row is not None else False
    )
    name = _glyph_name(family, entered, anchor, half, en_ext, ex_ext, locked, seam, spec)
    pair_modifiers: tuple[str, ...] = ()
    if left.kind == "letter":
        pair_cell = spec.settled_pair_cells.get((family, left.family))
        if pair_cell is not None:
            name = pair_cell
            pair_modifiers = (name.rsplit(".", 1)[1],)
            notes.append(spec.glyphs[pair_cell].provenance)
    if name not in spec.glyphs:
        raise RuntimeError(f"settled cell {name} has no glyph record in spec.GLYPHS")

    modifiers = (
        tuple(mod for mod, on in (("half", half), ("en-ext-1", en_ext), ("ex-ext-1", ex_ext)) if on)
        + pair_modifiers
    )
    settled = Settled(
        family=family,
        entry=entered,
        exit=anchor,
        modifiers=modifiers,
        locked=locked,
        seam_toward_next=seam,
        glyph_name=name,
    )
    return settled, joint, tuple(note for note in notes if note)


def _tokenize(sequence: str, spec: SubsetSpec) -> list[str]:
    tokens = []
    for ch in sequence:
        token = spec.codepoint_to_token.get(ord(ch))
        if token is None:
            raise ValueError(f"U+{ord(ch):04X} is outside the prototype alphabet")
        tokens.append(token)
    return tokens


def _form_ligatures(tokens: list[str], spec: SubsetSpec) -> list[str]:
    formed = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            for lead, trail, ligature in spec.formation:
                if tokens[i] == lead and tokens[i + 1] == trail:
                    formed.append(ligature)
                    i += 2
                    break
            else:
                formed.append(tokens[i])
                i += 1
            continue
        formed.append(tokens[i])
        i += 1
    return formed


def settle(sequence: str, features=(), spec: SubsetSpec = SPEC) -> list[Settled]:
    feats = _normalize_features(features)
    tokens = _form_ligatures(_tokenize(sequence, spec), spec)

    def right_token(index: int) -> RightToken:
        if index >= len(tokens):
            return EDGE
        token = tokens[index]
        if token == "space":
            return SPACE
        if token == "zwnj":
            return ZWNJ
        return RightToken("letter", token)

    out: list[Settled] = []
    left = LeftContext("edge")
    for i, token in enumerate(tokens):
        if token in ("space", "zwnj"):
            glyph = BOUNDARY_GLYPHS[token]
            out.append(Settled(glyph, None, None, (), False, None, glyph))
            left = LeftContext(token)
            continue
        locked = left.kind == "zwnj" and token in spec.entry_bearing_families
        settled, _joint, _notes = transition(
            left, token, locked, feats, right_token(i + 1), right_token(i + 2), spec
        )
        out.append(settled)
        left = LeftContext(
            "letter",
            family=token,
            committed=settled.seam_toward_next,
            extended="ex-ext-1" in settled.modifiers,
            glyph_name=settled.glyph_name,
        )
    return out


# Expected rows: prototype/recon/families.md section 4 with the PLAN.md section 7 divergence register applied, the supplementary probes (prototype/probe_supplementary.py), and the deviations recorded in PLAN.md's Deviations section. Tokens use family names; "zwnj"/"space" are boundaries.
SELF_CHECK_ROWS: tuple[tuple[tuple[str, ...], frozenset[str], tuple[str, ...]], ...] = (
    ((("qsIt",)), frozenset(), ("qsIt",)),
    ((("qsTea",)), frozenset(), ("qsTea",)),
    ((("qsMay",)), frozenset(), ("qsMay",)),
    (("qsIt", "qsMay"), frozenset(), ("qsIt.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1")),
    (("qsTea", "qsMay"), frozenset(), ("qsTea.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1")),
    (("qsTea", "qsIt"), frozenset(), ("qsTea.half.ex-y5", "qsIt.en-y5.ex-y0")),
    (("qsMay", "qsIt"), frozenset(), ("qsMay.ex-ext-1", "qsIt.en-y5.ex-y0")),
    (("qsMay", "qsMay"), frozenset(), ("qsMay.ex-y0", "qsMay.en-y0.ex-y5")),
    # Divergence 1: the first qsIt settles bare instead of carrying today's dangling ex-y5.
    (("qsIt", "qsIt"), frozenset(), ("qsIt", "qsIt")),
    (("qsMay", "qsTea"), frozenset(), ("qsMay", "qsTea")),
    (("qsMay", "qsTea"), frozenset({"ss03"}), ("qsMay.ex-ext-1", "qsTea.half.en-y5")),
    (
        ("qsTea", "qsIt", "qsMay"),
        frozenset(),
        ("qsTea.half.ex-y5", "qsIt.en-y5.ex-y0.ex-ext-1", "qsMay.en-y0.ex-y5"),
    ),
    # Divergence 3: the follower qsMay's en-ext-1 is suppressed because the entered qsIt already carries the seam's pixel.
    (
        ("qsMay", "qsIt", "qsMay"),
        frozenset(),
        ("qsMay.ex-ext-1", "qsIt.en-y5.ex-y0.ex-ext-1", "qsMay.en-y0.ex-y5"),
    ),
    (
        ("qsIt", "qsMay", "qsIt"),
        frozenset(),
        ("qsIt.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.en-y5.ex-y0"),
    ),
    (("qsMay", "qsTea", "qsIt"), frozenset({"ss03"}), ("qsMay.ex-ext-1", "qsTea.half.en-y5", "qsIt")),
    # Divergence 2: the entered middle qsIt before an entryless follower withdraws its exit.
    (
        ("qsTea", "qsIt", "qsTea", "qsIt"),
        frozenset(),
        ("qsTea.half.ex-y5", "qsIt.en-y5.ex-noentry", "qsTea.half.ex-y5", "qsIt.en-y5.ex-y0"),
    ),
    (("qsMay", "qsMay", "qsMay"), frozenset(), ("qsMay.ex-y0", "qsMay.en-y0.ex-y5", "qsMay")),
    (("qsIt", "zwnj", "qsTea"), frozenset(), ("qsIt", "uni200C", "qsTea.noentry")),
    (
        ("qsTea", "qsIt", "zwnj", "qsMay"),
        frozenset(),
        ("qsTea.half.ex-y5", "qsIt.en-y5.ex-y0", "uni200C", "qsMay.noentry"),
    ),
    (("qsMay", "zwnj", "qsIt", "qsTea"), frozenset(), ("qsMay", "uni200C", "qsIt.noentry", "qsTea")),
    # Divergence 4: no join across ZWNJ under ss03 (today's confirmed leak, fixed structurally).
    (("qsMay", "zwnj", "qsTea"), frozenset({"ss03"}), ("qsMay", "uni200C", "qsTea.noentry")),
    (("qsMay", "space", "qsTea"), frozenset({"ss03"}), ("qsMay", "space", "qsTea")),
    (("qsTea", "qsOy"), frozenset(), ("qsTea_qsOy",)),
    # Probe 2 (PLAN.md deviation 13): the ligature takes the settled-pair cell after any qsIt left.
    (("qsIt", "qsTea", "qsOy"), frozenset(), ("qsIt", "qsTea_qsOy.after-it")),
    (("qsMay", "qsTea", "qsOy"), frozenset(), ("qsMay", "qsTea_qsOy")),
    # Divergence 5: formation is staged before the ss03 marker, so the ligature forms under ss03 too.
    (("qsMay", "qsTea", "qsOy"), frozenset({"ss03"}), ("qsMay", "qsTea_qsOy")),
    (("qsIt", "qsIt", "qsTea", "qsOy"), frozenset(), ("qsIt", "qsIt", "qsTea_qsOy.after-it")),
    # Ligature forward seams, pinned by probe_supplementary.py (dangling-exit cells per the boundary rule and divergence 2).
    (("qsTea", "qsOy", "qsIt"), frozenset(), ("qsTea_qsOy", "qsIt.en-y0.ex-y5")),
    (("qsTea", "qsOy", "qsTea"), frozenset(), ("qsTea_qsOy", "qsTea.en-y0")),
    (("qsTea", "qsOy", "qsMay"), frozenset(), ("qsTea_qsOy", "qsMay.en-y0.ex-y5.en-ext-1")),
    (("qsTea", "qsOy", "qsOy"), frozenset(), ("qsTea_qsOy", "qsOy")),
    (("qsTea", "qsOy", "qsTea", "qsOy"), frozenset(), ("qsTea_qsOy", "qsTea_qsOy")),
    (("qsTea", "zwnj", "qsOy"), frozenset(), ("qsTea", "uni200C", "qsOy")),
    # Locked twins settle their exit side (PLAN.md section 4: the qsTea output mints a .noentry cell twin; qsIt/qsMay reuse the plain entryless cells, matching today's probes).
    (("zwnj", "qsTea", "qsIt"), frozenset(), ("uni200C", "qsTea.noentry.half.ex-y5", "qsIt.en-y5.ex-y0")),
    (
        ("zwnj", "qsTea", "qsMay"),
        frozenset(),
        ("uni200C", "qsTea.noentry.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1"),
    ),
    (("zwnj", "qsIt", "qsMay"), frozenset(), ("uni200C", "qsIt.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1")),
    (("zwnj", "qsMay", "qsIt"), frozenset(), ("uni200C", "qsMay.ex-ext-1", "qsIt.en-y5.ex-y0")),
    (("zwnj", "qsMay", "qsTea"), frozenset({"ss03"}), ("uni200C", "qsMay.ex-ext-1", "qsTea.half.en-y5")),
    (("zwnj", "qsMay", "qsTea"), frozenset(), ("uni200C", "qsMay.noentry", "qsTea")),
    (("qsIt", "zwnj"), frozenset(), ("qsIt", "uni200C")),
    (("qsMay", "zwnj"), frozenset(), ("qsMay", "uni200C")),
    # Deviations recorded in PLAN.md: divergence-2-class withdrawals where today keeps the dangling anchor, and ss03 chains where join-count buys two joins.
    (("qsTea", "qsIt", "qsIt"), frozenset(), ("qsTea.half.ex-y5", "qsIt.en-y5.ex-noentry", "qsIt")),
    (("qsMay", "qsIt", "qsTea"), frozenset(), ("qsMay.ex-ext-1", "qsIt.en-y5.ex-noentry", "qsTea")),
    (
        ("qsTea", "qsIt", "qsTea", "qsOy"),
        frozenset(),
        ("qsTea.half.ex-y5", "qsIt.en-y5.ex-noentry", "qsTea_qsOy.after-it"),
    ),
    (("qsIt", "qsMay", "qsTea"), frozenset(), ("qsIt.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1", "qsTea")),
    (
        ("qsIt", "qsMay", "qsTea"),
        frozenset({"ss03"}),
        ("qsIt.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsTea.half.en-y5"),
    ),
    (
        ("qsTea", "qsMay", "qsIt"),
        frozenset(),
        ("qsTea.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1.ex-ext-1", "qsIt.en-y5.ex-y0"),
    ),
    (("qsOy", "qsIt"), frozenset(), ("qsOy", "qsIt")),
    (("qsMay", "qsOy"), frozenset(), ("qsMay", "qsOy")),
    # Probe 1 (PLAN.md deviation 13): qsIt refuses its baseline exit toward qsMay when the raw right-square rune is qsOy; boundaries (edge, space, ZWNJ) at the right-square slot never refuse.
    (("qsIt", "qsMay", "qsOy"), frozenset(), ("qsIt", "qsMay", "qsOy")),
    (
        ("qsIt", "qsMay", "qsTea", "qsOy"),
        frozenset(),
        ("qsIt.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1", "qsTea_qsOy"),
    ),
    (
        ("qsTea", "qsIt", "qsMay", "qsOy"),
        frozenset(),
        ("qsTea.half.ex-y5", "qsIt.en-y5.ex-noentry", "qsMay", "qsOy"),
    ),
    (("zwnj", "qsIt", "qsMay", "qsOy"), frozenset(), ("uni200C", "qsIt.noentry", "qsMay", "qsOy")),
    (
        ("qsIt", "qsMay", "zwnj", "qsOy"),
        frozenset(),
        ("qsIt.ex-y0", "qsMay.en-y0.ex-y5.en-ext-1", "uni200C", "qsOy"),
    ),
    # Probe 2: ZWNJ severs the settled-pair adjacency (the ligature still forms — deviation 5 — but the left context is the boundary, not the qsIt).
    (("qsIt", "zwnj", "qsTea", "qsOy"), frozenset(), ("qsIt", "uni200C", "qsTea_qsOy")),
    (
        ("zwnj", "qsIt", "qsTea", "qsOy"),
        frozenset(),
        ("uni200C", "qsIt.noentry", "qsTea_qsOy.after-it"),
    ),
    (
        ("qsMay", "qsIt", "qsTea", "qsOy"),
        frozenset(),
        ("qsMay.ex-ext-1", "qsIt.en-y5.ex-noentry", "qsTea_qsOy.after-it"),
    ),
)

TOKEN_TO_CHAR = {token: chr(cp) for cp, token in SPEC.codepoint_to_token.items()}


def _self_check() -> None:
    failures = []
    for tokens, features, expected in SELF_CHECK_ROWS:
        text = "".join(TOKEN_TO_CHAR[token] for token in tokens)
        actual = tuple(settled.glyph_name for settled in settle(text, features))
        if actual != expected:
            failures.append((tokens, sorted(features), expected, actual))
    if failures:
        for tokens, features, expected, actual in failures:
            print(f"FAIL {' '.join(tokens)} {features}:\n  expected {expected}\n  actual   {actual}")
        raise SystemExit(1)
    print(f"settle.py self-check: {len(SELF_CHECK_ROWS)} rows OK")


if __name__ == "__main__":
    _self_check()
