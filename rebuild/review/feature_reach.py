"""Which configurations shape a window as `default` does, read from both fonts' layout tables at the start of the corpus build's units phase.

A review unit is compared in both fonts under each configuration in its set (`_ink_readings` in `rebuild/review/build.py`). A configuration whose features cannot fire on the unit's text gives the shapes `default` gives, in both fonts, so it takes `default`'s comparison and the text is shaped once for both. `derive_fold` decides, for each configuration, the letters a text must hold before its features can fire, its trigger letters, and `ConfigFold.effective` maps a configuration to `default` for a text that holds none of them, inside a unit that also holds `default`.

A configuration passes only when every check below holds in both fonts:

- No GPOS feature record carries one of its feature tags, and GSUB has no feature variations.
- Every GSUB lookup its feature records list is a single substitution or a chaining contextual substitution (`_reach`). A single substitution fires only on a glyph it covers, so the families of the glyphs it covers are trigger families. A chaining rule fires only where each slot of its backtrack, input and lookahead holds one of that slot's glyphs, so a slot whose glyphs are all of one family confines the rule to that family. Each rule is judged on its own (`_rules`): a class-based rule's slot holds the glyphs its class names, its first input slot the covered glyphs in its input class, class 0 included, and any other slot in class 0 holds every glyph no class names, so it confines nothing. A rule with a slot no glyph can fill never fires, and a rule with no slot of one family fails the check. The lookups a chaining rule calls run only where the rule matched, so they need no check of their own.
- The trigger families are the families both fonts' single substitutions cover, plus, for the chaining rules none of those confines, families chosen one at a time across both fonts' rules, the one that confines the most rules still open first, until every rule is confined (`_trigger_families`). In today's fonts that gives ·Tea for ss03, ss05 and ss03+ss05, since every one of the old font's rules for them names a slot of ·Tea glyphs and the rebuild's single substitutions take ·Tea, and ·It for ss04.
- A glyph's family is its name up to the first `.`. A configuration's trigger letters are the code points either font's Unicode cmap subtables and variation sequences map to a glyph of one of its trigger families. That rests on the premise that a glyph of the family appears in a shaped run only when the text holds one of those code points, and the derivation checks the premise in each font (`_Font.premise`): no GSUB substitution makes a glyph of a trigger family from glyphs with none of that family, so the families of the glyphs a substitution makes come from the run it reads; glyph 0, which a character with no cmap entry shapes to, is of no trigger family; and every trigger letter is a private-use code point, which no other character normalizes to. A font with no Unicode cmap subtable fails it.

A configuration that fails a check is shaped under its own features in every window, and `ConfigFold.note` names it and the check it failed, so a slower units phase is never silent.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.ttLib import TTFont

from rebuild.review.ink import features_for

DEFAULT = "default"

# GSUB lookup types (OpenType's GSUB chapter). A configuration's own lookups may be SINGLE or CHAINING. The premise check reads the substitutions of every other type but the two contextual ones, whose substitutions are lookups it reads on their own, and EXTENSION wraps another type.
SINGLE = 1
MULTIPLE = 2
ALTERNATE = 3
LIGATURE = 4
CONTEXTUAL = 5
CHAINING = 6
EXTENSION = 7
REVERSE_CHAINING = 8

# The most trigger families the line names one by one; the line gives a configuration with more their count.
NAMED_FAMILIES = 4


def family(glyph: str) -> str:
    """Return a glyph's family: its name up to the first `.`, so `qsTea.half` and `qsTea.ss03_ss05` are `qsTea`, and a ligature such as `qsDay_qsUtter` is a family of its own."""
    return glyph.split(".", 1)[0]


