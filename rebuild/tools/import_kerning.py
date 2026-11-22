"""Carry the old font's Senior kerning over into the rune files' `kern:` blocks (rebuild/KERN-PLAN.md section 8), as a starting point the author then refines in the review app's kerning view.

Usage::

    uv run python -m rebuild.tools.import_kerning            # dry run: print the summary, write only the report
    uv run python -m rebuild.tools.import_kerning --apply    # add the records, update var/kern/imported.json, write the report

**Evidence.** The importer works from the rebuild's side. It resolves the rune files over the tables and glyphs on disk as the review server's kerning routes do (`kern_routes.KernRoutes.context`), so it never runs the kernel, and evaluates `glyph_data/senior_quikscript_kerning.yaml` on old glyph pairs with the oracle's evaluator (`oracle_positions.KernEvaluator`), which sums every rule that matches, as the old font's separate lookups did. A facing pair's old glyph pairs come from three sources, and the report says which gave each value:

- `aliases`: for every member cell pair, each cell's old names from `rebuild/m1-aliases.yaml` (`labels.load_alias_map`).
- `siblings`: a cell the alias map does not name takes its siblings' names (`OldNames`). The alias map names a glyph whose stroke dangles (`qsNo.alt.en-y0.ex-y0`) as the cell that takes the stroke up, while the old font draws that one glyph whether the stroke is taken up or not, so a leading break cell takes the names of the cells that differ from it only in the exit, and a following one those that differ only in the entry.
- `baseline`: the old glyphs the old font drew at the facing pair's witness break, from the old baseline's row for the witness text (`read_witnesses`, over the inventory's witnesses and `baseline-<config>.subset.tsv.gz`), where the baseline holds that row and the old font breaks there. A witness whose old glyphs draw another shape than the rebuild's cells on the sides that face the break (another rune or stance, or other facing-side adjustments) is no evidence, and the report lists it (`witnesses_of_other_shapes`).

A member with no old name on a side is no evidence. Only the sidecar's pair rules between two Quikscript letters are read: a `global` document or a `right_group` (ZWNJ) rule is left out, and the Latin rules and the ZWNJ rule under `kerning:` in `glyph_data/opentype-features.yaml` are never read. A facing pair whose evidence is one value takes it; one with several gets none and is reported with its members, their values and the baseline's; one with no evidence gets none.

**Records.** Per rune pair, when every facing pair with evidence carries one nonzero value, every old rule that reaches a glyph pair of the two families is a plain `left_family` by `right_family` rule over bare family names, and no facing pair of the two is decided by a record or was set by an earlier import, the importer writes one letters-scope record, which also covers the rune pair's facing pairs with no evidence, as the old family rule did. A rune pair that a rule with a stance side, an exact-name side, a side left out, a carve-out (`except_left`, `except_right`) or a dotted family name reaches is narrowed (`OldRules.narrowing`): a letters-scope record would carry the value to shapes the author excluded, so the rune pair takes only facing-pair-scope records, one for each facing pair whose evidence is one nonzero value, and its facing pairs with no evidence take nothing; the report lists the narrowed rune pairs. Otherwise each such facing pair takes a facing-pair-scope record. A value of 0 writes nothing, so a ligature, which no old rule names, takes what its last letter's records give it through inheritance; the report lists each facing pair whose value differs from its evidence. Records are built with the editor's own machinery (`kern_writer.Scope.record`, `kern_writer.canonical`, `kern_writer.render_block`), and no `why` is ever written.

**Only adding.** The importer never removes or rewrites a record that is in a file, whoever wrote it, and never sets a pair again that it set before: `imported.json` keeps every pair it ever set, each with its record's line and its state now (`in place`, `overridden` when another record decides the pair, `record gone`), so a later run adds records only for facing pairs that no record decides and that it never set, as a migrated letter's pairs. A pair with an old value it skips for either reason is listed in the report (`skipped`), and so is each facing pair whose record, wherever it came from, gives another value than the evidence (`against_records`). A rune file whose block the editor cannot write back exactly is skipped whole. `imported.json` that is there but cannot be read refuses the import and stays as it is; a missing one while the rune files hold records refuses `--apply` too, unless `--no-earlier-import` says no import has run in this checkout.

**Validation.** The whole result is checked as the editor checks an edit. In memory first (`KernRoutes.resolve_with`, the defect gate's contact check at the kerned offset per facing pair, `emit_gpos.kern_plan`): a record the stage refuses (its provenance named in a `KernError`), one that puts ink on ink (`defects.check_kerned_breaks` under `rebuild/m1-contact-allow.yaml`), or one the kern lookup refuses (the glyph a `KernPlanError` names) is left out and reported with the build's message. A refused letters-scope record gives way to facing-pair records for the facing pairs with evidence that pass. This repeats until what remains resolves; then the editor's own check (`KernRoutes.check`: the spec loader over a temporary copy of the runes directory, the stage, the lookup's plan and the contact check) runs over every file to be written, and a refusal it names a record in goes round again.

**The report** (`var/kern/import-report.json`, format `REPORT_FORMAT`) holds the counts, the evidence's sources among them; the records this run adds with each facing pair's sources; the disagreements; the skipped pairs and files; `against_records`; the refused records with the build's messages; the facing pairs whose value differs from their evidence, and why; the facing pairs with no evidence a letters-scope record sets (`covered`); the narrowed rune pairs; the witnesses drawn with other shapes; the earlier imports no longer in place; each joined group of the rebuild whose old glyph pairs, as the alias map names them, the old rules move, with the old values (dropped, since kerning never moves a join); and each old rule that sets nothing in the rebuild, with why: its value is 0, its letters have no rune file, no glyph it names has an alias, its glyph pairs only join or never meet in the rebuild (said only when every member of every facing pair of its letters has an old name, and otherwise that it is not known), or every facing pair it reaches disagrees, already has a record or was set before, was refused, or has rules that bring its sum to 0.

**Writing.** The dry run prints the summary and writes only the report. `--apply` holds the cycle's pass lock (`var/cycle/pass.lock`) without waiting, as the review server's writes do, and refuses while a pass holds it or while the tables on disk are not the ones the sources give (run the M1 build first). It plans under the lock and lists the pairs it is about to set in `imported.json` (format `IMPORTED_FORMAT`) as `pending` before the first rune file changes; it then writes each rune file whose block changes atomically through a staging file beside its outputs (`kern_writer.replace_text`), marks the pairs of the files it wrote `in place`, and writes the report. A write that fails stops the run, which says which files it wrote; the pairs of the others stay `pending`, and a later run, finding their records missing, takes them as never set. The review server reads `imported.json` to mark the pairs carried over. Nothing here is in the build's or the corpus's code keys: no build stage imports it.

`--runes DIR` reads and writes another runes directory, a copy, over the tables and glyphs of `--m1-out`; `--out DIR` puts the outputs elsewhere than `var/kern/`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import yaml

from rebuild.pipeline import defects, emit_gpos, kern, kern_inventory, labels, oracle_positions, output_sink
from rebuild.pipeline.model import CellId, ResolvedSpec, parse_adjustment
from rebuild.review import kern_routes, kern_writer, store_lock

REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR = REPO_ROOT / "glyph_data" / "senior_quikscript_kerning.yaml"
ALIASES = REPO_ROOT / "rebuild" / "m1-aliases.yaml"
RUNES_DIR = REPO_ROOT / "glyph_data" / "runes"
M1_OUT = REPO_ROOT / "rebuild" / "out" / "m1"
OUT_DIR = REPO_ROOT / "var" / "kern"
PASS_LOCK = REPO_ROOT / "var" / "cycle" / "pass.lock"
IMPORTED_NAME = "imported.json"
REPORT_NAME = "import-report.json"
IMPORTED_FORMAT = "ams-kern-imported/2"
IMPORTED_FORMATS = ("ams-kern-imported/1", IMPORTED_FORMAT)
REPORT_FORMAT = "ams-kern-import-report/1"
ROUNDS = (
    100  # the most validation rounds before the importer gives up; each round leaves at least one record out
)
_PROVENANCE = re.compile(r"(\S+):(kern\[\d+\])")

PairKey = tuple[str, str]
OldPair = tuple[str, str]


class ImportRefused(Exception):
    """The import cannot run, or cannot write; the message says why and what to do."""


def _carved(glyph: str, prefixes: Iterable[str]) -> bool:
    return any(glyph == prefix or glyph.startswith(prefix + ".") for prefix in prefixes)


def _side_matches(rule: Mapping, glyph: str, side: str) -> bool:
    """Return whether one side of an old rule matches `glyph`, carve-out applied, as `oracle_positions.KernEvaluator` matches it."""
    match = oracle_positions.KernEvaluator._side_matches
    if f"{side}_family" in rule:
        found = match(glyph, rule[f"{side}_family"], "family")
    elif f"{side}_stance" in rule:
        found = match(glyph, rule[f"{side}_stance"], "stance")
    elif side in rule:
        found = match(glyph, rule[side], "exact")
    else:
        found = True
    return found and not _carved(glyph, rule.get(f"except_{side}", ()))


@dataclass
class OldRules:
    """The sidecar's pair rules with their index among its documents, the oracle's evaluator over exactly those, and the documents left out with why."""

    evaluator: oracle_positions.KernEvaluator
    rules: list[tuple[int, dict]]
    left_out: list[dict]

    def narrowing(self, lead: str, follow: str) -> list[tuple[int, dict]]:
        """Return the rules that reach a glyph pair of the two families and are not a plain `left_family` by `right_family` rule over bare family names: a stance side, an exact-name side, a side left out, a carve-out, or a family name with a dot in it, which names one stance. Such a rule tells some shapes of a family apart, so the two families take no letters-scope record. A rule reaches the two families when its left side names `lead` (or names no glyph, reaching every family) and its right side names `follow` likewise, by the head of each name it lists."""
        found = []
        for index, rule in self.rules:
            reaches = all(
                not any(key in rule for key in (f"{side}_family", f"{side}_stance", side))
                or family
                in {
                    name.split(".")[0]
                    for key in (f"{side}_family", f"{side}_stance", side)
                    for name in rule.get(key, ())
                }
                for side, family in (("left", lead), ("right", follow))
            )
            plain = (
                set(rule) <= {"left_family", "right_family", "value"}
                and "left_family" in rule
                and "right_family" in rule
                and not any("." in name for key in ("left_family", "right_family") for name in rule[key])
            )
            if reaches and not plain:
                found.append((index, rule))
        return found

    def sides(self, names: Iterable[str]) -> dict[int, tuple[frozenset[str], frozenset[str]]]:
        """Return, for each pair rule, the old glyph names among `names` its left side matches and those its right side matches, so it matches a glyph pair exactly when both glyphs are in its sets."""
        names = sorted(set(names))
        return {
            index: (
                frozenset(name for name in names if _side_matches(rule, name, "left")),
                frozenset(name for name in names if _side_matches(rule, name, "right")),
            )
            for index, rule in self.rules
        }


def load_old_rules(path: Path) -> OldRules:
    """Read the sidecar, keep its pair rules between two Quikscript letters, and give the oracle's evaluator exactly those."""
    documents = [
        document for document in yaml.safe_load_all(Path(path).read_text()) if isinstance(document, dict)
    ]
    rules: list[tuple[int, dict]] = []
    left_out: list[dict] = []
    for index, document in enumerate(documents):
        if "global" in document:
            left_out.append({"index": index, "rule": document, "reason": "a global value, not a pair rule"})
        elif "right_group" in document:
            left_out.append(
                {
                    "index": index,
                    "rule": document,
                    "reason": "a ZWNJ rule, which the rebuild does not carry over",
                }
            )
        else:
            rules.append((index, document))
    evaluator = oracle_positions.KernEvaluator(Path(path))
    evaluator.rules = [document for _index, document in rules]
    evaluator.global_value = 0
    return OldRules(evaluator, rules, left_out)


