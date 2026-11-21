"""Carry the old font's Senior kerning over into the rune files' `kern:` blocks (rebuild/KERN-PLAN.md section 8), as a starting point the author then refines in the review app's kerning view.

Usage::

    uv run python -m rebuild.tools.import_kerning            # dry run: print the summary, write only the report
    uv run python -m rebuild.tools.import_kerning --apply    # write the kern blocks, var/kern/imported.json and the report

**Evidence.** The importer works from the rebuild's side. It resolves the rune files over the tables and glyphs on disk as the review server's kerning routes do (`kern_routes.KernRoutes.context`), so it never runs the kernel. For every facing pair it takes the member cell pairs, names each cell in the old font through `rebuild/m1-aliases.yaml` (every old glyph name the alias map gives the cell; `labels.load_alias_map`), and evaluates `glyph_data/senior_quikscript_kerning.yaml` on each (old leading glyph, old following glyph) pair with the oracle's evaluator (`oracle_positions.KernEvaluator`), which sums every rule that matches, as the old font's separate lookups did. A member with no old name on either side is no evidence. Only the sidecar's pair rules between two Quikscript letters are read: a `global` document or a `right_group` (ZWNJ) rule is left out, and the Latin rules and the ZWNJ rule under `kerning:` in `glyph_data/opentype-features.yaml` are never read. A facing pair whose evidence is one value takes it; one whose members carry different old values gets none and is reported with its members and their values; one with no evidence gets none.

**Records.** Per rune pair, when every facing pair with evidence carries one nonzero value and every old rule that reaches a glyph pair of the two families is a plain `left_family` by `right_family` rule, the importer writes one letters-scope record, which also covers the rune pair's facing pairs with no evidence, as the old family rule did. A rune pair that a rule with a stance side, an exact-name side, a side left out, or a carve-out (`except_left`, `except_right`) reaches is narrowed (`OldRules.narrowing`): such a rule tells some shapes of a family apart, and a letters-scope record would carry its value to shapes the author excluded, so the rune pair takes only facing-pair-scope records, one for each facing pair whose evidence is one nonzero value, and its facing pairs with no evidence take nothing; the report lists the narrowed rune pairs with the rules that narrowed them. Otherwise it writes a facing-pair-scope record for each facing pair with a nonzero value. A value of 0 writes nothing, so a ligature, which no old rule names, takes what its last letter's records give it through inheritance; the report lists each facing pair that ends with another value than its evidence. Records are built with the editor's own machinery (`kern_writer.Scope.record`, `kern_writer.canonical`, `kern_writer.render_block`), so the importer writes exactly what the editor would, and no `why` is ever written. A record already in a file is never touched: a facing pair one decides is skipped and reported, and a rune pair with one takes no letters-scope record, its other facing pairs taking facing-pair records. A rune file whose block the editor cannot write back exactly is skipped whole and reported. Records the importer wrote itself, listed in `imported.json` and still in their files as written, are set aside before planning and planned again, so a second `--apply` over the same inputs writes nothing.

**Validation.** The whole result is checked as the editor checks an edit. In memory first (`KernRoutes.resolve_with`, the defect gate's contact check at the kerned offset per facing pair, `emit_gpos.kern_plan`): a record the stage refuses (its provenance named in a `KernError`), one that puts ink on ink (`defects.check_kerned_breaks` under `rebuild/m1-contact-allow.yaml`), or one the kern lookup refuses (the glyph a `KernPlanError` names) is left out and reported with the build's message. A refused letters-scope record gives way to facing-pair records for the facing pairs with evidence that pass. This repeats until what remains resolves; then the editor's own check (`KernRoutes.check`: the spec loader over a temporary copy of the runes directory, the stage, the lookup's plan and the contact check) runs over every file to be written, and a refusal it names a record in goes round again.

**The report** (`var/kern/import-report.json`, format `REPORT_FORMAT`) holds the counts; the records with the facing pairs each sets; the facing pairs whose members disagree, with their members' old glyph pairs and values; the facing pairs an existing record decides, and the files skipped whole; the refused records with the build's messages; the facing pairs that end with another value than their evidence, and why; the facing pairs with no evidence a letters-scope record sets (`covered`); the rune pairs narrowed to facing-pair records (`narrowed`); each joined group of the rebuild whose old glyph pairs the old rules move, with the old values (dropped, since kerning never moves a join); and each old rule that sets nothing in the rebuild, with why: its value is 0, its letters have no rune file, no glyph it names has an alias, its glyph pairs never meet in the rebuild or only join, or every facing pair it reaches disagrees, already has a record, was refused, or has rules that bring its sum to 0. It describes the plan, not the write, so it reads the same for a second `--apply`.

**Writing.** The dry run prints the summary and writes only the report. `--apply` holds the cycle's pass lock (`var/cycle/pass.lock`) without waiting, as the review server's writes do, and refuses while a pass holds it or while the tables on disk are not the ones the sources give (run the M1 build first). It plans under the lock, writes each rune file whose block changes atomically through a staging file beside its outputs (`kern_writer.replace_text`), and writes `var/kern/imported.json` (format `IMPORTED_FORMAT`: each facing pair it set, with the value and the record that sets it, which the review server reads to mark the pairs carried over) and the report, each only when its bytes change. Nothing here is in the build's or the corpus's code keys: no build stage imports it.

`--runes DIR` reads and writes another runes directory, a copy, over the tables and glyphs of `--m1-out`; `--out DIR` puts the outputs elsewhere than `var/kern/`.
"""