@dataclass(frozen=True)
class ConfigFold:
    """Each configuration's trigger letters for the corpus build's units phase. `triggers` holds the configurations that passed `derive_fold`'s checks in both fonts, each with the characters a text must hold before its features can fire, and `families` the trigger families those characters map to, for the line. `refused` holds each configuration that failed, with the check it failed. `configs` keeps the order the derivation was asked for, which the line follows. A configuration in neither mapping, which the derivation was not asked about, is never folded."""

    configs: tuple[str, ...] = ()
    triggers: dict[str, frozenset[str]] = field(default_factory=dict)
    families: dict[str, tuple[str, ...]] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)

    def effective(self, configs: tuple[str, ...], text: str) -> tuple[str, ...]:
        """Return the configuration each of `configs` is compared under for `text`, in order: `default` for one whose trigger letters `text` holds none of, since both fonts shape `text` under it as under `default`, and the configuration itself otherwise. A unit that does not hold `default` keeps every configuration. The audit gave its configurations rows for this text that differ from `default`'s, which a configuration that shapes the text as `default` does should not give, so shaping each of them keeps such a difference in the comparison."""
        if DEFAULT not in configs:
            return configs
        triggers = self.triggers
        return tuple(
            DEFAULT if (letters := triggers.get(config)) is not None and letters.isdisjoint(text) else config
            for config in configs
        )

    def note(self) -> str:
        """Return the line's text: the configurations that take `default`'s shapes for a window with no glyph of their trigger families, grouped by those families, and the configurations shaped in every window, each with the check it failed."""
        groups: dict[tuple[str, ...], list[str]] = {}
        for config in self.configs:
            if config in self.triggers:
                groups.setdefault(self.families[config], []).append(config)
        parts = []
        for families, configs in groups.items():
            whose = "its" if len(configs) == 1 else "their"
            if not families:
                letters = f"in every window, since {whose} features take no glyph"
            elif len(families) > NAMED_FAMILIES:
                letters = f"for a window with none of the {len(families)} families {whose} features take"
            else:
                letters = f"for a window with no {_series(list(families), 'or')}"
            verb = "" if parts else f" {'takes' if len(configs) == 1 else 'take'} default's shapes"
            parts.append(f"{_series(configs)}{verb} {letters}")
        clause = (
            "in a unit that holds default, " + "; ".join(parts)
            if parts
            else "no configuration takes default's shapes"
        )
        for config in self.configs:
            if config in self.refused:
                clause += f"; {config} is shaped in every window because {self.refused[config]}"
        return clause


def _series(items: Sequence[str], conjunction: str = "and") -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def _subtables(lookup) -> tuple[int, list]:
    """Return a GSUB lookup's type and its subtables, an extension lookup's unwrapped to the type and subtables it wraps."""
    if lookup.LookupType != EXTENSION:
        return lookup.LookupType, list(lookup.SubTable)
    subtables = list(lookup.SubTable)
    kind = subtables[0].ExtensionLookupType if subtables else EXTENSION
    return kind, [sub.ExtSubTable for sub in subtables]


def _class_members(class_def) -> dict[int, list[str]]:
    members: dict[int, list[str]] = {}
    for glyph, value in (class_def.classDefs if class_def is not None else {}).items():
        members.setdefault(value, []).append(glyph)
    return members


def _rules(sub) -> Iterator[list[list[str] | None]]:
    """Yield each rule of one chaining contextual subtable as the list of its backtrack, input and lookahead slots, which the caller reads as a set. A slot is the glyphs it accepts, or None for a class-based slot in class 0 past the first input slot, which accepts every glyph no class of its class definition names. The first input slot of a class-based rule is the covered glyphs in the rule set's input class, class 0 among them, since the coverage bounds it."""
    if sub.Format == 1:
        for first, rule_set in zip(sub.Coverage.glyphs, sub.ChainSubRuleSet or []):
            if rule_set is None:
                continue
            for rule in rule_set.ChainSubRule:
                yield [[first], *([glyph] for glyph in (*rule.Backtrack, *rule.Input, *rule.LookAhead))]
    elif sub.Format == 2:
        backtrack = _class_members(sub.BacktrackClassDef)
        inputs = _class_members(sub.InputClassDef)
        lookahead = _class_members(sub.LookAheadClassDef)
        input_class = sub.InputClassDef.classDefs if sub.InputClassDef is not None else {}

        def slot(members: dict[int, list[str]], value: int) -> list[str] | None:
            return None if value == 0 else members.get(value, [])

        for value, rule_set in enumerate(sub.ChainSubClassSet or []):
            if rule_set is None:
                continue
            first = [glyph for glyph in sub.Coverage.glyphs if input_class.get(glyph, 0) == value]
            for rule in rule_set.ChainSubClassRule:
                yield [
                    first,
                    *(slot(backtrack, member) for member in rule.Backtrack),
                    *(slot(inputs, member) for member in rule.Input),
                    *(slot(lookahead, member) for member in rule.LookAhead),
                ]
    elif sub.Format == 3:
        yield [
            list(coverage.glyphs)
            for coverage in (*sub.InputCoverage, *sub.BacktrackCoverage, *sub.LookAheadCoverage)
        ]


