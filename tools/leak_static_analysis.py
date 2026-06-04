"""Static analysis of the emitted Senior `calt` FEA, hunting for shaping-leak risk without shaping every letter tuple.

The dynamic check (`test/test_shaping.py::_check_break_isolation`, the sweep in `tools/build_check_html.py::find_leaks`) shapes sequences and flags any non-joining adjacent pair whose chosen glyphs differ in context vs. in isolation. That sweep costs ≈44x per letter of depth, so it is only run to depth 3 — leaks needing 4+ letters of context go uncaught.

This module instead reads the compiled FEA as a program and asks a structural question: can any contextual substitution change a glyph's shape because of a neighbor it does NOT cursively join? That is the seed of every leak. A non-join is `exit_ys(left) & entry_ys(right) == set()`, exactly as `_pair_join_ys` defines it.

This is a PROTOTYPE / research tool: its job is to test whether "leaks reduce to bounded-window rule firings" actually holds for this font by cross-checking its findings against the dynamic ground truth.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "test"
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from quikscript_shaping_helpers import _entry_ys, _exit_ys  # noqa: E402

# ---------------------------------------------------------------------------
# Parsing the calt feature into structured rules.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    lookup: str
    kind: str  # "sub" or "ignore"
    backtrack: tuple[frozenset[str], ...]  # left context, nearest-to-pivot LAST
    pivot: frozenset[str]  # the single marked input position
    lookahead: tuple[frozenset[str], ...]  # right context, nearest-to-pivot FIRST
    replacement: str | None  # for "sub": the output glyph; None for "ignore"
    line_no: int


@dataclass
class CaltProgram:
    classes: dict[str, frozenset[str]]
    lookups: list[str]  # in feature-application order
    rules_by_lookup: dict[str, list[Rule]]

    @property
    def rules(self) -> list[Rule]:
        out: list[Rule] = []
        for name in self.lookups:
            out.extend(self.rules_by_lookup.get(name, ()))
        return out


_CLASS_DEF_RE = re.compile(r"^\s*(@[A-Za-z0-9_]+)\s*=\s*\[([^\]]*)\]\s*;")
_LOOKUP_OPEN_RE = re.compile(r"^\s*lookup\s+([A-Za-z0-9_]+)\s*\{")
_LOOKUP_CLOSE_RE = re.compile(r"^\s*\}\s*([A-Za-z0-9_]+)\s*;")
_FEATURE_OPEN_RE = re.compile(r"^\s*feature\s+([A-Za-z0-9_]+)\s*\{")


def _resolve_token(token: str, classes: dict[str, frozenset[str]]) -> frozenset[str]:
    token = token.strip()
    if token.startswith("@"):
        return classes.get(token, frozenset())
    return frozenset([token])


def _split_class_body(body: str) -> list[str]:
    return [g for g in body.replace("\n", " ").split() if g]


def _parse_inline_classes(segment: str, classes: dict[str, frozenset[str]]) -> list[frozenset[str]]:
    """Tokenize a context segment that may mix bare glyphs, @classrefs, and [inline lists] into a list of position sets."""
    positions: list[frozenset[str]] = []
    i = 0
    n = len(segment)
    while i < n:
        c = segment[i]
        if c.isspace():
            i += 1
            continue
        if c == "[":
            j = segment.index("]", i)
            members = _split_class_body(segment[i + 1 : j])
            resolved: set[str] = set()
            for m in members:
                resolved |= _resolve_token(m, classes)
            positions.append(frozenset(resolved))
            i = j + 1
            continue
        # bare token (glyph name or @classref) up to next whitespace
        m = re.match(r"\S+", segment[i:])
        assert m is not None  # guaranteed: current char is non-space
        tok = m.group(0)
        positions.append(_resolve_token(tok, classes))
        i += len(tok)
    return positions


def _parse_rule(line: str, lookup: str, line_no: int, classes: dict[str, frozenset[str]]) -> Rule | None:
    raw = line.strip()
    if raw.endswith(";"):
        raw = raw[:-1]
    kind = "sub"
    if raw.startswith("ignore sub "):
        kind = "ignore"
        body = raw[len("ignore sub ") :]
    elif raw.startswith("sub "):
        body = raw[len("sub ") :]
    else:
        return None

    replacement: str | None = None
    if kind == "sub":
        if " by " not in body:
            return None  # not a contextual-form rule we model (e.g. ligature `sub a b by c` handled below)
        body, rep = body.rsplit(" by ", 1)
        rep = rep.strip()
        # Replacement may be a single glyph or a [list]; we only model single-glyph contextual swaps.
        replacement = rep if not rep.startswith("[") else None

    if "'" not in body:
        return None  # no marked position; not a contextual rule

    positions = _parse_context_with_mark(body, classes)
    if positions is None:
        return None
    backtrack, pivot, lookahead = positions
    return Rule(
        lookup=lookup,
        kind=kind,
        backtrack=tuple(backtrack),
        pivot=pivot,
        lookahead=tuple(lookahead),
        replacement=replacement,
        line_no=line_no,
    )


def _parse_context_with_mark(
    body: str, classes: dict[str, frozenset[str]]
) -> tuple[list[frozenset[str]], frozenset[str], list[frozenset[str]]] | None:
    """Split a marked context body into (backtrack, pivot, lookahead). Exactly one token carries the ' mark."""
    # Tokenize respecting [inline lists]; a marked token is "<tok>'".
    tokens: list[tuple[str, bool]] = []  # (token_text, is_marked)
    i = 0
    n = len(body)
    while i < n:
        if body[i].isspace():
            i += 1
            continue
        if body[i] == "[":
            j = body.index("]", i)
            tok = body[i : j + 1]
            i = j + 1
        else:
            m = re.match(r"\S+", body[i:])
            assert m is not None  # guaranteed: current char is non-space
            tok = m.group(0)
            i += len(tok)
        marked = tok.endswith("'")
        if marked:
            tok = tok[:-1]
        tokens.append((tok, marked))

    marks = [idx for idx, (_, mk) in enumerate(tokens) if mk]
    if len(marks) != 1:
        return None  # multi-mark or unmarked — out of scope for this prototype
    pivot_idx = marks[0]

    def resolve(tok: str) -> frozenset[str]:
        if tok.startswith("["):
            members = _split_class_body(tok[1:-1])
            out: set[str] = set()
            for m in members:
                out |= _resolve_token(m, classes)
            return frozenset(out)
        return _resolve_token(tok, classes)

    backtrack = [resolve(t) for t, _ in tokens[:pivot_idx]]
    pivot = resolve(tokens[pivot_idx][0])
    lookahead = [resolve(t) for t, _ in tokens[pivot_idx + 1 :]]
    # Backtrack in FEA is written left-to-right; nearest-to-pivot is last. Keep as-is (index -1 = nearest).
    return backtrack, pivot, lookahead