def old_names(aliases: Mapping[str, CellId | str]) -> dict[CellId, list[str]]:
    """Return each cell's old glyph names, the alias map inverted."""
    names: dict[CellId, list[str]] = defaultdict(list)
    for name, cell in aliases.items():
        if isinstance(cell, CellId):
            names[cell].append(name)
    return {cell: sorted(found) for cell, found in names.items()}


def side_adjustments(cell: CellId, side: str) -> tuple[str, ...]:
    """Return the cell's adjustments on one side, `en` (the entry) or `ex` (the exit). `locked` is on neither: a cell after a ZWNJ is drawn as the word-initial cell it copies, and the alias map names the old `.noentry` glyphs with or without it."""
    found = []
    for token in cell.adjustments:
        operation, token_side, _argument = parse_adjustment(token)
        if operation != "locked" and token_side == side:
            found.append(token)
    return tuple(found)


def _lead_shape(cell: CellId) -> tuple:
    return (cell.rune, cell.stance, cell.entry, side_adjustments(cell, "en"))


def _follow_shape(cell: CellId) -> tuple:
    return (cell.rune, cell.stance, cell.exit, side_adjustments(cell, "ex"))


class OldNames:
    """The old font's names for the rebuild's cells: the alias map's (`ALIASES`), and for a cell it names nothing, its siblings' names. The alias map names a glyph whose stroke dangles (`qsNo.alt.en-y0.ex-y0`) as the cell that takes the stroke up, while the old font draws that one glyph whether the stroke is taken up or not, so a leading cell it does not name takes the names of the cells that differ from it only in the exit (the same rune, stance, entry and entry-side adjustments), and a following cell the names of those that differ only in the entry. Among the siblings, those whose other side's adjustments are the cell's own are preferred."""

    def __init__(self, aliases: Mapping[str, CellId | str]) -> None:
        self.aliases = aliases
        self.names = old_names(aliases)
        self._leading: dict[tuple, list[CellId]] = defaultdict(list)
        self._following: dict[tuple, list[CellId]] = defaultdict(list)
        for cell in self.names:
            self._leading[_lead_shape(cell)].append(cell)
            self._following[_follow_shape(cell)].append(cell)

    def _of(self, cell: CellId, siblings: list[CellId], other: str) -> tuple[list[str], str | None]:
        if cell in self.names:
            return self.names[cell], "aliases"
        preferred = [one for one in siblings if side_adjustments(one, other) == side_adjustments(cell, other)]
        found = sorted({name for one in preferred or siblings for name in self.names[one]})
        return found, "siblings" if found else None

    def lead(self, cell: CellId) -> tuple[list[str], str | None]:
        """Return the old names of a leading cell and where they came from (`aliases`, `siblings`, or None for none)."""
        return self._of(cell, self._leading.get(_lead_shape(cell), []), "ex")

    def follow(self, cell: CellId) -> tuple[list[str], str | None]:
        return self._of(cell, self._following.get(_follow_shape(cell), []), "en")

    def lead_shape_of(self, name: str, cell: CellId) -> bool:
        """Return whether the old glyph `name` draws the leading cell's shape on the side that faces the break: its alias is a cell of the same rune and stance with the same exit-side adjustments. How the old glyph was entered, and whether its exit was taken up, do not move that edge."""
        alias = self.aliases.get(name)
        return isinstance(alias, CellId) and (alias.rune, alias.stance, side_adjustments(alias, "ex")) == (
            cell.rune,
            cell.stance,
            side_adjustments(cell, "ex"),
        )

    def follow_shape_of(self, name: str, cell: CellId) -> bool:
        """Return whether the old glyph `name` draws the following cell's shape on the side that faces the break: the same rune and stance with the same entry-side adjustments."""
        alias = self.aliases.get(name)
        return isinstance(alias, CellId) and (alias.rune, alias.stance, side_adjustments(alias, "en")) == (
            cell.rune,
            cell.stance,
            side_adjustments(cell, "en"),
        )