from __future__ import annotations

import argparse
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
from rebuild.pipeline.model import CellId, ResolvedSpec
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
IMPORTED_FORMAT = "ams-kern-imported/1"
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
        """Return the rules that reach a glyph pair of the two families and are not a plain `left_family` by `right_family` rule: a stance side, an exact-name side, a side left out, or a carve-out. Such a rule tells some shapes of a family apart, so the two families take no letters-scope record. A rule reaches the two families when its left side names `lead` (or names no glyph, reaching every family) and its right side names `follow` likewise, by the head of each name it lists."""
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


@dataclass
class Evidence:
    """What the old rules say about one facing pair: every value its named members take, each named member's old glyph pairs with their values, and those old glyph pairs."""

    values: set[int] = field(default_factory=set)
    members: list[dict] = field(default_factory=list)
    old_pairs: set[OldPair] = field(default_factory=set)

    @property
    def value(self) -> int | None:
        return next(iter(self.values)) if len(self.values) == 1 else None


def gather_evidence(
    resolution: kern.KernResolution,
    names: Mapping[CellId, list[str]],
    rules: OldRules,
    label: Mapping[CellId, str],
) -> dict[PairKey, Evidence]:
    """Return the evidence of every facing pair, by its two side names; a facing pair no member of which is named on both sides has none."""
    found: dict[PairKey, Evidence] = {}
    for pair in resolution.pairs:
        evidence = Evidence()
        for left, right in pair.members:
            left_names, right_names = names.get(left, []), names.get(right, [])
            if not left_names or not right_names:
                continue
            values = {(a, b): rules.evaluator.value_for(a, b) for a in left_names for b in right_names}
            evidence.values |= set(values.values())
            evidence.old_pairs |= set(values)
            evidence.members.append(
                {
                    "lead": label.get(left, str(left)),
                    "follow": label.get(right, str(right)),
                    "values": {f"{a} {b}": value for (a, b), value in sorted(values.items())},
                }
            )
        if evidence.values:
            found[(pair.leading.name, pair.following.name)] = evidence
    return found


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


def _ours(out_dir: Path) -> dict[str, set[str]]:
    """Return the records the last import wrote, by rune, as their lines, from its `imported.json`."""
    try:
        document = json.loads((Path(out_dir) / IMPORTED_NAME).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    ours: dict[str, set[str]] = defaultdict(set)
    for entry in document.get("pairs", []) if isinstance(document, dict) else []:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("rune"), str)
            and isinstance(entry.get("line"), str)
        ):
            ours[entry["rune"]].add(entry["line"])
    return ours


