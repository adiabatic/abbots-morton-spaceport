"""The `data-expect` checker: shapes a pin's runs with HarfBuzz and checks the glyphs and junctions against the pin, reading only the font and the glyph-facts sidecar beside it.

`check_pin` checks one pin (`corpus.Pin`), `check_run` one of its runs, and `assert_expect_any` a text against candidate expect strings. The root conftest's pin items and the build's pin gate (`engine/pipeline/manual_pins.py`) both call `check_pin`, so the suite and the gate check a pin alike. The module is a leaf: it imports nothing from `engine/pipeline/`, `engine/review/`, `tools/` or `test/`.

What the checker reads, and from where:

- **A junction** from the font's GPOS `curs` lookups and HarfBuzz's clusters (`CheckFont.junctions`, `classify.JunctionClassifier`): two characters in one cluster are a ligature, and two glyphs are joined at each height where the left has an exit and the right an entry in that height's lookup.
- **A token's glyph** is the first glyph of the cluster that covers the token's first character (`shaping.first_glyph_covering`), so a variation selector's leftover glyph or a mark in that cluster does not stand in for the letter, and neither shifts the tokens after it.
- **A glyph's base, stance, entry and exit heights, adjustments and copy** from its name, which is a cell label (`engine/pipeline/settle.py` `cell_label`) or the name of a raw cmap glyph or one of its copies (`parse_glyph_name`).
- **A trait, a bound bitmap, and whether the glyph draws the isolated cell** from the glyph-facts sidecar (`read_glyph_facts`) the build writes beside the font when it mints the cells (`engine/pipeline/glyph_facts.py`).

`MODIFIER_FORMS` lists the modifier vocabulary, which `doc/data-expect.md` documents. A modifier outside it is an error (`UnknownModifier`), never a pass, and a pending pin does not excuse one.

A `|` between two Quikscript letters asserts a break and, on a font with cursive lookups, that each letter beside it shapes as it does when the text is split there into separate buffers (`_check_break_isolation`): the same outline at the same offsets and advance, both shaped with `kern` off, since a kern pair is the space the font puts between the two letters at that break. A `?` between two letters that do not join asserts the same, and `|?|` asserts the break alone. A run of a font with no cursive lookups (Junior) is checked for glyph identity only: every connection is a maybe.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from .classify import JunctionClassifier
from .corpus import Pin, collect_pin_runs
from .expect import (
    Connection,
    ExpectToken,
    PartitionedRun,
    expand_maybe_ligatures,
    parse_expect,
    token_char_spans,
)
from .shaping import ShapeResult, Shaper, first_glyph_covering, last_glyph_covering

GLYPH_FACTS_FORMAT = "ams-glyph-facts/1"
GLYPH_FACTS_SUFFIX = ".glyph-facts.json"
# A pin's `space` token stands for a space or a ZWNJ (`expect.LOZENGE_MAP`), and the engine's fonts give a ZWNJ its own glyph.
BOUNDARY_GLYPH_EQUIVALENTS = {"space": frozenset({"space", "uni200C"})}
TRAITS = frozenset({"half", "alt"})
# The modifier vocabulary, one line per form, as `modifier_holds` reads it.
MODIFIER_FORMS = (
    ("half, alt", "the stance's trait (sidecar)"),
    ("entry, exit", "the glyph has an entry, an exit (name)"),
    ("en-yN, ex-yN", "the glyph enters, exits at height N (name)"),
    ("extended", "some en-ext-N or ex-ext-N adjustment (name)"),
    ("en-ext-N, ex-ext-N, en-con-N, ex-con-N, en-trim-N", "that adjustment (name)"),
    ("locked", "the ZWNJ-locked form: a cell's `locked` adjustment or a raw glyph's locked copy (name)"),
    ("<bitmap>", "the cell draws that bound bitmap, as `reaches-way-back` (sidecar)"),
)

_COPY_FEATURES_RE = re.compile(r"ss\d\d(?:_ss\d\d)*")
_COSMETIC_RE = re.compile(r"ss\d\d")
_ENTRY_RE = re.compile(r"en-y(\d+)")
_EXIT_RE = re.compile(r"ex-y(\d+)")
_HEIGHT_RE = re.compile(r"(en|ex)-y(\d+)")
_ADJUSTMENT_RE = re.compile(r"(?:en-(?:ext|con|trim)|ex-(?:ext|con))-\d+")
_EXTENDED_RE = re.compile(r"(?:en|ex)-ext-\d+")
RAW_LOCKED_SUFFIX = "noentry"
LOCKED_ADJUSTMENT = "locked"


class UnknownModifier(ValueError):
    pass


def glyph_facts_path(font_path: Path | str) -> Path:
    """Return the sidecar beside a font: the font's name up to its first hyphen, then `GLYPH_FACTS_SUFFIX`, so `Senior-Regular.otf` and `Senior-Bold.otf` share `Senior.glyph-facts.json` and `AbbotsMortonSpaceportSansSenior-Regular.otf` has `AbbotsMortonSpaceportSansSenior.glyph-facts.json`."""
    path = Path(font_path)
    return path.with_name(path.stem.split("-", 1)[0] + GLYPH_FACTS_SUFFIX)