@dataclass(frozen=True)
class Witness:
    """What the old font drew at a facing pair's witness break, from the old baseline's row for the witness text: the two old glyph names, and the rebuild's two cells there."""

    old_lead: str
    old_follow: str
    lead: CellId
    follow: CellId


def read_witnesses(
    inventory: Path,
    m1_out: Path,
    tables_stamp: str | None,
    cells_by_label: Mapping[str, CellId],
    spec: ResolvedSpec,
) -> tuple[dict[PairKey, Witness], str | None]:
    """Return the old glyph names drawn at each facing pair's witness break, by facing pair, from the old baseline's subset rows (`baseline-<config>.subset.tsv.gz` beside the tables), and why none were read, if so. A witness counts when the baseline holds a row for its text in its configuration and the old font breaks between the two glyphs that hold the break's code points; a witness the old font joins there, or forms one glyph across, gives nothing. The inventory must have been built over the tables on disk."""
    try:
        document = json.loads(Path(inventory).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}, f"no {inventory.name} to read the witness texts from"
    if (document.get("stamp") or {}).get("tables") != tables_stamp:
        return {}, f"{inventory.name} was built over other tables than the ones on disk"
    runes = [rune["name"] for rune in document["runes"]]
    glyphs = document["glyphs"]
    configs = document["configs"]
    wanted: dict[tuple[str, str], list[tuple[dict, dict]]] = defaultdict(list)
    for pair in document["pairs"]:
        if pair.get("witness") is None:
            continue
        text = document["texts"][pair["witness"]]
        window = ":".join(f"{codepoint:04X}" for codepoint in text["cp"])
        wanted[(configs[text["config"]], window)].append((pair, text))
    rows: dict[tuple[str, str], list[str]] = {}
    for config in sorted({config for config, _window in wanted}):
        path = Path(m1_out) / f"baseline-{config}.subset.tsv.gz"
        if not path.is_file():
            continue
        keys = {window for found_config, window in wanted if found_config == config}
        with gzip.open(path, "rt") as handle:
            for line in handle:
                key = line.split("\t", 1)[0]
                if key in keys:
                    rows[(config, key)] = line.rstrip("\n").split("\t")
    found: dict[PairKey, Witness] = {}
    for where, entries in wanted.items():
        row = rows.get(where)
        if row is None or len(row) < 4:
            continue
        old = row[1].split("|")
        clusters = [int(value) for value in row[2].split(",")]
        junctions = row[3].split(",")
        for pair, text in entries:
            spans: list[tuple[int, int]] = []
            codepoint = 0
            for index, glyph in enumerate(text["glyphs"]):
                rune = runes[glyphs[glyph]["rune"]]
                sequence = spec.runes[rune].sequence if rune in spec.runes else None
                width = len(sequence) if sequence else 1
                spans.append((codepoint, codepoint + width - 1))
                codepoint += width + (index in text["zwnj"])
            at = pair["at"]
            last, first = spans[at][1], spans[at + 1][0]
            if first not in clusters:
                continue
            follow_index = clusters.index(first)
            lead_index = max(index for index, cluster in enumerate(clusters) if cluster <= last)
            if lead_index >= follow_index or any(
                junctions[index] != "break" for index in range(lead_index, follow_index)
            ):
                continue
            lead = cells_by_label.get(glyphs[text["glyphs"][at]]["name"])
            follow = cells_by_label.get(glyphs[text["glyphs"][at + 1]]["name"])
            if lead is None or follow is None:
                continue
            key = (document["sides"][pair["lead"]]["name"], document["sides"][pair["follow"]]["name"])
            found[key] = Witness(old[lead_index], old[follow_index], lead, follow)
    return found, None


@dataclass
class Evidence:
    """What the old rules say about one facing pair: every value its named members take, each named member's old glyph pairs with their values, those old glyph pairs, where the names came from (`aliases`, `siblings`, `baseline`), and the old glyphs drawn at its witness break, when the baseline gives them."""

    values: set[int] = field(default_factory=set)
    members: list[dict] = field(default_factory=list)
    old_pairs: set[OldPair] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    baseline: dict | None = None

    @property
    def value(self) -> int | None:
        return next(iter(self.values)) if len(self.values) == 1 else None


def gather_evidence(
    resolution: kern.KernResolution,
    names: OldNames,
    rules: OldRules,
    label: Mapping[CellId, str],
    witnesses: Mapping[PairKey, Witness] | None = None,
) -> tuple[dict[PairKey, Evidence], list[dict]]:
    """Return the evidence of every facing pair, by its two side names, as the module docstring says, and the witnesses whose old glyphs draw other shapes than the rebuild's cells there, which are no evidence. A facing pair nothing names has no evidence."""
    found: dict[PairKey, Evidence] = {}
    other_shapes: list[dict] = []
    for pair in resolution.pairs:
        key = (pair.leading.name, pair.following.name)
        evidence = Evidence()
        witness = (witnesses or {}).get(key)
        if witness is not None:
            value = rules.evaluator.value_for(witness.old_lead, witness.old_follow)
            drawn = {
                "old": f"{witness.old_lead} {witness.old_follow}",
                "cells": f"{label.get(witness.lead, str(witness.lead))} | {label.get(witness.follow, str(witness.follow))}",
                "value": value,
            }
            if names.lead_shape_of(witness.old_lead, witness.lead) and names.follow_shape_of(
                witness.old_follow, witness.follow
            ):
                evidence.values.add(value)
                evidence.old_pairs.add((witness.old_lead, witness.old_follow))
                evidence.sources.add("baseline")
                evidence.baseline = drawn
            else:
                other_shapes.append({"lead": key[0], "follow": key[1], **drawn})
        for left, right in pair.members:
            (left_names, left_source), (right_names, right_source) = names.lead(left), names.follow(right)
            if not left_names or not right_names:
                continue
            values = {(a, b): rules.evaluator.value_for(a, b) for a in left_names for b in right_names}
            evidence.values |= set(values.values())
            evidence.old_pairs |= set(values)
            evidence.sources |= {left_source or "", right_source or ""} - {""}
            evidence.members.append(
                {
                    "lead": label.get(left, str(left)),
                    "follow": label.get(right, str(right)),
                    "names": sorted({left_source or "", right_source or ""} - {""}),
                    "values": {f"{a} {b}": value for (a, b), value in sorted(values.items())},
                }
            )
        if evidence.values:
            found[key] = evidence
    return found, other_shapes