@cache
def parse_calt(fea_path: str) -> CaltProgram:
    text = Path(fea_path).read_text()
    lines = text.splitlines()
    classes: dict[str, frozenset[str]] = {}
    lookups: list[str] = []
    rules_by_lookup: dict[str, list[Rule]] = {}

    in_feature: str | None = None
    cur_lookup: str | None = None
    # Rules written directly in `feature calt { ... }` (outside any named lookup) form an implicit
    # lookup at their position in feature order. We give each maximal run its own synthetic name so
    # application order is preserved relative to the named lookups around it.
    implicit_seq = 0

    def target_lookup() -> str:
        nonlocal implicit_seq
        if cur_lookup is not None:
            return cur_lookup
        name = f"__feature_calt_implicit_{implicit_seq}"
        if name not in rules_by_lookup:
            lookups.append(name)
            rules_by_lookup[name] = []
        return name

    for idx, line in enumerate(lines, start=1):
        m = _CLASS_DEF_RE.match(line)
        if m:
            classes[m.group(1)] = frozenset(_split_class_body(m.group(2)))
            continue
        m = _FEATURE_OPEN_RE.match(line)
        if m:
            in_feature = m.group(1)
            continue
        if in_feature != "calt":
            continue
        m = _LOOKUP_OPEN_RE.match(line)
        if m:
            name = m.group(1) or ""  # local (not the closure-captured cur_lookup) so its str type is kept
            cur_lookup = name
            if name not in rules_by_lookup:
                lookups.append(name)
                rules_by_lookup[name] = []
            continue
        m = _LOOKUP_CLOSE_RE.match(line)
        if m:
            cur_lookup = None
            implicit_seq += 1  # the next feature-level run is a distinct implicit lookup
            continue
        rule = _parse_rule(line, target_lookup(), idx, classes)
        if rule is not None:
            rules_by_lookup[target_lookup()].append(rule)

    return CaltProgram(classes=classes, lookups=lookups, rules_by_lookup=rules_by_lookup)


# ---------------------------------------------------------------------------
# Join model.
# ---------------------------------------------------------------------------


def joins(left: str, right: str) -> bool:
    """Whether *left* can cursively hand off to *right* — they share an exit/entry Y. Mirrors `_pair_join_ys`."""
    return bool(_exit_ys(left) & _entry_ys(right))


def any_join(lefts: frozenset[str], rights: frozenset[str]) -> bool:
    return any(joins(l, r) for l in lefts for r in rights)


if __name__ == "__main__":
    fea = sys.argv[1] if len(sys.argv) > 1 else str(TEST_DIR / "AbbotsMortonSpaceportSansSenior-Regular.fea")
    prog = parse_calt(fea)
    rules = prog.rules
    n_sub = sum(1 for r in rules if r.kind == "sub")
    n_ign = sum(1 for r in rules if r.kind == "ignore")
    bt = {}
    la = {}
    for r in rules:
        bt[len(r.backtrack)] = bt.get(len(r.backtrack), 0) + 1
        la[len(r.lookahead)] = la.get(len(r.lookahead), 0) + 1
    print(f"classes: {len(prog.classes)}  lookups: {len(prog.lookups)}")
    print(f"parsed rules: {len(rules)}  (sub={n_sub}, ignore={n_ign})")
    print(f"backtrack-length histogram: {dict(sorted(bt.items()))}")
    print(f"lookahead-length histogram: {dict(sorted(la.items()))}")