@dataclass(frozen=True)
class GlyphFacts:
    """What a glyph's name does not say: its stance's traits, the bound bitmap it draws (a `bitmaps:` sibling of its stance), and whether it draws the isolated cell, as the bare cmap glyph and the isolated cell's own glyph do."""

    traits: frozenset[str] = frozenset()
    bitmap: str | None = None
    isolated: bool = False


def read_glyph_facts(path: Path | str) -> dict[str, GlyphFacts]:
    """Read a glyph-facts sidecar into each glyph's facts. Raises ValueError for a document in another format, and OSError for a file that does not read."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("format") != GLYPH_FACTS_FORMAT:
        raise ValueError(f"{path} is not an {GLYPH_FACTS_FORMAT} document")
    facts: dict[str, GlyphFacts] = {}
    for name, entry in document["glyphs"].items():
        facts[name] = GlyphFacts(
            traits=frozenset(entry.get("traits", ())),
            bitmap=entry.get("bitmap"),
            isolated=bool(entry.get("isolated", False)),
        )
    return facts


@dataclass(frozen=True)
class GlyphName:
    """A compiled glyph's name, parsed (`parse_glyph_name`). `base` is the rune (`qsMay`, `qsDay_qsUtter`) or, for a glyph outside the runes, the name up to its first dot. A cell's name gives its `stance`, its entry and exit heights and its adjustments; a raw glyph (the bare cmap glyph and its marker, ss10 and locked copies) has no stance. `features` holds the stylistic sets a raw copy stands for (`qsTea.ss03_ss05`) or the cosmetic set a cell's copy is drawn for (`qsOwe.sole.ss06`). `locked` is a cell's `locked` adjustment or a raw glyph's locked copy (`qsMay.noentry`)."""

    name: str
    base: str
    stance: str | None = None
    entry: int | None = None
    exit: int | None = None
    adjustments: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    locked: bool = False

    @property
    def is_rune(self) -> bool:
        return self.base.startswith("qs")


def parse_glyph_name(name: str) -> GlyphName:
    """Parse a compiled glyph name. A cell's name is its label, `rune.stance[.en-yN][.ex-yN][.adjustment…]`, with a cosmetic copy's set appended. A raw glyph's name is the bare rune, then the stylistic sets of a marker or ss10 copy (`qsTea.ss03`), then `noentry` on a locked copy. No stance is named `noentry` or `ssNN`, so the two forms do not collide. Raises ValueError for a rune glyph's name that fits neither."""
    parts = name.split(".")
    base, rest = parts[0], parts[1:]
    if not base.startswith("qs"):
        return GlyphName(name=name, base=base)
    if not rest:
        return GlyphName(name=name, base=base)
    if _COPY_FEATURES_RE.fullmatch(rest[0]) or rest[0] == RAW_LOCKED_SUFFIX:
        index = 0
        features: tuple[str, ...] = ()
        if _COPY_FEATURES_RE.fullmatch(rest[0]):
            features = tuple(rest[0].split("_"))
            index = 1
        locked = False
        if index < len(rest) and rest[index] == RAW_LOCKED_SUFFIX:
            locked = True
            index += 1
        if index != len(rest):
            raise ValueError(f"{name!r} is a raw glyph's copy with parts after its sets and lock")
        return GlyphName(name=name, base=base, features=features, locked=locked)
    stance = rest[0]
    index = 1
    entry: int | None = None
    exit_: int | None = None
    if index < len(rest) and (match := _ENTRY_RE.fullmatch(rest[index])):
        entry = int(match.group(1))
        index += 1
    if index < len(rest) and (match := _EXIT_RE.fullmatch(rest[index])):
        exit_ = int(match.group(1))
        index += 1
    tail = rest[index:]
    cosmetic: tuple[str, ...] = ()
    if tail and _COSMETIC_RE.fullmatch(tail[-1]):
        cosmetic = (tail[-1],)
        tail = tail[:-1]
    if any(_HEIGHT_RE.fullmatch(part) for part in tail):
        raise ValueError(f"{name!r} names a height out of the label's order")
    adjustments = tuple(tail)
    return GlyphName(
        name=name,
        base=base,
        stance=stance,
        entry=entry,
        exit=exit_,
        adjustments=adjustments,
        features=cosmetic,
        locked=LOCKED_ADJUSTMENT in adjustments,
    )