@dataclass(eq=False)
class Plan:
    """One record the import would write: the rune whose file holds it, its scope, its value, and the facing pairs whose evidence it carries."""

    rune: str
    scope: kern_writer.Scope
    by: int
    pairs: list[PairKey]

    def record(self):
        return self.scope.record(self.by, None)

    @property
    def line(self) -> str:
        return kern_writer.record_line(self.record())


@dataclass
class Outcome:
    """One import's result: the rune files' texts it would write (only those that change), the files as it found them, the entries of `imported.json`, and the report."""

    texts: dict[str, str]
    current: dict[str, str]
    imported: list[dict]
    report: dict


def _key_text(key: PairKey) -> str:
    return f"{key[0]} | {key[1]}"


def read_imported(out_dir: Path) -> list[dict] | None:
    """Return every facing pair the importer has set, as `imported.json` lists them, or None when there is no such file. A file that is there but cannot be read raises `ImportRefused`: the importer must not take its own records for the author's, nor replace the file."""
    path = Path(out_dir) / IMPORTED_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ImportRefused(
            f"{path} cannot be read ({error}); fix it or restore it from a backup before importing"
        ) from None
    try:
        document = json.loads(text)
    except ValueError as error:
        raise ImportRefused(
            f"{path} is not JSON ({error}); fix it or restore it from a backup before importing"
        ) from None
    if (
        not isinstance(document, dict)
        or document.get("format") not in IMPORTED_FORMATS
        or not isinstance(document.get("pairs"), list)
        or not all(
            isinstance(entry, dict)
            and all(isinstance(entry.get(key), str) for key in ("lead", "follow", "rune", "line"))
            and isinstance(entry.get("by"), int)
            for entry in document["pairs"]
        )
    ):
        raise ImportRefused(
            f"{path} is not a list of imported pairs the importer reads; fix it or restore it from a backup before importing"
        )
    return document["pairs"]