class Importer:
    """One import over the routes' runes directory and tables, its outputs under `out_dir`."""

    def __init__(
        self, routes: kern_routes.KernRoutes, out_dir: Path, sidecar: Path = SIDECAR, aliases: Path = ALIASES
    ) -> None:
        self.routes = routes
        self.out_dir = Path(out_dir)
        self.rules = load_old_rules(sidecar)
        self.aliases = labels.load_alias_map(aliases)
        self.names = old_names(self.aliases)
        self.evidence: dict[PairKey, Evidence] = {}
        self.narrowed: list[dict] = []

    def _file(self, rune: str) -> str:
        return kern_routes.file_label(self.routes.runes_dir / f"{rune}.yaml")

    def plan(self) -> Outcome:
        """Find the evidence, plan the records, leave out what the validation refuses, and return the outcome."""
        routes = self.routes
        context = routes.context()
        spec = context.spec
        label = {cell: record.name for cell, record in context.glyphs.items()}
        order: Callable[[str], tuple[int, ...]] = lambda rune: kern.rune_order_key(spec, rune)  # noqa: E731
        ours = _ours(self.out_dir)
        current: dict[str, str] = {}
        base: dict[str, list] = {}
        unwritable: dict[str, str] = {}
        set_aside: set[str] = set()
        for path in sorted(routes.runes_dir.glob("*.yaml")):
            if path.stem not in spec.runes:
                continue
            text = kern_writer.read_text(path)
            current[path.stem] = text
            try:
                records = kern_writer.parse_block(
                    kern_writer.split_file(text)[1], self._file(path.stem), kern_writer.line_ending(text)
                )
            except kern_writer.BlockRefused as refusal:
                unwritable[path.stem] = str(refusal)
                continue
            mine = ours.get(path.stem, set())
            base[path.stem] = [record for record in records if kern_writer.record_line(record) not in mine]
            if len(base[path.stem]) != len(records):
                set_aside.add(path.stem)
        if set_aside:
            found = routes.resolve_with(context, {rune: base[rune] for rune in set_aside})
        else:
            found = context.before
        if found is None:
            raise ImportRefused(
                "the rune files' kern blocks do not resolve over the tables on disk; fix them first"
            )
        resolution: kern.KernResolution = found
        self.evidence = evidence = gather_evidence(resolution, self.names, self.rules, label)

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
            valued = [key for key in keys if key not in decided and key in evidence and evidence[key].value]
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
            if len(seen) == 1 and not narrowing and not any(key in decided for key in keys):
                plans.append(
                    Plan(
                        lead,
                        kern_writer.letters_scope(lead, follow),
                        evidence[valued[0]].value or 0,
                        keys,
                    )
                )
                continue
            for key in valued:
                plans.append(Plan(lead, kern_writer.facing_scope(*key), evidence[key].value or 0, [key]))

        refused: list[dict] = []
        blocks, final, final_spec = self._validate(
            context, base, set_aside, plans, resolution, refused, label
        )
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
                        }
                    )
        self.narrowed = narrowed
        report = self._report(
            context=context,
            spec=final_spec,
            resolution=resolution,
            final=final,
            decided=decided,
            plans=plans,
            refused=refused,
            imported=imported,
            final_values=final_values,
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
        plans: list[Plan],
        refused: list[dict],
        imported: list[dict],
        final_values: Mapping[PairKey, int],
        set_by: Mapping[PairKey, Plan],
        skipped_files: Mapping[str, str],
        label: Mapping[CellId, str],
        order,
    ) -> dict:
        evidence = self.evidence
        refused_pairs = {pair for entry in refused for pair in entry["pairs"]}
        outcome: dict[PairKey, str] = {}
        for key, found in evidence.items():
            if found.value is None:
                outcome[key] = "disagree"
            elif key in decided:
                outcome[key] = "skipped"
            elif key in set_by and final_values.get(key) == found.value:
                outcome[key] = "imported"
            elif _key_text(key) in refused_pairs:
                outcome[key] = "refused"
            elif found.value == 0:
                outcome[key] = "zero"
            else:
                outcome[key] = "differs"

        def sort_key(key: PairKey) -> tuple:
            return (order(key[0].split("[")[0]), key[0], order(key[1].split("[")[0]), key[1])

        disagreements = [
            {
                "lead": key[0],
                "follow": key[1],
                "values": sorted(evidence[key].values),
                "members": evidence[key].members,
            }
            for key in sorted(evidence, key=sort_key)
            if outcome[key] == "disagree"
        ]
        skipped = [
            {"lead": key[0], "follow": key[1], "old": evidence[key].value, "deciders": decided[key]}
            for key in sorted(evidence, key=sort_key)
            if outcome[key] == "skipped" and evidence[key].value
        ]
        inherited = {
            (pair.leading.name, pair.following.name)
            for pair in final.pairs
            if any(decider.inherited for decider in pair.deciders)
        }
        differs = []
        for key in sorted(evidence, key=sort_key):
            found = evidence[key]
            if found.value is None or key in decided or final_values.get(key, 0) == found.value:
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
                for a in self.names.get(left, []):
                    for b in self.names.get(right, []):
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

        rules = self._inert_rules(spec, outcome, joined_pairs, imported)
        covered = [
            {"lead": entry["lead"], "follow": entry["follow"], "by": entry["by"], "line": entry["line"]}
            for entry in imported
            if (entry["lead"], entry["follow"]) not in evidence
        ]
        records = []
        for plan in plans:
            pairs = sorted(
                [_key_text(key) for key, by in set_by.items() if by is plan], key=lambda text: text
            )
            records.append(
                {
                    "rune": plan.rune,
                    "file": self._file(plan.rune),
                    "scope": plan.scope.kind,
                    "line": plan.line,
                    "pairs": pairs,
                }
            )
        counts = {
            "facing_pairs": len(resolution.pairs),
            "with_evidence": len(evidence),
            "without_evidence": len(resolution.pairs) - len(evidence),
            "disagree": len(disagreements),
            "zero": sum(1 for value in outcome.values() if value == "zero"),
            "letters_records": sum(1 for plan in plans if plan.scope.kind == kern_writer.LETTERS),
            "facing_records": sum(1 for plan in plans if plan.scope.kind == kern_writer.FACING),
            "set_by_letters": sum(1 for entry in imported if entry["scope"] == kern_writer.LETTERS),
            "set_by_facing": sum(1 for entry in imported if entry["scope"] == kern_writer.FACING),
            "refused_records": len(refused),
            "skipped_pairs": len(skipped),
            "skipped_files": len(skipped_files),
            "differs": len(differs),
            "covered_without_evidence": len(covered),
            "narrowed_rune_pairs": len(self.narrowed),
            "joined_groups_moved": len(joined),
            "old_rules": len(self.rules.rules),
            "old_rules_inert": len(rules),
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
            "refused": refused,
            "differs": differs,
            "covered": covered,
            "narrowed": self.narrowed,
            "joined": joined,
            "inert_rules": rules,
            "left_out": self.rules.left_out,
            "not_read": "the kerning rules of glyph_data/opentype-features.yaml (the Latin rules and the rule that tightens ·He before a ZWNJ)",
        }

    def _inert_rules(
        self,
        spec: ResolvedSpec,
        outcome: Mapping[PairKey, str],
        joined_pairs: set[OldPair],
        imported: list[dict],
    ) -> list[dict]:
        """Return each old pair rule that sets nothing in the rebuild, with why."""
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

        reached: dict[int, set[str]] = defaultdict(set)
        for key, found in self.evidence.items():
            for pair in found.old_pairs:
                for index in matched(pair):
                    reached[index].add(outcome[key])
        joins: set[int] = {index for pair in joined_pairs for index in matched(pair)}
        inert = []
        for index, rule in self.rules.rules:
            if "imported" in reached[index] and rule.get("value", 0):
                continue
            heads = {
                name.split(".")[0]
                for side in ("left", "right")
                for key in (f"{side}_family", f"{side}_stance", side)
                for name in rule.get(key, ())
            }
            missing = sorted(head for head in heads if head not in spec.runes)
            left, right = sides[index]
            outcomes = reached[index]
            if not rule.get("value", 0):
                reason = "its value is 0, an override that only cancels another rule"
            elif missing:
                reason = f"its letters have no rune file: {', '.join(missing)}"
            elif not left or not right:
                reason = "no glyph it names on one side has an alias in rebuild/m1-aliases.yaml"
            elif not outcomes and index in joins:
                reason = "its glyph pairs only join in the rebuild"
            elif not outcomes:
                reason = "its glyph pairs never meet in the rebuild"
            elif outcomes == {"disagree"}:
                reason = "every facing pair it reaches has members the old rules give different values"
            elif outcomes == {"skipped"}:
                reason = "every facing pair it reaches already has a record"
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
        f"  facing pairs: {counts['facing_pairs']}; with old evidence {counts['with_evidence']} (0 for {counts['zero']}), without {counts['without_evidence']}; members disagree on {counts['disagree']}",
        f"  set: {counts['set_by_letters']} facing pairs by {counts['letters_records']} letters records, {counts['set_by_facing']} by {counts['facing_records']} facing-pair records; {counts['narrowed_rune_pairs']} pairs of letters narrowed to facing-pair records by a stance or carve-out rule",
        f"  refused: {counts['refused_records']} records"
        + (
            f" ({'; '.join(f'{count} {message}' for message, count in refusals.most_common())})"
            if refusals
            else ""
        ),
        f"  skipped: {counts['skipped_pairs']} facing pairs a record already decides, {counts['skipped_files']} files the editor cannot write",
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
    pass_lock: Path = PASS_LOCK,
    sidecar: Path = SIDECAR,
    aliases: Path = ALIASES,
    repo_root: Path = REPO_ROOT,
    tables_inputs: Callable[[], str] | None = None,
    routes: kern_routes.KernRoutes | None = None,
    stream: TextIO = sys.stdout,
) -> int:
    """Run the import as the module docstring says, printing to `stream`, and return the exit status: 0, or 1 when it refuses."""
    routes = routes or kern_routes.KernRoutes(repo_root, m1_out, tables_inputs, runes_dir=runes_dir)
    routes.pass_lock = Path(pass_lock)
    out_dir = Path(out_dir)
    try:
        importer = Importer(routes, out_dir, sidecar, aliases)
        _tables, stamp, error = routes.tables()
        current = routes.tables_current(stamp) if error is None else None
        if not apply:
            outcome = importer.plan()
            _write_json(out_dir / REPORT_NAME, outcome.report)
            if current is False:
                print(
                    "import_kerning: the tables on disk are not the ones the sources give; run the M1 build before --apply",
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
                outcome = importer.plan()
                written: list[str] = []
                for rune, text in outcome.texts.items():
                    path = routes.runes_dir / f"{rune}.yaml"
                    if kern_writer.read_text(path) != outcome.current[rune]:
                        raise ImportRefused(f"{path} changed while the import ran; run it again")
                    kern_writer.replace_text(path, text, out_dir)
                    written.append(path.name)
                _write_json(out_dir / IMPORTED_NAME, {"format": IMPORTED_FORMAT, "pairs": outcome.imported})
                _write_json(out_dir / REPORT_NAME, outcome.report)
        except store_lock.LockBusy:
            holder = store_lock.holder(routes.pass_lock) or "unknown"
            raise ImportRefused(
                f"a cycle pass (pid {holder}) holds {routes.pass_lock}; run the import when it ends"
            ) from None
    except (ImportRefused, kern_routes.Refused) as refusal:
        print(f"import_kerning: {refusal}", file=stream)
        return 1
    for line in summary_lines(outcome.report, written, routes.runes_dir):
        print(line, file=stream)
    print(f"  wrote {out_dir / IMPORTED_NAME} and {out_dir / REPORT_NAME}", file=stream)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Carry the old font's Senior kerning over into the rune files' kern: blocks; without --apply, a dry run that writes only the report."
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the kern blocks and var/kern/imported.json"
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
    )


if __name__ == "__main__":
    sys.exit(main())