class CheckFont:
    """One font as the checker reads it: a shaper, its cursive junctions when it has `curs` lookups (`cursive`), its GSUB features, its outlines for the isolation check, and the glyph-facts sidecar when one is given."""

    def __init__(
        self, font_path: Path | str, *, glyph_facts: Path | str | None = None, cursive: bool = True
    ) -> None:
        self.path = Path(font_path)
        self.shaper = Shaper(self.path)
        self.classifier = JunctionClassifier(self.path) if cursive else None
        tt = TTFont(str(self.path))
        self.feature_tags = frozenset(
            record.FeatureTag for record in tt["GSUB"].table.FeatureList.FeatureRecord
        )
        self._glyph_set = tt.getGlyphSet()
        self._outlines: dict[str, tuple] = {}
        self.facts: dict[str, GlyphFacts] | None = None
        self.bitmaps: frozenset[str] = frozenset()
        self.traits: frozenset[str] = TRAITS
        if glyph_facts is not None:
            self.facts = read_glyph_facts(glyph_facts)
            self.bitmaps = frozenset(fact.bitmap for fact in self.facts.values() if fact.bitmap)
            self.traits = TRAITS | frozenset(trait for fact in self.facts.values() for trait in fact.traits)

    @property
    def cursive(self) -> bool:
        return self.classifier is not None

    def shape(self, text: str, features: Mapping[str, bool] | None = None) -> ShapeResult:
        return self.shaper.shape(text, dict(features) if features else None)

    def junctions(self, result: ShapeResult, length: int) -> tuple[str, ...]:
        """Return the junction between each pair of adjacent characters: `lig` inside one glyph, and otherwise the joined heights (`y0+y5`) or `break`. A font with no cursive lookups has no joins, so every junction between two glyphs is a break."""
        junctions: list[str] = []
        for k in range(length - 1):
            left = last_glyph_covering(result.clusters, k)
            right_last = last_glyph_covering(result.clusters, k + 1)
            if left == right_last:
                junctions.append("lig")
                continue
            if self.classifier is None:
                junctions.append("break")
                continue
            right = first_glyph_covering(result.clusters, k + 1)
            junctions.append(self.classifier.classify(result.names[left], result.names[right]))
        return tuple(junctions)

    def outline(self, glyph_name: str) -> tuple:
        signature = self._outlines.get(glyph_name)
        if signature is None:
            pen = RecordingPen()
            self._glyph_set[glyph_name].draw(pen)
            signature = self._outlines[glyph_name] = tuple(pen.value)
        return signature

    def glyph_facts(self, glyph_name: str) -> GlyphFacts | None:
        if self.facts is None:
            return None
        return self.facts.get(glyph_name)


def _needs_facts(font: CheckFont, modifier: str) -> None:
    if font.facts is None:
        raise UnknownModifier(
            f"modifier .{modifier} is read from the glyph-facts sidecar, and {font.path.name} has none"
        )