class Importer:
    """One import over the routes' runes directory and tables, its outputs under `out_dir`."""

    def __init__(
        self, routes: kern_routes.KernRoutes, out_dir: Path, sidecar: Path = SIDECAR, aliases: Path = ALIASES
    ) -> None:
        self.routes = routes
        self.out_dir = Path(out_dir)
        self.rules = load_old_rules(sidecar)
        self.aliases = labels.load_alias_map(aliases)
        self.names = OldNames(self.aliases)
        self.evidence: dict[PairKey, Evidence] = {}
        self.narrowed: list[dict] = []
        self.other_shapes: list[dict] = []
        self.witness_note: str | None = None
        self.prior: list[dict] = []

    def _file(self, rune: str) -> str:
        return kern_routes.file_label(self.routes.runes_dir / f"{rune}.yaml")

    def plan(self, *, no_earlier_import: bool = False) -> Outcome:
        """Find the evidence, plan the records the import adds, leave out what the validation refuses, and return the outcome. Raises `ImportRefused` when `imported.json` cannot be read, or is missing while the rune files hold records (unless `no_earlier_import` says no import has run)."""
        routes = self.routes
        context = routes.context()
        spec = context.spec
        label = {cell: record.name for cell, record in context.glyphs.items()}
        cells_by_label = {name: cell for cell, name in label.items()}
        order: Callable[[str], tuple[int, ...]] = lambda rune: kern.rune_order_key(spec, rune)  # noqa: E731
        prior = read_imported(self.out_dir)
        current: dict[str, str] = {}
        base: dict[str, list] = {}
        unwritable: dict[str, str] = {}
        for path in sorted(routes.runes_dir.glob("*.yaml")):
            if path.stem not in spec.runes:
                continue
            text = kern_writer.read_text(path)
            current[path.stem] = text
            try:
                base[path.stem] = kern_writer.parse_block(
                    kern_writer.split_file(text)[1], self._file(path.stem), kern_writer.line_ending(text)
                )
            except kern_writer.BlockRefused as refusal:
                unwritable[path.stem] = str(refusal)
        if prior is None and not no_earlier_import and any(base.values()):
            raise ImportRefused(
                f"the rune files hold kern records but {self.out_dir / IMPORTED_NAME} is missing, so the importer cannot tell the pairs it set before from the author's; restore the file, or pass --no-earlier-import if no import has run in this checkout"
            )
        resolution = context.before
        if resolution is None:
            raise ImportRefused(
                "the rune files' kern blocks do not resolve over the tables on disk; fix them first"
            )
        _tables, stamp, _error = routes.tables()
        witnesses, self.witness_note = read_witnesses(
            routes.m1_out / kern_inventory.KERN_INVENTORY_JSON, routes.m1_out, stamp, cells_by_label, spec
        )
        self.evidence, self.other_shapes = gather_evidence(
            resolution, self.names, self.rules, label, witnesses
        )
        evidence = self.evidence

        lines_in_files = {
            rune: {kern_writer.record_line(record) for record in records} for rune, records in base.items()
        }
        current_values = {(pair.leading.name, pair.following.name): pair.by for pair in resolution.pairs}
        self.prior = []
        for entry in prior or []:
            present = entry["line"] in lines_in_files.get(entry["rune"], set())
            if entry.get("state") == "pending" and not present:
                continue  # an import that stopped before writing this record: the pair was never set
            key = (entry["lead"], entry["follow"])
            state = (
                "in place"
                if present and current_values.get(key) == entry["by"]
                else "overridden" if present else "record gone"
            )
            self.prior.append({**entry, "state": state})
        before = {(entry["lead"], entry["follow"]) for entry in self.prior}

        decided: dict[PairKey, list[str]] = {
            (pair.leading.name, pair.following.name): [str(decider) for decider in pair.deciders]
            for pair in resolution.pairs
            if pair.deciders
        }
        rune_pairs: dict[PairKey, list[PairKey]] = defaultdict(list)
        for pair in resolution.pairs:
            rune_pairs[(pair.leading.rune, pair.following.rune)].append(
                (pair.leading.name, pair.following.name)
            )
        plans: list[Plan] = []
        skipped_files: dict[str, str] = {}
        narrowed: list[dict] = []
        for (lead, follow), keys in sorted(
            rune_pairs.items(), key=lambda item: (order(item[0][0]), order(item[0][1]))
        ):
            narrowing = self.rules.narrowing(lead, follow)
            valued = [
                key
                for key in keys
                if key not in decided and key not in before and key in evidence and evidence[key].value
            ]
            if narrowing:
                narrowed.append(
                    {
                        "lead": lead,
                        "follow": follow,
                        "rules": [{"index": index, "rule": rule} for index, rule in narrowing],
                        "pairs": [_key_text(key) for key in valued],
                    }
                )
            if not valued:
                continue
            if lead in unwritable:
                skipped_files[lead] = unwritable[lead]
                continue
            seen = {evidence[key].value for key in keys if key in evidence}
            fresh = not any(key in decided or key in before for key in keys)
            if len(seen) == 1 and not narrowing and fresh:
                plans.append(
                    Plan(lead, kern_writer.letters_scope(lead, follow), evidence[valued[0]].value or 0, keys)
                )
                continue
            for key in valued:
                plans.append(Plan(lead, kern_writer.facing_scope(*key), evidence[key].value or 0, [key]))

        refused: list[dict] = []
        blocks, final, final_spec = self._validate(context, base, set(), plans, resolution, refused, label)
        texts: dict[str, str] = {}
        for rune, records in blocks.items():
            text = current[rune]
            rendered = kern_writer.render_block(records, kern_writer.line_ending(text))
            texts[rune] = kern_writer.splice(kern_writer.split_file(text)[0], rendered, self._file(rune))
        texts = {rune: text for rune, text in texts.items() if text != current[rune]}

        planned = {(plan.rune, plan.line): plan for plan in plans}
        imported: list[dict] = []
        final_values: dict[PairKey, int] = {}
        set_by: dict[PairKey, Plan] = {}
        for pair in final.pairs:
            key = (pair.leading.name, pair.following.name)
            final_values[key] = pair.by
            for decider in pair.deciders:
                record = decider.record
                if decider.inherited or record.rune not in blocks or record.provenance is None:
                    continue
                index = int(record.provenance.path[len("kern[") : -1])
                line = kern_writer.record_line(blocks[record.rune][index])
                plan = planned.get((record.rune, line))
                if plan is not None:
                    set_by[key] = plan
                    imported.append(
                        {
                            "lead": key[0],
                            "follow": key[1],
                            "by": pair.by,
                            "rune": record.rune,
                            "file": self._file(record.rune),
                            "scope": plan.scope.kind,
                            "line": line,
                            "sources": sorted(evidence[key].sources) if key in evidence else [],
                        }
                    )
        self.narrowed = narrowed
        report = self._report(
            context=context,
            spec=final_spec,
            resolution=resolution,
            final=final,
            decided=decided,
            before=before,
            plans=plans,
            refused=refused,
            imported=imported,
            final_values=final_values,
            current_values=current_values,
            set_by=set_by,
            skipped_files=skipped_files,
            label=label,
            order=order,
        )
        return Outcome(texts, current, imported, report)

    def _blocks(
        self, base: Mapping[str, list], runes: Iterable[str], plans: list[Plan], order
    ) -> dict[str, list]:
        return {
            rune: kern_writer.canonical(
                [*base[rune], *(plan.record() for plan in plans if plan.rune == rune)], order
            )
            for rune in sorted(set(runes) | {plan.rune for plan in plans})
        }

    def _validate(
        self,
        context,
        base: Mapping[str, list],
        set_aside: set[str],
        plans: list[Plan],
        before: kern.KernResolution,
        refused: list[dict],
        label: Mapping[CellId, str],
    ) -> tuple[dict[str, list], kern.KernResolution, ResolvedSpec]:
        """Leave out every planned record the build would refuse, as the module docstring says, editing `plans` in place and listing each refusal in `refused`, and return the blocks of what remains with their resolution and spec."""
        routes = self.routes
        spec = context.spec
        order = lambda rune: kern.rune_order_key(spec, rune)  # noqa: E731
        allow = defects.read_allow(routes.allow_path) if routes.allow_path.is_file() else frozenset()
        before_values = {(pair.leading.name, pair.following.name): pair.by for pair in before.pairs}
        for _round in range(ROUNDS):
            blocks = self._blocks(base, set_aside, plans, order)
            by_record = {(plan.rune, kern_writer.record_line(plan.record())): plan for plan in plans}
            provenance: dict[str, Plan] = {}
            for rune, records in blocks.items():
                for index, record in enumerate(records):
                    plan = by_record.get((rune, kern_writer.record_line(record)))
                    if plan is not None:
                        provenance[f"{self._file(rune)}:kern[{index}]"] = plan
            try:
                resolution = routes.resolve_with(context, blocks)
            except kern.KernError as error:
                self._refuse_named(plans, error.issues, provenance, "the kern stage refuses it", refused)
                continue
            records_of = {**resolution.realized, **context.glyphs}
            failing: dict[int, list[tuple[PairKey, list[list[str]]]]] = defaultdict(list)
            for pair in resolution.pairs:
                key = (pair.leading.name, pair.following.name)
                if pair.by == before_values.get(key, 0):
                    continue
                checked = defects.check_kerned_breaks(
                    allow, records_of, {member: pair.by for member in pair.members}
                )
                if not checked.errors:
                    continue
                issues = [
                    [defect.code, f"[{defect.signature}]: {defect.message}"] for defect in checked.errors
                ]
                for decider in pair.deciders:
                    plan = provenance.get(str(decider.record.provenance))
                    if plan is not None:
                        failing[id(plan)].append((key, issues))
            if failing:
                for plan in [one for one in plans if id(one) in failing]:
                    self._refuse_pairs(plans, plan, failing[id(plan)], refused)
                continue
            try:
                emit_gpos.kern_plan(resolution, context.glyphs)
            except emit_gpos.KernPlanError as error:
                glyph = str(error).split(" ", 1)[0]
                hit = {
                    id(plan): plan
                    for pair in resolution.pairs
                    if any(label.get(cell) == glyph for member in pair.members for cell in member)
                    for decider in pair.deciders
                    if (plan := provenance.get(str(decider.record.provenance))) is not None
                }
                if not hit:
                    raise ImportRefused(f"the kern lookup refuses the result: {error}") from None
                for plan in hit.values():
                    self._refuse(
                        plans, plan, "the kern lookup refuses it", [["KernPlanError", str(error)]], refused
                    )
                continue
            if not blocks:
                return blocks, resolution, spec
            texts = {}
            for rune, records in blocks.items():
                text = kern_writer.read_text(routes.runes_dir / f"{rune}.yaml")
                rendered = kern_writer.render_block(records, kern_writer.line_ending(text))
                texts[rune] = kern_writer.splice(kern_writer.split_file(text)[0], rendered, self._file(rune))
            try:
                checked_spec, checked_resolution = routes.check(context, texts)
            except kern_routes.Refused as refusal:
                self._refuse_named(plans, refusal.issues, provenance, refusal.message, refused)
                continue
            return blocks, checked_resolution, checked_spec
        raise ImportRefused(f"the validation did not settle in {ROUNDS} rounds")

    def _refuse_named(
        self,
        plans: list[Plan],
        issues: Iterable,
        provenance: Mapping[str, Plan],
        message: str,
        refused: list[dict],
    ) -> None:
        """Leave out each planned record an issue names by its provenance, or raise when an issue names none."""
        named: dict[int, tuple[Plan, list]] = {}
        issues = list(issues)
        for issue in issues:
            for match in _PROVENANCE.finditer(str(issue[1])):
                plan = provenance.get(f"{match.group(1)}:{match.group(2)}")
                if plan is not None:
                    named.setdefault(id(plan), (plan, []))[1].append(list(issue))
        if not named:
            raise ImportRefused(f"{message}, and names no record the import would write: {issues}")
        for plan, found in named.values():
            self._refuse(plans, plan, message, found, refused)

    @staticmethod
    def _refuse(plans: list[Plan], plan: Plan, message: str, issues: list, refused: list[dict]) -> None:
        if plan in plans:
            plans.remove(plan)
        refused.append(
            {
                "rune": plan.rune,
                "line": plan.line,
                "pairs": [_key_text(key) for key in plan.pairs],
                "message": message,
                "issues": issues,
            }
        )

    def _refuse_pairs(
        self, plans: list[Plan], plan: Plan, failing: list[tuple[PairKey, list]], refused: list[dict]
    ) -> None:
        """Leave out a record that puts ink on ink at some of its facing pairs; a letters-scope record gives way to facing-pair records for its other facing pairs with evidence."""
        bad = {key for key, _issues in failing}
        refused.append(
            {
                "rune": plan.rune,
                "line": plan.line,
                "pairs": [_key_text(key) for key in sorted(bad)],
                "message": "the defect gate refuses it: kerned ink would overlap",
                "issues": [issue for _key, issues in failing for issue in issues],
            }
        )
        index = plans.index(plan)
        plans.pop(index)
        if plan.scope.kind == kern_writer.LETTERS:
            plans[index:index] = [
                Plan(plan.rune, kern_writer.facing_scope(*key), plan.by, [key])
                for key in plan.pairs
                if key not in bad and key in self.evidence and self.evidence[key].value == plan.by
            ]

    def _report(
        self,
        *,
        context,
        spec: ResolvedSpec,
        resolution: kern.KernResolution,
        final: kern.KernResolution,
        decided: Mapping[PairKey, list[str]],
        before: set[PairKey],
        plans: list[Plan],
        refused: list[dict],
        imported: list[dict],
        final_values: Mapping[PairKey, int],
        current_values: Mapping[PairKey, int],
        set_by: Mapping[PairKey, Plan],
        skipped_files: Mapping[str, str],
        label: Mapping[CellId, str],
        order,
    ) -> dict:
        evidence = self.evidence
        refused_pairs = {pair for entry in refused for pair in entry["pairs"]}
        outcome: dict[PairKey, str] = {}
        in_place = {(entry["lead"], entry["follow"]) for entry in self.prior if entry["state"] == "in place"}
        for key, found in evidence.items():
            if found.value is None:
                outcome[key] = "disagree"
            elif key in set_by and final_values.get(key) == found.value:
                outcome[key] = "imported"
            elif key in in_place and current_values.get(key) == found.value:
                outcome[key] = "imported before"
            elif key in decided or key in before:
                outcome[key] = "skipped"
            elif _key_text(key) in refused_pairs:
                outcome[key] = "refused"
            elif found.value == 0:
                outcome[key] = "zero"
            else:
                outcome[key] = "differs"

        def sort_key(key: PairKey) -> tuple:
            return (order(key[0].split("[")[0]), key[0], order(key[1].split("[")[0]), key[1])

        keys = sorted(evidence, key=sort_key)
        disagreements = [
            {
                "lead": key[0],
                "follow": key[1],
                "values": sorted(evidence[key].values),
                "baseline": evidence[key].baseline,
                "members": evidence[key].members,
            }
            for key in keys
            if outcome[key] == "disagree"
        ]
        skipped = []
        for key in keys:
            value = evidence[key].value
            if outcome[key] != "skipped" or not value:
                continue
            reasons = []
            if key in before:
                reasons.append("the import set it before, and its record has gone or been overridden since")
            if key in decided:
                reasons.append(f"a record decides it: {', '.join(decided[key])}")
            skipped.append(
                {
                    "lead": key[0],
                    "follow": key[1],
                    "old": value,
                    "now": current_values.get(key, 0),
                    "why": "; ".join(reasons),
                }
            )
        against_records = [
            {
                "lead": key[0],
                "follow": key[1],
                "old": evidence[key].value,
                "now": current_values.get(key, 0),
                "deciders": decided[key],
            }
            for key in keys
            if key in decided
            and evidence[key].value is not None
            and current_values.get(key, 0) != evidence[key].value
        ]
        inherited = {
            (pair.leading.name, pair.following.name)
            for pair in final.pairs
            if any(decider.inherited for decider in pair.deciders)
        }
        differs = []
        for key in keys:
            found = evidence[key]
            if (
                found.value is None
                or key in decided
                or key in before
                or final_values.get(key, 0) == found.value
            ):
                continue
            if _key_text(key) in refused_pairs:
                why = "its record was refused"
            elif key in inherited:
                why = "a ligature takes it from its last letter's records"
            elif key in set_by:
                why = f"the record for {', '.join(_key_text(one) for one in set_by[key].pairs)} covers it too"
            else:
                why = "its value comes from no record the import wrote"
            differs.append(
                {
                    "lead": key[0],
                    "follow": key[1],
                    "old": found.value,
                    "value": final_values.get(key, 0),
                    "why": why,
                }
            )

        cells_by_label = {name: cell for cell, name in label.items()}
        joined = []
        joined_pairs: set[OldPair] = set()
        for group in kern_inventory.joined_groups(spec, context.tables, cells_by_label):
            values: Counter[int] = Counter()
            sample: dict[int, str] = {}
            for left, right in group.members:
                for a in self.names.names.get(left, []):
                    for b in self.names.names.get(right, []):
                        value = self.rules.evaluator.value_for(a, b)
                        joined_pairs.add((a, b))
                        if value:
                            values[value] += 1
                            sample.setdefault(value, f"{a} {b}")
            if values:
                joined.append(
                    {
                        "lead": group.lead,
                        "lead_stance": group.lead_stance,
                        "follow": group.follow,
                        "follow_stance": group.follow_stance,
                        "height": group.height,
                        "configs": list(group.configs),
                        "members": len(group.members),
                        "old_values": {str(value): count for value, count in sorted(values.items())},
                        "old_pairs": [sample[value] for value in sorted(sample)],
                    }
                )

        rules = self._inert_rules(spec, resolution, outcome, joined_pairs)
        covered = [
            {"lead": entry["lead"], "follow": entry["follow"], "by": entry["by"], "line": entry["line"]}
            for entry in imported
            if (entry["lead"], entry["follow"]) not in evidence
        ]
        records = []
        for plan in plans:
            pairs = [
                {"pair": _key_text(key), "sources": sorted(evidence[key].sources) if key in evidence else []}
                for key in sorted((key for key, by in set_by.items() if by is plan), key=sort_key)
            ]
            records.append(
                {
                    "rune": plan.rune,
                    "file": self._file(plan.rune),
                    "scope": plan.scope.kind,
                    "line": plan.line,
                    "pairs": pairs,
                }
            )
        sources = Counter(
            source for key in keys if evidence[key].value is not None for source in evidence[key].sources
        )
        states = Counter(entry["state"] for entry in self.prior)
        counts = {
            "facing_pairs": len(resolution.pairs),
            "with_evidence": len(evidence),
            "without_evidence": len(resolution.pairs) - len(evidence),
            "evidence_sources": dict(sorted(sources.items())),
            "witnesses_of_other_shapes": len(self.other_shapes),
            "disagree": len(disagreements),
            "zero": sum(1 for value in outcome.values() if value == "zero"),
            "letters_records": sum(1 for plan in plans if plan.scope.kind == kern_writer.LETTERS),
            "facing_records": sum(1 for plan in plans if plan.scope.kind == kern_writer.FACING),
            "set_by_letters": sum(1 for entry in imported if entry["scope"] == kern_writer.LETTERS),
            "set_by_facing": sum(1 for entry in imported if entry["scope"] == kern_writer.FACING),
            "refused_records": len(refused),
            "skipped_pairs": len(skipped),
            "skipped_files": len(skipped_files),
            "against_records": len(against_records),
            "differs": len(differs),
            "covered_without_evidence": len(covered),
            "narrowed_rune_pairs": len(self.narrowed),
            "joined_groups_moved": len(joined),
            "old_rules": len(self.rules.rules),
            "old_rules_inert": len(rules),
            "imported_before": dict(sorted(states.items())),
            "in_place_before": sum(1 for value in outcome.values() if value == "imported before"),
        }
        return {
            "format": REPORT_FORMAT,
            "counts": counts,
            "records": records,
            "disagreements": disagreements,
            "skipped": skipped,
            "skipped_files": [
                {"rune": rune, "reason": reason} for rune, reason in sorted(skipped_files.items())
            ],
            "against_records": against_records,
            "refused": refused,
            "differs": differs,
            "covered": covered,
            "narrowed": self.narrowed,
            "witnesses_of_other_shapes": self.other_shapes,
            "baseline_not_read": self.witness_note,
            "imported_before": [entry for entry in self.prior if entry["state"] != "in place"],
            "joined": joined,
            "inert_rules": rules,
            "left_out": self.rules.left_out,
            "not_read": "the kerning rules of glyph_data/opentype-features.yaml (the Latin rules and the rule that tightens ·He before a ZWNJ)",
        }

    def _inert_rules(
        self,
        spec: ResolvedSpec,
        resolution: kern.KernResolution,
        outcome: Mapping[PairKey, str],
        joined_pairs: set[OldPair],
    ) -> list[dict]:
        """Return each old pair rule that sets nothing in the rebuild, with why. That its glyph pairs only join, or never meet, is said only when every member of every facing pair of its letters has an old name on both sides, so no break the rule could reach went unseen; otherwise the report says how many members have none."""
        names = [name for name, cell in self.aliases.items() if isinstance(cell, CellId)]
        sides = self.rules.sides(names)
        by_left: dict[str, list[int]] = defaultdict(list)
        for index, (left, _right) in sides.items():
            for name in left:
                by_left[name].append(index)
        values = dict(self.rules.rules)

        def matched(pair: OldPair) -> list[int]:
            found = [index for index in by_left.get(pair[0], ()) if pair[1] in sides[index][1]]
            total = sum(values[index].get("value", 0) for index in found)
            if total != self.rules.evaluator.value_for(*pair):
                raise ImportRefused(
                    f"the evaluator and the importer's rule matching disagree on {pair}; the sidecar uses a form the importer does not read"
                )
            return found

        unnamed: Counter[PairKey] = Counter()
        for pair in resolution.pairs:
            for left, right in pair.members:
                if not self.names.lead(left)[0] or not self.names.follow(right)[0]:
                    unnamed[(pair.leading.rune, pair.following.rune)] += 1
        reached: dict[int, set[str]] = defaultdict(set)
        for key, found in self.evidence.items():
            for pair in found.old_pairs:
                for index in matched(pair):
                    reached[index].add(outcome[key])
        joins: set[int] = {index for pair in joined_pairs for index in matched(pair)}

        def heads(rule: Mapping, side: str) -> set[str] | None:
            listed = [
                name for key in (f"{side}_family", f"{side}_stance", side) for name in rule.get(key, ())
            ]
            return {name.split(".")[0] for name in listed} if listed else None

        inert = []
        for index, rule in self.rules.rules:
            if reached[index] & {"imported", "imported before"} and rule.get("value", 0):
                continue
            left_heads, right_heads = heads(rule, "left"), heads(rule, "right")
            missing = sorted(
                head for head in (left_heads or set()) | (right_heads or set()) if head not in spec.runes
            )
            unseen = sum(
                count
                for (lead, follow), count in unnamed.items()
                if (left_heads is None or lead in left_heads)
                and (right_heads is None or follow in right_heads)
            )
            left, right = sides[index]
            outcomes = reached[index]
            if not rule.get("value", 0):
                reason = "its value is 0, an override that only cancels another rule"
            elif missing:
                reason = f"its letters have no rune file: {', '.join(missing)}"
            elif not left or not right:
                reason = "no glyph it names on one side has an alias in rebuild/m1-aliases.yaml"
            elif not outcomes and unseen:
                reason = f"it reaches no break the old names reach, and {unseen} member glyph pairs of its letters have no old name, so whether it reaches a break of the rebuild is not known"
            elif not outcomes and index in joins:
                reason = "its glyph pairs only join in the rebuild"
            elif not outcomes:
                reason = "its glyph pairs never meet in the rebuild"
            elif outcomes == {"disagree"}:
                reason = "every facing pair it reaches has members the old rules give different values"
            elif outcomes == {"skipped"}:
                reason = "every facing pair it reaches already has a record, or the import set it before"
            elif outcomes == {"refused"}:
                reason = "the validation refused the values it would set"
            elif outcomes == {"zero"}:
                reason = "the rules that also match its glyph pairs bring their sum to 0"
            else:
                reason = f"the facing pairs it reaches are {', '.join(sorted(outcomes))}"
            inert.append({"index": index, "rule": rule, "reason": reason})
        return inert