def _substitutions(kind: int, sub) -> Iterator[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Yield each substitution one subtable of a non-contextual GSUB lookup makes, as the glyphs it reads and the glyphs it writes."""
    if kind == SINGLE:
        for source, target in sub.mapping.items():
            yield (source,), (target,)
    elif kind in (MULTIPLE, ALTERNATE):
        for source, targets in (sub.mapping if kind == MULTIPLE else sub.alternates).items():
            yield (source,), tuple(targets)
    elif kind == LIGATURE:
        for first, ligatures in sub.ligatures.items():
            for ligature in ligatures:
                yield (first, *ligature.Component), (ligature.LigGlyph,)
    elif kind == REVERSE_CHAINING:
        for source, target in zip(sub.Coverage.glyphs, sub.Substitute):
            yield (source,), (target,)


def _feature_records(font: TTFont, tag: str) -> list:
    if tag not in font:
        return []
    feature_list = font[tag].table.FeatureList  # pyright: ignore[reportAttributeAccessIssue]
    return list(feature_list.FeatureRecord) if feature_list is not None else []


class _Font:
    """One font opened lazily for the derivation, its premise facts read once, on first use."""

    def __init__(self, path: Path | str) -> None:
        self.name = Path(path).name
        self.font = TTFont(str(path), lazy=True)
        self._made: dict[str, str] | None = None
        self._code_points: dict[str, set[int]] | None = None

    def reach(self, tags: frozenset[str]) -> tuple[set[str], list[frozenset[str]]] | str:
        """Return the families the single substitutions under `tags` cover and, for each of their chaining rules that can fire, the families of its one-family slots; or the check the configuration fails in this font."""
        for record in _feature_records(self.font, "GPOS"):
            if record.FeatureTag in tags:
                return f"{self.name}'s GPOS has a {record.FeatureTag} feature record"
        if "GSUB" not in self.font:
            return set(), []
        gsub = self.font["GSUB"].table
        if getattr(gsub, "FeatureVariations", None) is not None:
            return f"{self.name}'s GSUB has feature variations"
        indexes = sorted(
            {
                index
                for record in _feature_records(self.font, "GSUB")
                if record.FeatureTag in tags
                for index in record.Feature.LookupListIndex
            }
        )
        covered: set[str] = set()
        rules: list[frozenset[str]] = []
        for index in indexes:
            kind, subtables = _subtables(gsub.LookupList.Lookup[index])
            if kind not in (SINGLE, CHAINING):
                return f"{self.name}'s lookup {index} is of GSUB type {kind}"
            for sub in subtables:
                if kind == SINGLE:
                    covered.update(family(glyph) for glyph in sub.mapping)
                    continue
                if sub.Format not in (1, 2, 3):
                    return f"{self.name}'s lookup {index} has a chaining subtable of format {sub.Format}"
                for slots in _rules(sub):
                    bounded = [{family(glyph) for glyph in slot} for slot in slots if slot is not None]
                    if not all(bounded):
                        continue
                    single = frozenset(families.pop() for families in bounded if len(families) == 1)
                    if not single:
                        return f"a rule in {self.name}'s lookup {index} has no slot of one family"
                    rules.append(single)
        return covered, rules

    def premise(self, families: frozenset[str]) -> tuple[set[int], str | None]:
        """Return the code points this font's cmap maps to a glyph of one of `families`, and the premise check they fail, if any."""
        if self._made is None:
            self._made = self._made_without()
            self._code_points = self._read_code_points()
        assert self._code_points is not None
        if not self._code_points:
            return set(), f"{self.name} has no Unicode cmap subtable"
        notdef = family(self.font.getGlyphOrder()[0])
        for name in sorted(families):
            if name in self._made:
                return set(), self._made[name]
            if name == notdef:
                return (
                    set(),
                    f"{self.name}'s glyph 0, which a character with no cmap entry shapes to, is a {name} glyph",
                )
        letters = {point for name in families for point in self._code_points.get(name, ())}
        for point in sorted(letters):
            if unicodedata.category(chr(point)) != "Co":
                return (
                    set(),
                    f"{self.name}'s cmap maps U+{point:04X}, outside the private use areas, to a trigger glyph",
                )
        return letters, None

    def _made_without(self) -> dict[str, str]:
        """Return, for each family a GSUB substitution makes a glyph of from glyphs with none of that family, the first such substitution, described. Contextual lookups make nothing themselves: the lookups their rules call are read here on their own."""
        made: dict[str, str] = {}
        if "GSUB" not in self.font:
            return made
        lookup_list = self.font["GSUB"].table.LookupList
        for index, lookup in enumerate(lookup_list.Lookup if lookup_list is not None else ()):
            kind, subtables = _subtables(lookup)
            if kind in (CONTEXTUAL, CHAINING):
                continue
            for sub in subtables:
                for sources, targets in _substitutions(kind, sub):
                    read = {family(glyph) for glyph in sources}
                    for target in targets:
                        if family(target) not in read:
                            made.setdefault(
                                family(target),
                                f"{self.name}'s lookup {index} makes {target} from {' '.join(sources)}",
                            )
        return made

    def _read_code_points(self) -> dict[str, set[int]]:
        """Return each family's code points over every Unicode cmap subtable and every variation sequence. HarfBuzz reads a symbol or Macintosh subtable only in a font with no Unicode subtable, which `premise` refuses."""
        families: dict[str, set[int]] = {}
        for table in self.font["cmap"].tables if "cmap" in self.font else ():
            if not table.isUnicode():
                continue
            pairs = list((getattr(table, "cmap", None) or {}).items())
            for entries in (getattr(table, "uvsDict", None) or {}).values():
                pairs.extend((point, glyph) for point, glyph in entries if glyph is not None)
            for point, glyph in pairs:
                families.setdefault(family(glyph), set()).add(point)
        return families

    def close(self) -> None:
        self.font.close()


def _trigger_families(covered: set[str], rules: Sequence[frozenset[str]]) -> tuple[str, ...]:
    """Return `covered` with the families that confine every rule it leaves open, chosen one at a time, the family that confines the most open rules first and the first by name among equals."""
    chosen = set(covered)
    open_rules = [options for options in rules if chosen.isdisjoint(options)]
    while open_rules:
        counts = Counter(name for options in open_rules for name in options)
        pick = min(counts, key=lambda name: (-counts[name], name))
        chosen.add(pick)
        open_rules = [options for options in open_rules if pick not in options]
    return tuple(sorted(chosen))


def _derive_one(
    opened: Sequence[_Font], tags: frozenset[str]
) -> tuple[tuple[str, ...], frozenset[str]] | str:
    """Return one configuration's trigger families and trigger letters over `opened`, or the first check it fails."""
    covered: set[str] = set()
    rules: list[frozenset[str]] = []
    for font in opened:
        reach = font.reach(tags)
        if isinstance(reach, str):
            return reach
        covered |= reach[0]
        rules.extend(reach[1])
    chosen = _trigger_families(covered, rules)
    letters: set[int] = set()
    for font in opened:
        points, refusal = font.premise(frozenset(chosen))
        if refusal is not None:
            return refusal
        letters |= points
    return chosen, frozenset(chr(point) for point in letters)


def derive_fold(fonts: Sequence[Path | str], configs: Iterable[str]) -> ConfigFold:
    """Derive each of `configs`'s trigger letters over `fonts` (the module docstring has the checks), with `default` left out. Each font is opened lazily, so only the lookups the checks read are decompiled."""
    opened = [_Font(path) for path in fonts]
    asked: list[str] = []
    triggers: dict[str, frozenset[str]] = {}
    families: dict[str, tuple[str, ...]] = {}
    refused: dict[str, str] = {}
    try:
        for config in configs:
            if config == DEFAULT or config in asked:
                continue
            asked.append(config)
            derived = _derive_one(opened, frozenset(features_for(config)))
            if isinstance(derived, str):
                refused[config] = derived
            else:
                families[config], triggers[config] = derived
    finally:
        for font in opened:
            font.close()
    return ConfigFold(tuple(asked), triggers, families, refused)