def modifier_holds(font: CheckFont, glyph: GlyphName, modifier: str) -> bool:
    """Return whether `modifier` (without its `!`) holds of the shaped glyph, as `MODIFIER_FORMS` reads it. Raises `UnknownModifier` for a modifier outside the vocabulary, and for a trait or bitmap modifier on a font without a sidecar."""
    if modifier in font.traits:
        _needs_facts(font, modifier)
        facts = font.glyph_facts(glyph.name)
        return facts is not None and modifier in facts.traits
    if modifier == "entry":
        return glyph.entry is not None
    if modifier == "exit":
        return glyph.exit is not None
    if match := _HEIGHT_RE.fullmatch(modifier):
        side, height = match.group(1), int(match.group(2))
        return (glyph.entry if side == "en" else glyph.exit) == height
    if modifier == "extended":
        return any(_EXTENDED_RE.fullmatch(part) for part in glyph.adjustments)
    if _ADJUSTMENT_RE.fullmatch(modifier):
        return modifier in glyph.adjustments
    if modifier == LOCKED_ADJUSTMENT:
        return glyph.locked
    if modifier in font.bitmaps:
        facts = font.glyph_facts(glyph.name)
        return facts is not None and facts.bitmap == modifier
    raise UnknownModifier(
        f"unknown modifier .{modifier}: doc/data-expect.md lists the vocabulary (`check.MODIFIER_FORMS`)"
    )


def _expected_base(token: ExpectToken) -> str:
    if token["lig_base"]:
        return f"{token['base']}_{token['lig_base']}"
    return token["base"]


def _base_matches(expected: str, glyph_base: str) -> bool:
    return glyph_base == expected or glyph_base in BOUNDARY_GLYPH_EQUIVALENTS.get(expected, frozenset())


def _heights(junction: str) -> set[int]:
    if not junction.startswith("y"):
        return set()
    return {int(part[1:]) for part in junction.split("+")}


def _try_interpretation(
    font: CheckFont,
    text: str,
    result: ShapeResult,
    junctions: tuple[str, ...],
    tokens: list[ExpectToken],
    connections: list[Connection],
) -> str | None:
    """Check one reading of a run's tokens and connections against its shaping, and return the first failure or None. Raises ValueError when the tokens do not cover the text, and `UnknownModifier`."""
    spans = token_char_spans(text, tokens)
    for i, token in enumerate(tokens):
        start, end = spans[i]
        glyph_name = result.names[first_glyph_covering(result.clusters, start)]
        glyph = parse_glyph_name(glyph_name)
        expected = _expected_base(token)
        if not _base_matches(expected, glyph.base):
            return f"Glyph {i}: expected base {expected}, got {glyph_name!r}"
        if token["exact_glyph"]:
            facts = font.glyph_facts(glyph_name)
            if font.facts is None:
                raise UnknownModifier(
                    f"modifier .∅ is read from the glyph-facts sidecar, and {font.path.name} has none"
                )
            if facts is None or not facts.isolated:
                return f"Glyph {i}: expected the isolated form of {expected}, got {glyph_name!r}"
        for modifier in token["variants"]:
            if not modifier_holds(font, glyph, modifier):
                return f"Glyph {i}: expected .{modifier} on {glyph_name!r}"
        for modifier in token["neg_variants"]:
            if modifier_holds(font, glyph, modifier):
                return f"Glyph {i}: .{modifier} must not hold on {glyph_name!r}"
        for k in range(start, end - 1):
            if junctions[k] != "lig":
                return f"Glyph {i}: expected one ligature glyph, got a {junctions[k]!r} junction at character {k}"

    for i, conn in enumerate(connections):
        index = spans[i + 1][0] - 1
        junction = junctions[index]
        left = result.names[last_glyph_covering(result.clusters, index)]
        right = result.names[last_glyph_covering(result.clusters, index + 1)]
        kind = conn["kind"]
        if kind == "maybe":
            continue
        if kind in ("break", "break_no_isolation"):
            if junction != "break":
                return f"Connection {i}: expected break between {left} and {right}, got {junction!r}"
        elif kind == "join":
            if not _heights(junction):
                return f"Connection {i}: expected join between {left} and {right}, got {junction!r}"
        elif kind == "height":
            if conn["y"] not in _heights(junction):
                return f"Connection {i}: expected join at y={conn['y']} between {left} and {right}, got {junction!r}"
        else:
            raise ValueError(f"unknown connection kind {kind!r}")
    return None