def summary_lines(report: dict, written: list[str] | None, runes_dir: Path) -> list[str]:
    """Return the lines the importer prints: what the plan sets, what it leaves for the author, and what it wrote (`written` None for a dry run)."""
    counts = report["counts"]
    reasons = Counter(entry["reason"] for entry in report["inert_rules"])
    refusals = Counter(entry["message"] for entry in report["refused"])
    lines = [
        f"import_kerning: {'dry run over' if written is None else 'applied to'} {runes_dir}",
        f"  facing pairs: {counts['facing_pairs']}; with old evidence {counts['with_evidence']} (0 for {counts['zero']}), without {counts['without_evidence']}; members disagree on {counts['disagree']}; evidence from {', '.join(f'{source} {count}' for source, count in counts['evidence_sources'].items())}; witnesses drawn with other shapes {counts['witnesses_of_other_shapes']}",
        f"  set: {counts['set_by_letters']} facing pairs by {counts['letters_records']} letters records, {counts['set_by_facing']} by {counts['facing_records']} facing-pair records; {counts['narrowed_rune_pairs']} pairs of letters narrowed to facing-pair records by a stance or carve-out rule",
        f"  refused: {counts['refused_records']} records"
        + (
            f" ({'; '.join(f'{count} {message}' for message, count in refusals.most_common())})"
            if refusals
            else ""
        ),
        f"  skipped: {counts['skipped_pairs']} facing pairs with old values a record decides or the import set before, {counts['skipped_files']} files the editor cannot write; {counts['in_place_before']} set by an earlier import are in place; {counts['against_records']} facing pairs' records differ from the old value",
        f"  end with another value than the old one: {counts['differs']} facing pairs; set with no old evidence, by a letters record: {counts['covered_without_evidence']}",
        f"  joined groups the old rules move (dropped): {counts['joined_groups_moved']}",
        f"  old rules that set nothing: {counts['old_rules_inert']} of {counts['old_rules']}"
        + (
            f" ({'; '.join(f'{count} {reason}' for reason, count in reasons.most_common())})"
            if reasons
            else ""
        ),
    ]
    if written is not None:
        lines.append(
            f"  wrote {len(written)} rune files"
            + (f": {', '.join(written)}" if written else " (nothing to change)")
        )
    return lines


