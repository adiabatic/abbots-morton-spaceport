"""YAML → ResolvedSpec: schema validation with file/line errors, the Python lints (stance-ID naming, ductus parity, refuse right.then rejection, the right-side then-chain depth cap, reference resolution), predicate-class evaluation, and rune-local group resolution (rebuild/M1-PLAN.md section 5, Group 1).

Schema validation is driven directly by the JSON Schema files under rebuild/schema/ through a small built-in evaluator covering the keyword subset those schemas use, so `uv run pytest rebuild/` needs no third-party validator; when `jsonschema` is importable (the plan's `uv run --with jsonschema` path) a cross-check test asserts the two layers agree. Every error carries the YAML file, key path, and line; all errors are collected before SpecError is raised.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

from rebuild.pipeline.model import (
    RIGHT_CHAIN_CAP,
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

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNES_DIR = REPO_ROOT / "glyph_data" / "runes"
DEFAULT_REGISTRY_PATH = REPO_ROOT / "rebuild" / "script.yaml"
DEFAULT_SCHEMA_DIR = REPO_ROOT / "rebuild" / "schema"

FORBIDDEN_ID_PATTERN = re.compile(r"(before|after|noentry|noexit|nonjoining|ss[0-9])")

HAPAX_SENTINEL = "hapax"

_NAMING_RULE = "stance IDs and motion names describe pen motions, never neighbors, boundaries, or features (doc/rebuild-design.md section 3.1), except that a single-stance rune names its sole stance and sole motion 'hapax' (a reserved sentinel; the pen-motion label lives in the ductus prose)"
_WINDOW_RULE = "refuse and require records must be decidable one position to the left of the rune they constrain, so right.then is forbidden on them (doc/rebuild-design.md section 3.3)"
_CAP_WORDS = {1: "one", 2: "two", 3: "three", 4: "four"}
_CHAIN_RULE = f"a right-side then: chain may reach at most {_CAP_WORDS[RIGHT_CHAIN_CAP]} letters past the immediate right neighbor — the depth-{RIGHT_CHAIN_CAP + 1} window edge, counting hops carried by except: entries (doc/rebuild-design.md section 3.4)"


@dataclass(frozen=True)
class SpecIssue:
    file: str
    path: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line is not None else self.file
        at = f" at {self.path}" if self.path else ""
        return f"{where}{at}: {self.message}"


class SpecError(Exception):
    def __init__(
        self,
        file: str,
        path: str,
        message: str,
        line: int | None = None,
        issues: tuple[SpecIssue, ...] | None = None,
    ):
        if issues is None:
            issues = (SpecIssue(file, path, message, line),)
        self.issues = tuple(issues)
        super().__init__("\n".join(str(issue) for issue in self.issues))

    @classmethod
    def from_issues(cls, issues: list[SpecIssue] | tuple[SpecIssue, ...]) -> "SpecError":
        ordered = tuple(issues)
        first = ordered[0]
        return cls(first.file, first.path, first.message, first.line, issues=ordered)


class SpecWarning(UserWarning):
    pass


class _SchemaChecker:
    """Evaluates the keyword subset rebuild/schema/*.json actually uses; an unrecognized keyword is a hard error so a schema edit cannot silently skip validation."""

    _KEYWORDS = frozenset(
        {
            "type",
            "enum",
            "const",
            "pattern",
            "minimum",
            "items",
            "minItems",
            "maxItems",
            "minProperties",
            "properties",
            "patternProperties",
            "additionalProperties",
            "required",
            "propertyNames",
            "allOf",
            "anyOf",
            "oneOf",
            "not",
            "if",
            "then",
            "$ref",
        }
    )
    _IGNORED = frozenset({"$schema", "$id", "title", "description", "$defs"})

    def __init__(self, schema: dict, schema_name: str):
        self.schema = schema
        self.schema_name = schema_name
        self.defs = schema.get("$defs", {})

    def check(self, value: object) -> list[tuple[str, str]]:
        errors: list[tuple[str, str]] = []
        self._check(value, self.schema, "", errors)
        return errors

    def _resolve(self, schema: dict) -> dict:
        while "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name not in self.defs:
                raise ValueError(f"{self.schema_name}: dangling $ref {schema['$ref']}")
            schema = self.defs[name]
        return schema

    def _passes(self, value: object, schema: dict) -> bool:
        probe: list[tuple[str, str]] = []
        self._check(value, schema, "", probe)
        return not probe

    def _type_ok(self, value: object, expected: str) -> bool:
        if expected == "object":
            return isinstance(value, dict)
        if expected == "array":
            return isinstance(value, list)
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        raise ValueError(f"{self.schema_name}: unsupported type {expected!r}")

    def _check(self, value: object, schema: dict, path: str, errors: list[tuple[str, str]]) -> None:
        schema = self._resolve(schema)
        for keyword in schema:
            if keyword not in self._KEYWORDS and keyword not in self._IGNORED:
                raise ValueError(f"{self.schema_name}: unsupported JSON Schema keyword {keyword!r}")
        expected_type = schema.get("type")
        if expected_type is not None and not self._type_ok(value, expected_type):
            errors.append((path, f"expected {expected_type}, got {type(value).__name__} ({value!r})"))
            return
        if "const" in schema and value != schema["const"]:
            errors.append((path, f"must be {schema['const']!r}, got {value!r}"))
        if "enum" in schema and value not in schema["enum"]:
            errors.append((path, f"must be one of {schema['enum']}, got {value!r}"))
        if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
            errors.append((path, f"{value!r} does not match required pattern {schema['pattern']!r}"))
        if "minimum" in schema and isinstance(value, int) and not isinstance(value, bool):
            if value < schema["minimum"]:
                errors.append((path, f"must be >= {schema['minimum']}, got {value}"))
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append((path, f"must have at least {schema['minItems']} items"))
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append((path, f"must have at most {schema['maxItems']} items"))
            if "items" in schema:
                for index, item in enumerate(value):
                    self._check(item, schema["items"], f"{path}[{index}]", errors)
        if isinstance(value, dict):
            self._check_object(value, schema, path, errors)
        for sub in schema.get("allOf", ()):
            self._check(value, sub, path, errors)
        if "anyOf" in schema and not any(self._passes(value, sub) for sub in schema["anyOf"]):
            errors.append((path, "does not satisfy any permitted alternative"))
        if "oneOf" in schema:
            matched = sum(1 for sub in schema["oneOf"] if self._passes(value, sub))
            if matched != 1:
                errors.append(
                    (
                        path,
                        f"must satisfy exactly one of {len(schema['oneOf'])} alternatives, matched {matched}",
                    )
                )
        if "not" in schema and self._passes(value, schema["not"]):
            errors.append((path, "matches a forbidden form"))
        if "if" in schema and self._passes(value, schema["if"]) and "then" in schema:
            self._check(value, schema["then"], path, errors)

    def _check_object(self, value: dict, schema: dict, path: str, errors: list[tuple[str, str]]) -> None:
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append((path, f"must have at least {schema['minProperties']} keys"))
        for key in schema.get("required", ()):
            if key not in value:
                errors.append((path, f"missing required key {key!r}"))
        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        additional = schema.get("additionalProperties", True)
        property_names = schema.get("propertyNames")
        for key, item in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if not isinstance(key, str):
                errors.append((key_path, f"keys must be strings, got {key!r}"))
                continue
            if property_names is not None:
                self._check(key, property_names, key_path, errors)
            matched = False
            if key in properties:
                matched = True
                self._check(item, properties[key], key_path, errors)
            for pattern, sub in pattern_properties.items():
                if re.search(pattern, key):
                    matched = True
                    self._check(item, sub, key_path, errors)
            if not matched:
                if additional is False:
                    known = sorted(set(properties) | set(pattern_properties))
                    errors.append(
                        (key_path, f"unknown key {key!r} (closed vocabulary; expected one of {known})")
                    )
                elif isinstance(additional, dict):
                    self._check(item, additional, key_path, errors)


def _line_index(text: str) -> dict[str, int]:
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return {}
    index: dict[str, int] = {}

    def walk(node: yaml.Node, path: str) -> None:
        index.setdefault(path, node.start_mark.line + 1)
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                child = f"{path}.{key_node.value}" if path else str(key_node.value)
                index[child] = key_node.start_mark.line + 1
                walk(value_node, child)
        elif isinstance(node, yaml.SequenceNode):
            for position, item in enumerate(node.value):
                walk(item, f"{path}[{position}]")

    if root is not None:
        walk(root, "")
    return index


def _line_for(lines: dict[str, int], path: str) -> int | None:
    candidate = path
    while candidate:
        if candidate in lines:
            return lines[candidate]
        trimmed = re.sub(r"(\.[^.\[\]]+|\[\d+\])$", "", candidate)
        if trimmed == candidate:
            break
        candidate = trimmed
    return lines.get("")


class _FileContext:
    def __init__(self, path: Path, issues: list[SpecIssue]):
        try:
            self.file = str(path.relative_to(REPO_ROOT))
        except ValueError:
            self.file = str(path)
        self.issues = issues
        text = path.read_text()
        self.lines = _line_index(text)
        self.data = yaml.safe_load(text)

    def error(self, path: str, message: str) -> None:
        self.issues.append(SpecIssue(self.file, path, message, _line_for(self.lines, path)))

    def provenance(self, path: str) -> Provenance:
        return Provenance(file=self.file, path=path)


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _condition(raw: dict) -> Condition:
    return Condition(
        family=_as_tuple(raw.get("family")),
        klass=_as_tuple(raw.get("class")),
        stance=_as_tuple(raw.get("stance")),
        joined_at=raw.get("joined_at"),
        stroke=raw.get("stroke"),
        is_token=raw.get("is"),
        except_=tuple(_condition(item) for item in raw.get("except", ())),
        then=_condition(raw["then"]) if raw.get("then") is not None else None,
    )


def _when(raw: dict | None) -> When:
    if not raw:
        return When()
    self_state = raw.get("self") or {}
    return When(
        left=_condition(raw["left"]) if "left" in raw else None,
        right=_condition(raw["right"]) if "right" in raw else None,
        self_entry=self_state.get("entry"),
        self_exit=self_state.get("exit"),
        word=raw.get("word"),
        feature=raw.get("feature"),
    )


def _stub(raw: dict | None) -> Stub | None:
    if raw is None:
        return None
    return Stub(cols=tuple(raw["cols"]), inks_when=raw["inks_when"])


def _bitmap(raw: dict) -> Bitmap:
    return Bitmap(rows=tuple(raw["bitmap"]), y_offset=raw.get("y_offset", 0))


def _entry_row(height: str, raw: dict, provenance: Provenance) -> SurfaceRow:
    return SurfaceRow(
        height=height,
        x=raw["x"],
        stroke=raw.get("stroke"),
        joined=raw.get("joined"),
        joined_x=raw.get("joined_x"),
        stub=_stub(raw.get("stub")),
        scope=tuple(_condition(item) for item in raw.get("from", ())),
        selectable=raw.get("selectable", True),
        x_off_convention=raw.get("x_off_convention", False),
        provenance=provenance,
    )


def _exit_row(height: str, raw: dict, provenance: Provenance) -> SurfaceRow:
    return SurfaceRow(
        height=height,
        x=raw["x"],
        stroke=raw.get("stroke"),
        withdrawal=raw.get("withdrawal"),
        stub=_stub(raw.get("stub")),
        scope=tuple(_condition(item) for item in raw.get("toward", ())),
        ink_y=raw.get("ink_y"),
        x_off_convention=raw.get("x_off_convention", False),
        provenance=provenance,
    )


def _pairing(raw: dict) -> Pairing:
    return Pairing(entry=raw["entry"], exit=raw["exit"])


def _surface(raw: dict | None, context: _FileContext, base_path: str) -> Surface:
    if not raw:
        return Surface()
    pairings_raw = raw.get("pairings") or {}
    return Surface(
        entries={
            height: _entry_row(height, row, context.provenance(f"{base_path}.entries.{height}"))
            for height, row in (raw.get("entries") or {}).items()
        },
        exits={
            height: _exit_row(height, row, context.provenance(f"{base_path}.exits.{height}"))
            for height, row in (raw.get("exits") or {}).items()
        },
        pairings=Pairings(
            never=tuple(_pairing(item) for item in pairings_raw.get("never", ())),
            only=tuple(_pairing(item) for item in pairings_raw["only"]) if "only" in pairings_raw else None,
        ),
        cells=tuple(
            CellBinding(
                entry=item["entry"],
                exit=item["exit"],
                bitmap=item["bitmap"],
                entry_x=item.get("entry_x"),
                exit_x=item.get("exit_x"),
                provenance=context.provenance(f"{base_path}.cells[{index}]"),
            )
            for index, item in enumerate(raw.get("cells", ()))
        ),
        unlocks=tuple(
            Unlock(
                feature=item["feature"],
                entry=item.get("entry"),
                exit=item.get("exit"),
                pairing=_pairing(item["pairing"]) if "pairing" in item else None,
                when=_when(item.get("when")) if item.get("when") else None,
                why=item.get("why"),
                provenance=context.provenance(f"{base_path}.unlocks[{index}]"),
            )
            for index, item in enumerate(raw.get("unlocks", ()))
        ),
        require=tuple(raw.get("require", ())),
    )


_RECORD_KINDS = ("refuse", "prefer", "extend", "contract")


def _policy_record(kind: str, raw: dict, provenance: Provenance) -> PolicyRecord:
    ok = raw.get("ok")
    split = raw.get("split")
    return PolicyRecord(
        kind=kind,
        when=_when(raw.get("when")),
        stance=raw.get("stance"),
        entry=raw.get("entry"),
        exit=raw.get("exit"),
        cell=raw.get("cell"),
        over=raw.get("over"),
        mode=raw.get("mode"),
        by=raw.get("by"),
        ok=(ok[0], ok[1]) if ok else None,
        bind=raw.get("bind"),
        trim=raw.get("trim"),
        split=(split[0], split[1]) if split else None,
        why=raw.get("why"),
        provenance=provenance,
    )


def _load_schema(schema_dir: Path, name: str) -> _SchemaChecker:
    path = schema_dir / name
    return _SchemaChecker(json.loads(path.read_text()), name)


def _walk_conditions(raw: object, path: str):
    """Yields (path, condition_dict) for every condition object reachable from a when:/scope structure, except: atoms and then: hops included."""
    if isinstance(raw, dict):
        yield path, raw
        for atom_index, atom in enumerate(raw.get("except", ()) or ()):
            yield from _walk_conditions(atom, f"{path}.except[{atom_index}]")
        if isinstance(raw.get("then"), dict):
            yield from _walk_conditions(raw["then"], f"{path}.then")


def _right_chain_reach(raw: dict) -> int:
    """How many raw slots past its own a right condition's then: chains read: a then: hop advances one slot, and an except: entry tests its parent's slot, so its hops count from there."""
    reach = 0
    if isinstance(raw.get("then"), dict):
        reach = max(reach, 1 + _right_chain_reach(raw["then"]))
    for atom in raw.get("except", ()) or ():
        if isinstance(atom, dict):
            reach = max(reach, _right_chain_reach(atom))
    return reach


def _when_conditions(raw: dict | None, path: str):
    if not raw:
        return
    for side in ("left", "right"):
        if isinstance(raw.get(side), dict):
            yield from _walk_conditions(raw[side], f"{path}.{side}")


class _Linter:
    def __init__(
        self,
        context: _FileContext,
        registry_families: dict,
        registry_classes: dict,
        registry_features: set[str],
    ):
        self.context = context
        self.raw = context.data
        self.families = registry_families
        self.classes = registry_classes
        self.features = registry_features
        policy = self.raw.get("policy")
        self.group_names = set(policy.get("groups") or {}) if isinstance(policy, dict) else set()
        self.stance_rows: dict[str, dict[str, set[str]]] = {}

    def _stance_rows(self) -> dict[str, dict[str, set[str]]]:
        rows: dict[str, dict[str, set[str]]] = {}
        for stance_name, stance_raw in (self.raw.get("stances") or {}).items():
            surface = stance_raw.get("surface") or {}
            entries = set(surface.get("entries") or {})
            exits = set(surface.get("exits") or {})
            for unlock in surface.get("unlocks", ()):
                if unlock.get("entry"):
                    entries.add(unlock["entry"])
                if unlock.get("exit"):
                    exits.add(unlock["exit"])
            rows[stance_name] = {"entry": entries, "exit": exits}
        return rows

    def run_shallow(self) -> None:
        """Lints safe on any document shape — run even when the schema layer already rejected the file, so the readable design-rule messages always accompany the mechanical ones."""
        self._lint_identifiers()
        self._lint_single_stance_sentinel()
        self._lint_single_motion_sentinel()
        self._lint_ductus_parity()
        self._lint_refuse_window_rule()
        self._lint_right_chain_depth()

    def run_deep(self) -> None:
        self.stance_rows = self._stance_rows()
        self._lint_registry_consistency()
        self._lint_stances()
        self._lint_policy()

    def _stances(self) -> dict[str, dict]:
        stances = self.raw.get("stances")
        if not isinstance(stances, dict):
            return {}
        return {name: value for name, value in stances.items() if isinstance(value, dict)}

    def _lint_identifiers(self) -> None:
        for stance_name, stance_raw in self._stances().items():
            if FORBIDDEN_ID_PATTERN.search(stance_name):
                self.context.error(
                    f"stances.{stance_name}",
                    f"stance ID {stance_name!r} matches the forbidden pattern {FORBIDDEN_ID_PATTERN.pattern}; {_NAMING_RULE}",
                )
            motion = stance_raw.get("motion")
            if isinstance(motion, str) and FORBIDDEN_ID_PATTERN.search(motion):
                self.context.error(
                    f"stances.{stance_name}.motion",
                    f"motion name {motion!r} matches the forbidden pattern {FORBIDDEN_ID_PATTERN.pattern}; {_NAMING_RULE}",
                )
        ductus = self.raw.get("ductus")
        for motion_name in ductus if isinstance(ductus, dict) else ():
            if FORBIDDEN_ID_PATTERN.search(str(motion_name)):
                self.context.error(
                    f"ductus.{motion_name}",
                    f"motion name {motion_name!r} matches the forbidden pattern {FORBIDDEN_ID_PATTERN.pattern}; {_NAMING_RULE}",
                )

    def _lint_single_stance_sentinel(self) -> None:
        keys = list(self._stances())
        if len(keys) == 1 and keys[0] != HAPAX_SENTINEL:
            self.context.error(
                f"stances.{keys[0]}",
                f"single-stance rune must name its sole stance {HAPAX_SENTINEL!r} (reserved sentinel for one-stance runes; the pen-motion label lives in the ductus prose), not {keys[0]!r}",
            )
        elif len(keys) > 1 and HAPAX_SENTINEL in keys:
            self.context.error(
                f"stances.{HAPAX_SENTINEL}",
                f"{HAPAX_SENTINEL!r} is reserved for the sole stance of a single-stance rune; a rune with multiple stances may not use it",
            )

    def _lint_single_motion_sentinel(self) -> None:
        ductus = self.raw.get("ductus")
        keys = list(ductus) if isinstance(ductus, dict) else []
        if len(keys) == 1 and keys[0] != HAPAX_SENTINEL:
            self.context.error(
                f"ductus.{keys[0]}",
                f"single-motion ductus must name its sole motion {HAPAX_SENTINEL!r} (reserved sentinel for one-motion runes; the pen-motion label lives in the ductus prose), not {keys[0]!r}",
            )
        elif len(keys) > 1 and HAPAX_SENTINEL in keys:
            self.context.error(
                f"ductus.{HAPAX_SENTINEL}",
                f"{HAPAX_SENTINEL!r} is reserved for the sole motion of a single-motion ductus; a rune with multiple motions may not use it",
            )

    def _lint_refuse_window_rule(self) -> None:
        policy = self.raw.get("policy")
        refuse = policy.get("refuse") if isinstance(policy, dict) else None
        for index, record in enumerate(refuse if isinstance(refuse, list) else ()):
            if not isinstance(record, dict):
                continue
            when = record.get("when")
            right = when.get("right") if isinstance(when, dict) else None
            if isinstance(right, dict) and "then" in right:
                self.context.error(f"policy.refuse[{index}].when.right.then", _WINDOW_RULE)

    def _lint_right_chain_depth(self) -> None:
        policy = self.raw.get("policy")
        for kind in ("prefer", "extend", "contract"):
            records = policy.get(kind) if isinstance(policy, dict) else None
            for index, record in enumerate(records if isinstance(records, list) else ()):
                if not isinstance(record, dict):
                    continue
                when = record.get("when")
                right = when.get("right") if isinstance(when, dict) else None
                if isinstance(right, dict) and _right_chain_reach(right) > RIGHT_CHAIN_CAP:
                    self.context.error(f"policy.{kind}[{index}].when.right", _CHAIN_RULE)
        for stance_name, stance_raw in self._stances().items():
            surface = stance_raw.get("surface")
            unlocks = surface.get("unlocks") if isinstance(surface, dict) else None
            for index, unlock in enumerate(unlocks if isinstance(unlocks, list) else ()):
                if not isinstance(unlock, dict):
                    continue
                when = unlock.get("when")
                right = when.get("right") if isinstance(when, dict) else None
                if isinstance(right, dict) and _right_chain_reach(right) > RIGHT_CHAIN_CAP:
                    self.context.error(
                        f"stances.{stance_name}.surface.unlocks[{index}].when.right", _CHAIN_RULE
                    )

    def _lint_ductus_parity(self) -> None:
        ductus = self.raw.get("ductus")
        if not isinstance(ductus, dict):
            ductus = {}
        realized = set(ductus)
        used: set[str] = set()
        for stance_name, stance_raw in self._stances().items():
            motion = stance_raw.get("motion")
            used.add(motion)
            if motion not in ductus:
                self.context.error(
                    f"stances.{stance_name}.motion",
                    f"stance {stance_name!r} names motion {motion!r}, which is not in the ductus",
                )
        for motion in sorted(realized - used):
            self.context.error(
                f"ductus.{motion}",
                f"motion {motion!r} is realized in the ductus but no stance names it (ductus parity, doc/rebuild-design.md section 3.1)",
            )

    def _lint_registry_consistency(self) -> None:
        rune_name = self.raw.get("rune")
        stem = Path(self.context.file).stem
        if rune_name != stem:
            self.context.error("rune", f"rune {rune_name!r} does not match its file name {stem!r}")
        info = self.families.get(rune_name)
        if info is None:
            self.context.error("rune", f"rune {rune_name!r} is not in the registry's families table")
            return
        codepoint = self.raw.get("codepoint")
        if codepoint is not None and info.get("codepoint") != codepoint:
            self.context.error(
                "codepoint",
                f"codepoint 0x{codepoint:04X} disagrees with the registry's 0x{info.get('codepoint', 0):04X} for {rune_name}",
            )
        sequence = self.raw.get("sequence")
        if sequence is not None and tuple(info.get("sequence") or ()) != tuple(sequence):
            self.context.error(
                "sequence", f"sequence {sequence} disagrees with the registry's {info.get('sequence')}"
            )
        for position, member in enumerate(sequence or ()):
            self._check_family(member, f"sequence[{position}]")

    def _check_family(self, name: str, path: str) -> None:
        if name not in self.families:
            self.context.error(
                path, f"unknown family {name!r}; every family reference must resolve against the registry"
            )

    def _check_class(self, name: str, path: str) -> None:
        if name not in self.classes and name not in self.group_names:
            self.context.error(
                path,
                f"unknown class {name!r}; not a registry predicate class and not a rune-local group of this rune",
            )

    def _check_condition_atom(self, condition: dict, condition_path: str) -> None:
        for family in _as_tuple(condition.get("family")):
            self._check_family(family, f"{condition_path}.family")
        for klass in _as_tuple(condition.get("class")):
            self._check_class(klass, f"{condition_path}.class")
        if "trait" in condition:
            self.context.error(
                f"{condition_path}.trait",
                "trait qualifiers in conditions are not representable in the frozen M1 model contract (Condition has no trait axis); use a predicate class or a rune-local group",
            )

    def _check_condition_refs(self, raw: dict, path: str) -> None:
        for condition_path, condition in _walk_conditions(raw, path):
            self._check_condition_atom(condition, condition_path)

    def _check_when_refs(self, raw: dict | None, path: str) -> None:
        if not raw:
            return
        for condition_path, condition in _when_conditions(raw, path):
            self._check_condition_atom(condition, condition_path)
        feature = raw.get("feature")
        if feature is not None and feature not in self.features:
            self.context.error(f"{path}.feature", f"feature {feature!r} is not registered in script.yaml")

    def _lint_stances(self) -> None:
        for stance_name, stance_raw in (self.raw.get("stances") or {}).items():
            base = f"stances.{stance_name}"
            bitmaps = set(stance_raw.get("bitmaps") or {})
            surface = stance_raw.get("surface") or {}
            for height, row in (surface.get("entries") or {}).items():
                row_path = f"{base}.surface.entries.{height}"
                self._check_condition_refs_list(row.get("from"), f"{row_path}.from")
                joined = row.get("joined")
                if joined is not None and joined not in bitmaps:
                    self.context.error(
                        f"{row_path}.joined", f"joined binding {joined!r} names no bitmaps: sibling"
                    )
            for height, row in (surface.get("exits") or {}).items():
                row_path = f"{base}.surface.exits.{height}"
                self._check_condition_refs_list(row.get("toward"), f"{row_path}.toward")
                withdrawal = row.get("withdrawal")
                if withdrawal not in (None, "safe") and withdrawal not in bitmaps:
                    self.context.error(
                        f"{row_path}.withdrawal",
                        f"withdrawal binding {withdrawal!r} names no bitmaps: sibling",
                    )
            entries = self.stance_rows[stance_name]["entry"]
            exits = self.stance_rows[stance_name]["exit"]
            pairings = surface.get("pairings") or {}
            for group_key in ("never", "only"):
                for index, pair in enumerate(pairings.get(group_key, ()) or ()):
                    for side, declared in (("entry", entries), ("exit", exits)):
                        height = pair.get(side)
                        if height not in (None, "none") and height not in declared:
                            warnings.warn(
                                f"{self.context.file}: {base}.surface.pairings.{group_key}[{index}] names {side} {height!r}, which {stance_name!r} never declares; the pairing is vacuous",
                                SpecWarning,
                                stacklevel=2,
                            )
            for index, cell in enumerate(surface.get("cells", ())):
                cell_path = f"{base}.surface.cells[{index}]"
                if cell.get("bitmap") not in bitmaps:
                    self.context.error(
                        f"{cell_path}.bitmap",
                        f"cell binding {cell.get('bitmap')!r} names no bitmaps: sibling",
                    )
                for side, declared in (("entry", entries), ("exit", exits)):
                    state = cell.get(side)
                    height = state.removesuffix("-withdrawn") if isinstance(state, str) else state
                    if height not in (None, "none") and height not in declared:
                        self.context.error(
                            f"{cell_path}.{side}",
                            f"cell binding references {side} {state!r}, but {stance_name!r} declares no {side} row at {height!r}",
                        )
            for index, unlock in enumerate(surface.get("unlocks", ())):
                unlock_path = f"{base}.surface.unlocks[{index}]"
                feature = unlock.get("feature")
                if feature not in self.features:
                    self.context.error(
                        f"{unlock_path}.feature", f"feature {feature!r} is not registered in script.yaml"
                    )
                self._check_when_refs(unlock.get("when"), f"{unlock_path}.when")
                pairing = unlock.get("pairing")
                if pairing:
                    for side, declared in (("entry", entries), ("exit", exits)):
                        height = pairing.get(side)
                        if height not in (None, "none") and height not in declared:
                            self.context.error(
                                f"{unlock_path}.pairing.{side}",
                                f"unlock pairing references {side} {height!r}, but {stance_name!r} declares no {side} row there",
                            )

    def _check_condition_refs_list(self, scope: list | None, path: str) -> None:
        for index, condition in enumerate(scope or ()):
            self._check_condition_refs(condition, f"{path}[{index}]")

    def _lint_policy(self) -> None:
        policy = self.raw.get("policy") or {}
        stances = set(self.raw.get("stances") or {})
        for position, stance_name in enumerate(policy.get("order", ())):
            if stance_name not in stances:
                self.context.error(f"policy.order[{position}]", f"order names unknown stance {stance_name!r}")
        for kind in _RECORD_KINDS:
            for index, record in enumerate(policy.get(kind, ())):
                record_path = f"policy.{kind}[{index}]"
                self._lint_record(kind, record, record_path, stances)
        for index, record in enumerate(policy.get("resolve", ())):
            self.context.error(
                f"policy.resolve[{index}]",
                "resolve records are not representable in the frozen M1 model contract; M1 expects zero (M1-PLAN section 5)",
            )
        for group_name, group in (policy.get("groups") or {}).items():
            group_path = f"policy.groups.{group_name}"
            if group_name in self.classes:
                self.context.error(group_path, f"group {group_name!r} shadows a registry predicate class")
            for part in ("union", "minus"):
                for index, atom in enumerate(group.get(part, ()) or ()):
                    atom_path = f"{group_path}.{part}[{index}]"
                    for family in _as_tuple(atom.get("family")):
                        self._check_family(family, f"{atom_path}.family")
                    for klass in _as_tuple(atom.get("class")):
                        if klass not in self.classes:
                            self.context.error(
                                f"{atom_path}.class",
                                f"group atom class {klass!r} is not a registry predicate class",
                            )

    def _lint_record(self, kind: str, record: dict, record_path: str, stances: set[str]) -> None:
        when = record.get("when") or {}
        self._check_when_refs(when, f"{record_path}.when")
        stance_name = record.get("stance")
        if stance_name is not None and stance_name not in stances:
            self.context.error(f"{record_path}.stance", f"record names unknown stance {stance_name!r}")
        for side in ("entry", "exit"):
            height = record.get(side)
            if height is None:
                continue
            offering = [name for name, rows in self.stance_rows.items() if height in rows[side]]
            if stance_name is not None:
                if stance_name in stances and height not in self.stance_rows[stance_name][side]:
                    self.context.error(
                        f"{record_path}.{side}",
                        f"stance {stance_name!r} declares no {side} row at {height!r} (unlock rows included)",
                    )
            elif not offering:
                self.context.error(f"{record_path}.{side}", f"no stance declares a {side} row at {height!r}")
            elif len(offering) > 1 and kind in ("extend", "contract"):
                self.context.error(
                    f"{record_path}.{side}",
                    f"{kind} target is ambiguous: stances {sorted(offering)} all offer {side} {height!r}; declare stance: explicitly (refuse-to-guess, doc/rebuild-design.md section 3.3)",
                )
        bind = record.get("bind")
        if bind is not None:
            owners = [stance_name] if stance_name else sorted(stances)
            stance_bitmaps = {
                name: set(((self.raw.get("stances") or {}).get(name) or {}).get("bitmaps") or {})
                for name in owners
            }
            if not any(bind in named for named in stance_bitmaps.values()):
                self.context.error(
                    f"{record_path}.bind", f"bind {bind!r} names no bitmaps: sibling of the targeted stance"
                )


def _stance_satisfies(expression: dict, rune_raw: dict, stance_raw: dict, is_ligature: bool) -> bool:
    if "can_enter_at" in expression:
        entries = ((stance_raw.get("surface") or {}).get("entries")) or {}
        row = entries.get(expression["can_enter_at"])
        return row is not None and row.get("selectable", True)
    if "can_exit_at" in expression:
        exits = ((stance_raw.get("surface") or {}).get("exits")) or {}
        return expression["can_exit_at"] in exits
    if "trait" in expression:
        return expression["trait"] in (stance_raw.get("traits") or ())
    if "height_class" in expression:
        if is_ligature:
            return False
        rows = len(stance_raw.get("bitmap") or ())
        y_offset = stance_raw.get("y_offset", 0)
        wanted = expression["height_class"]
        if wanted == "tall":
            return rows == 9 and y_offset == 0
        if wanted == "short":
            return rows == 6 and y_offset == 0
        return rows == 9 and y_offset == -3
    if "stroke_at" in expression:
        surface = stance_raw.get("surface") or {}
        for side, key in (("entry", "entries"), ("exit", "exits")):
            wanted = expression["stroke_at"].get(side)
            if wanted is None:
                continue
            if not any(row.get("stroke") == wanted for row in (surface.get(key) or {}).values()):
                return False
        return True
    if "all" in expression:
        return all(_stance_satisfies(sub, rune_raw, stance_raw, is_ligature) for sub in expression["all"])
    if "union" in expression:
        return any(_stance_satisfies(sub, rune_raw, stance_raw, is_ligature) for sub in expression["union"])
    raise ValueError(f"unsupported predicate-class expression {expression!r}")


def _evaluate_predicate_classes(registry_raw: dict, rune_raws: dict[str, dict]) -> dict[str, frozenset[str]]:
    members: dict[str, frozenset[str]] = {}
    for class_name, expression in (registry_raw.get("predicate_classes") or {}).items():
        matched = set()
        for rune_name, rune_raw in rune_raws.items():
            is_ligature = "sequence" in rune_raw
            for stance_raw in (rune_raw.get("stances") or {}).values():
                if _stance_satisfies(expression, rune_raw, stance_raw, is_ligature):
                    matched.add(rune_name)
                    break
        members[class_name] = frozenset(matched)
    return members


def _resolve_groups(
    context: _FileContext, policy_raw: dict, classes: dict[str, frozenset[str]]
) -> dict[str, frozenset[str]]:
    resolved: dict[str, frozenset[str]] = {}
    for group_name, group in (policy_raw.get("groups") or {}).items():
        members: set[str] = set()
        for atom in group.get("union", ()) or ():
            if atom.get("trait") or atom.get("stance"):
                warnings.warn(
                    f"{context.file}: policy.groups.{group_name} carries a trait/stance qualifier; the frozen model resolves groups at family grain, so the qualifier is widened away",
                    SpecWarning,
                    stacklevel=2,
                )
            members.update(_as_tuple(atom.get("family")))
            for klass in _as_tuple(atom.get("class")):
                members.update(classes.get(klass, frozenset()))
        for atom in group.get("minus", ()) or ():
            removed = set(_as_tuple(atom.get("family")))
            for klass in _as_tuple(atom.get("class")):
                removed.update(classes.get(klass, frozenset()))
            if not (atom.get("trait") or atom.get("stance")):
                members -= removed
            else:
                warnings.warn(
                    f"{context.file}: policy.groups.{group_name} subtracts a qualified atom; family-grain resolution keeps the families (the qualifier cannot be honored)",
                    SpecWarning,
                    stacklevel=2,
                )
        resolved[group_name] = frozenset(members)
    return resolved


def _build_rune(context: _FileContext, classes: dict[str, frozenset[str]]) -> Rune:
    raw = context.data
    ductus = dict(raw.get("ductus") or {})
    stances = {}
    for stance_name, stance_raw in (raw.get("stances") or {}).items():
        base_path = f"stances.{stance_name}"
        stances[stance_name] = Stance(
            name=stance_name,
            motion=stance_raw["motion"],
            traits=tuple(stance_raw.get("traits") or ()),
            bitmap=Bitmap(rows=tuple(stance_raw["bitmap"]), y_offset=stance_raw.get("y_offset", 0)),
            bitmaps={name: _bitmap(drawing) for name, drawing in (stance_raw.get("bitmaps") or {}).items()},
            surface=_surface(stance_raw.get("surface"), context, f"{base_path}.surface"),
        )
    policy_raw = raw.get("policy") or {}
    policy = Policy(
        order=tuple(policy_raw.get("order") or ()),
        refuse=tuple(
            _policy_record("refuse", record, context.provenance(f"policy.refuse[{index}]"))
            for index, record in enumerate(policy_raw.get("refuse", ()))
        ),
        prefer=tuple(
            _policy_record("prefer", record, context.provenance(f"policy.prefer[{index}]"))
            for index, record in enumerate(policy_raw.get("prefer", ()))
        ),
        extend=tuple(
            _policy_record("extend", record, context.provenance(f"policy.extend[{index}]"))
            for index, record in enumerate(policy_raw.get("extend", ()))
        ),
        contract=tuple(
            _policy_record("contract", record, context.provenance(f"policy.contract[{index}]"))
            for index, record in enumerate(policy_raw.get("contract", ()))
        ),
        groups=_resolve_groups(context, policy_raw, classes),
    )
    return Rune(
        name=raw["rune"],
        codepoint=raw.get("codepoint"),
        sequence=tuple(raw["sequence"]) if raw.get("sequence") else None,
        ductus=ductus,
        notes=raw.get("notes"),
        mono=_bitmap(raw["mono"]) if raw.get("mono") else None,
        stances=stances,
        policy=policy,
    )


def _build_registry(raw: dict, classes: dict[str, frozenset[str]]) -> ScriptRegistry:
    features_raw = dict(raw.get("features") or {})
    interactions = tuple(tuple(group) for group in features_raw.pop("interactions", ()))
    return ScriptRegistry(
        heights=dict(raw["heights"]),
        boundary_tokens={
            name: BoundaryToken(codepoint=token["codepoint"], splits_runs=token["splits_runs"])
            for name, token in (raw.get("boundary_tokens") or {}).items()
        },
        features={
            tag: FeatureInfo(
                kind=info["kind"], description=info.get("description", ""), overlay=info.get("overlay")
            )
            for tag, info in features_raw.items()
        },
        interactions=interactions,
        predicate_classes=classes,
        families={
            name: FamilyInfo(
                codepoint=info.get("codepoint"),
                sequence=tuple(info["sequence"]) if info.get("sequence") else None,
            )
            for name, info in (raw.get("families") or {}).items()
        },
    )


def _flag_duplicate_groups(contexts: list[_FileContext], runes: dict[str, Rune]) -> None:
    seen: dict[frozenset[str], list[tuple[str, str]]] = {}
    for rune in runes.values():
        for group_name, members in rune.policy.groups.items():
            seen.setdefault(members, []).append((rune.name, group_name))
    for members, owners in seen.items():
        if len(owners) > 1:
            described = ", ".join(f"{rune}.{group}" for rune, group in owners)
            warnings.warn(
                f"rune-local groups with identical membership across files: {described}; candidates for a predicate class (doc/rebuild-design.md section 2)",
                SpecWarning,
                stacklevel=2,
            )


def load_spec(runes_dir: Path, registry_path: Path, schema_dir: Path) -> ResolvedSpec:
    issues: list[SpecIssue] = []
    rune_checker = _load_schema(schema_dir, "rune.schema.json")
    script_checker = _load_schema(schema_dir, "script.schema.json")

    registry_context = _FileContext(registry_path, issues)
    if not isinstance(registry_context.data, dict):
        registry_context.error("", "the registry must be a YAML mapping")
        raise SpecError.from_issues(issues)
    for path, message in script_checker.check(registry_context.data):
        registry_context.error(path, message)
    registry_clean = not issues

    rune_paths = sorted(runes_dir.glob("*.yaml"))
    if not rune_paths:
        raise SpecError(str(runes_dir), "", f"no rune files found under {runes_dir}")
    contexts: list[_FileContext] = []
    schema_clean: dict[int, bool] = {}
    for path in rune_paths:
        context = _FileContext(path, issues)
        if not isinstance(context.data, dict):
            context.error("", "a rune file must be a YAML mapping")
            continue
        schema_errors = rune_checker.check(context.data)
        for error_path, message in schema_errors:
            context.error(error_path, message)
        schema_clean[id(context)] = not schema_errors
        contexts.append(context)
    if not registry_clean:
        raise SpecError.from_issues(issues)

    registry_raw = registry_context.data
    registry_families = registry_raw.get("families") or {}
    registry_classes = registry_raw.get("predicate_classes") or {}
    registry_features = {tag for tag in (registry_raw.get("features") or {}) if tag != "interactions"}
    for group in (registry_raw.get("features") or {}).get("interactions", ()):
        for tag in group:
            if tag not in registry_features:
                registry_context.error(
                    "features.interactions", f"interaction names unregistered feature {tag!r}"
                )
    for family_name, info in registry_families.items():
        for position, member in enumerate(info.get("sequence") or ()):
            if member not in registry_families:
                registry_context.error(
                    f"families.{family_name}.sequence[{position}]",
                    f"unknown family {member!r} in ligature sequence",
                )

    for context in contexts:
        linter = _Linter(context, registry_families, registry_classes, registry_features)
        linter.run_shallow()
        if schema_clean[id(context)]:
            linter.run_deep()
    if issues:
        raise SpecError.from_issues(issues)

    rune_raws = {context.data["rune"]: context.data for context in contexts}
    if len(rune_raws) != len(contexts):
        names = [context.data["rune"] for context in contexts]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise SpecError(str(runes_dir), "", f"duplicate rune files for {duplicates}")

    classes = _evaluate_predicate_classes(registry_raw, rune_raws)
    runes = {context.data["rune"]: _build_rune(context, classes) for context in contexts}
    if issues:
        raise SpecError.from_issues(issues)
    _flag_duplicate_groups(contexts, runes)
    registry = _build_registry(registry_raw, classes)
    return ResolvedSpec(runes=runes, registry=registry)


def load_default_spec() -> ResolvedSpec:
    return load_spec(DEFAULT_RUNES_DIR, DEFAULT_REGISTRY_PATH, DEFAULT_SCHEMA_DIR)