def _token_glyphs(result: ShapeResult, span: tuple[int, int], offset: int = 0) -> list[int]:
    start, end = span
    return [index for index, cluster in enumerate(result.clusters) if start <= cluster + offset < end]


def _check_break_isolation(
    font: CheckFont,
    text: str,
    junctions: tuple[str, ...],
    tokens: list[ExpectToken],
    connections: list[Connection],
    features: Mapping[str, bool] | None,
) -> str | None:
    """Return why a letter beside a non-join shapes differently when the run is split there, or None.

    A non-join is a "break" between two Quikscript letter tokens, or a "maybe" between two whose junction is a break. The run is reshaped with a buffer boundary at every non-join, so no contextual lookup can read across one, and the glyphs of each letter beside a non-join must have the same outlines and position records (offsets and advance) as in the whole run, though their names may differ. Both shapings turn `kern` off: a kern pair is the space the font puts between the two letters at the break, which no split can reproduce, and the conformance sweep checks every break's kern (`conform.check_break_advances`). A ZWNJ is no equivalent reference, since the font locks the letter after one.
    """

    def is_letter(token: ExpectToken) -> bool:
        # Boundary tokens (◊space, ◊ZWNJ, an escaped character) are left out: the font may change a letter beside them, as the locked forms after a ZWNJ do.
        return token["base"].startswith("qs")

    spans = token_char_spans(text, tokens)
    isolating: list[int] = []
    for i, conn in enumerate(connections):
        if not (is_letter(tokens[i]) and is_letter(tokens[i + 1])):
            continue
        if conn["kind"] == "break" or (conn["kind"] == "maybe" and junctions[spans[i + 1][0] - 1] == "break"):
            isolating.append(i)
    if not isolating:
        return None

    unkerned = {**(features or {}), "kern": False}
    whole_run = font.shape(text, unkerned)
    bounds = [0, *sorted({spans[i + 1][0] for i in isolating}), len(text)]
    segments = [(start, font.shape(text[start:end], unkerned)) for start, end in zip(bounds, bounds[1:])]

    def split_glyphs(span: tuple[int, int]) -> list[tuple[str, tuple[int, int, int]]]:
        glyphs: list[tuple[str, tuple[int, int, int]]] = []
        for offset, segment in segments:
            for index in _token_glyphs(segment, span, offset):
                glyphs.append((segment.names[index], segment.positions[index]))
        return glyphs

    for number, ci in enumerate(isolating, start=1):
        for side, ti in (("left", ci), ("right", ci + 1)):
            whole = [
                (whole_run.names[index], whole_run.positions[index])
                for index in _token_glyphs(whole_run, spans[ti])
            ]
            alone = split_glyphs(spans[ti])
            if whole == alone:
                continue
            same_outline = len(whole) == len(alone) and all(
                font.outline(a) == font.outline(b) for (a, _), (b, _) in zip(whole, alone)
            )
            same_position = [position for _, position in whole] == [position for _, position in alone]
            if same_outline and same_position:
                continue
            differs = " + ".join(
                part for part, same in (("outline", same_outline), ("position", same_position)) if not same
            )
            return (
                f"Break #{number} (between tokens {ci} and {ci + 1}): {side} glyph differs in isolation ({differs}) —\n"
                f"  whole run: {whole}\n"
                f"  split:     {alone}\n"
                "A non-joining pair must shape the same when split. A contextual lookup reads across the break."
            )
    return None