def _write_json(path: Path, document: dict) -> bool:
    return output_sink.replace_output(
        path, json.dumps(document, indent=1, ensure_ascii=False) + "\n", keep_if_equal=True
    )


def run(
    *,
    runes_dir: Path = RUNES_DIR,
    m1_out: Path = M1_OUT,
    out_dir: Path = OUT_DIR,
    apply: bool = False,
    no_earlier_import: bool = False,
    pass_lock: Path = PASS_LOCK,
    sidecar: Path = SIDECAR,
    aliases: Path = ALIASES,
    repo_root: Path = REPO_ROOT,
    tables_inputs: Callable[[], str] | None = None,
    routes: kern_routes.KernRoutes | None = None,
    stream: TextIO = sys.stdout,
) -> int:
    """Run the import as the module docstring says, printing to `stream`, and return the exit status: 0, or 1 when it refuses or a write fails."""
    routes = routes or kern_routes.KernRoutes(repo_root, m1_out, tables_inputs, runes_dir=runes_dir)
    routes.pass_lock = Path(pass_lock)
    out_dir = Path(out_dir)
    imported_path = out_dir / IMPORTED_NAME
    try:
        importer = Importer(routes, out_dir, sidecar, aliases)
        _tables, stamp, error = routes.tables()
        current = routes.tables_current(stamp) if error is None else None
        if not apply:
            missing = read_imported(out_dir) is None
            outcome = importer.plan(no_earlier_import=True)
            _write_json(out_dir / REPORT_NAME, outcome.report)
            if current is False:
                print(
                    "import_kerning: the tables on disk are not the ones the sources give; run the M1 build before --apply",
                    file=stream,
                )
            if missing and any(kern_writer.split_file(text)[1] for text in outcome.current.values()):
                print(
                    f"import_kerning: {imported_path} is missing while the rune files hold records; this dry run takes them all for the author's, and --apply refuses without --no-earlier-import",
                    file=stream,
                )
            for line in summary_lines(outcome.report, None, routes.runes_dir):
                print(line, file=stream)
            print(f"  report: {out_dir / REPORT_NAME}", file=stream)
            return 0
        if current is not True:
            raise ImportRefused(
                f"the tables under {routes.m1_out} are not the ones the sources give ({error or 'stale'}); run the M1 build first"
            )
        try:
            with routes.hold_pass_lock():
                outcome = importer.plan(no_earlier_import=no_earlier_import)
                for rune in outcome.texts:
                    path = routes.runes_dir / f"{rune}.yaml"
                    if kern_writer.read_text(path) != outcome.current[rune]:
                        raise ImportRefused(f"{path} changed while the import ran; run it again")
                new = [{**entry, "state": "pending"} for entry in outcome.imported]
                # The pairs about to be set are listed before the first rune file changes, as pending; a run that stops partway leaves pending entries whose records never reached their files, which the next run takes as never set.
                _write_json(imported_path, {"format": IMPORTED_FORMAT, "pairs": [*importer.prior, *new]})
                written: list[str] = []
                failure: str | None = None
                for rune, text in outcome.texts.items():
                    path = routes.runes_dir / f"{rune}.yaml"
                    try:
                        kern_writer.replace_text(path, text, out_dir)
                    except OSError as error:
                        failure = f"writing {path} failed ({error})"
                        break
                    written.append(rune)
                done = [
                    {**entry, "state": "in place" if entry["rune"] in written else "pending"}
                    for entry in outcome.imported
                ]
                _write_json(imported_path, {"format": IMPORTED_FORMAT, "pairs": [*importer.prior, *done]})
                _write_json(out_dir / REPORT_NAME, outcome.report)
        except store_lock.LockBusy:
            holder = store_lock.holder(routes.pass_lock) or "unknown"
            raise ImportRefused(
                f"a cycle pass (pid {holder}) holds {routes.pass_lock}; run the import when it ends"
            ) from None
    except (ImportRefused, kern_routes.Refused) as refusal:
        print(f"import_kerning: {refusal}", file=stream)
        return 1
    names = [f"{rune}.yaml" for rune in written]
    if failure is not None:
        print(
            f"import_kerning: {failure}; it wrote {', '.join(names) or 'no rune file'} first, and {imported_path} lists the pairs it set there; run it again to add the rest",
            file=stream,
        )
        return 1
    for line in summary_lines(outcome.report, names, routes.runes_dir):
        print(line, file=stream)
    print(f"  wrote {imported_path} and {out_dir / REPORT_NAME}", file=stream)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Carry the old font's Senior kerning over into the rune files' kern: blocks; without --apply, a dry run that writes only the report."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="add the records to the kern blocks and update var/kern/imported.json",
    )
    parser.add_argument(
        "--no-earlier-import",
        action="store_true",
        help="state that no import has run in this checkout, so every kern record in the rune files is the author's; needed when var/kern/imported.json is missing while the files hold records",
    )
    parser.add_argument(
        "--runes",
        type=Path,
        default=RUNES_DIR,
        help="the runes directory to read and write (default: %(default)s)",
    )
    parser.add_argument(
        "--m1-out",
        type=Path,
        default=M1_OUT,
        help="the M1 output whose tables and glyphs are read (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR,
        help="where imported.json and the report go (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    return run(
        runes_dir=args.runes.absolute(),
        m1_out=args.m1_out.absolute(),
        out_dir=args.out.absolute(),
        apply=args.apply,
        no_earlier_import=args.no_earlier_import,
    )


if __name__ == "__main__":
    sys.exit(main())