def check_run(font: CheckFont, run: PartitionedRun, expect: str) -> str | None:
    """Check one run of a pin against its shaping in `font`, and return why it fails or None. Raises ValueError when the run's tokens do not cover its text, and `UnknownModifier`."""
    text = run["text"]
    if not text:
        return None
    features = run.get("features") or None
    absent = sorted(tag for tag, on in (features or {}).items() if on and tag not in font.feature_tags)
    if absent:
        return f"[{font.path.name}] the font has no {', '.join(absent)}, which run {text!r} of {expect!r} turns on"
    result = font.shape(text, features)
    junctions = font.junctions(result, len(text))
    interpretations = expand_maybe_ligatures(run["tokens"], run["connections"])
    if not font.cursive:
        interpretations = [
            (tokens, [Connection(kind="maybe", y=None) for _ in connections])
            for tokens, connections in interpretations
        ]
    errors: list[str] = []
    for tokens, connections in interpretations:
        try:
            error = _try_interpretation(font, text, result, junctions, tokens, connections)
        except UnknownModifier:
            raise
        except ValueError as structural:
            errors.append(f"structural: {structural}")
            continue
        if error is None:
            if not font.cursive:
                return None
            isolation = _check_break_isolation(font, text, junctions, tokens, connections, features)
            if isolation is None:
                return None
            return (
                f"[{font.path.name}] Isolation check failed for run {text!r} in {expect!r} (shaped: {list(result.names)}).\n"
                + isolation
            )
        errors.append(error)
    return (
        f"[{font.path.name}] No interpretation matched for run {text!r} in {expect!r} (shaped: {list(result.names)}; junctions: {list(junctions)}).\n"
        + "\n".join(f"  Interpretation {i + 1}: {error}" for i, error in enumerate(errors))
    )


@dataclass
class PinOutcome:
    """What `check_pin` found: `errors` (a failure of the pin, empty when it passes), `vocabulary` (an unknown modifier or a structural fault in the pin, which no pending mark excuses), the runs it checked, and each skipped run's reason."""

    pin: Pin
    errors: list[str] = field(default_factory=list)
    vocabulary: list[str] = field(default_factory=list)
    checked_runs: int = 0
    skipped_runs: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors and not self.vocabulary

    @property
    def status(self) -> str:
        """`pass` or `fail`, `pending` for a pending pin that fails as expected, `pending-passes` for a pending pin that passes, and `invalid` for a pin the vocabulary or the grammar rejects, pending or not."""
        if self.vocabulary:
            return "invalid"
        if self.pin.pending is not None:
            return "pending-passes" if not self.errors else "pending"
        return "pass" if not self.errors else "fail"

    def message(self) -> str:
        return "\n".join([*self.vocabulary, *self.errors])


def check_pin(
    fonts: Mapping[str, CheckFont | None],
    pin: Pin,
    skip_reasons: Mapping[str, str] | None = None,
) -> PinOutcome:
    """Check every run of `pin` against the font its run names (`senior` or `junior`). A run whose font maps to None is skipped, with `skip_reasons`' reason for that font."""
    outcome = PinOutcome(pin=pin)
    try:
        runs = collect_pin_runs(pin)
    except ValueError as error:
        outcome.vocabulary.append(f"{pin.source}: {pin.expect!r} does not fit its text: {error}")
        return outcome
    for run in runs:
        if not run["text"]:
            continue
        font = fonts.get(run["font"])
        if font is None:
            reason = (skip_reasons or {}).get(run["font"], f"no {run['font']} font")
            outcome.skipped_runs.append(f"{run['font']} run {run['text']!r}: {reason}")
            continue
        outcome.checked_runs += 1
        try:
            error = check_run(font, run, pin.expect)
        except UnknownModifier as unknown:
            outcome.vocabulary.append(f"{pin.source}: {unknown}")
            continue
        except ValueError as structural:
            outcome.vocabulary.append(
                f"{pin.source}: {pin.expect!r} does not fit run {run['text']!r}: {structural}"
            )
            continue
        if error is not None:
            outcome.errors.append(error)
    return outcome


def assert_expect_any(
    font: CheckFont, text: str, expects: Sequence[str], features: Mapping[str, bool] | None = None
) -> None:
    """Pass when any of `expects`, each a data-expect string, matches the shaping of `text` in `font` as one run, trying them in order. Raises AssertionError listing every attempt when none does, and `UnknownModifier` as `modifier_holds` does."""
    errors: list[str] = []
    for expect in expects:
        tokens, connections = parse_expect(expect)
        run: PartitionedRun = {"font": "senior", "text": text, "tokens": tokens, "connections": connections}
        if features:
            run["features"] = dict(features)
        error = check_run(font, run, expect)
        if error is None:
            return
        errors.append(f"  {expect!r}: {error}")
    raise AssertionError(f"No candidate data-expect matched shaping of {text!r}:\n" + "\n".join(errors))
