"""Apply the checked-in standing approvals (rebuild/standing-approvals.yaml) to the live review surface: for every rule, find the blank human units whose before→after delta matches the rule's pattern and emit fill records for them into an importable verdicts file. Three delta shapes are expressible, and a rule declares exactly one of them — which one is keyed by the field its `match.after` carries. The `ligature` shape is a pivot letter whose backward join drops as it ligates with its follower; it holds the seams flanking the delta fixed. The `follower_cells` shape is a pivot letter that gives up a named exit extension: the two sides must line up letter for letter over an identical seam vector, the pivot and the follower must settle into cells the rule names in full — rune, stance, entry, exit and the whole adjustment set — and the unit's own primary judged adjacency must be exactly that pivot–follower seam with no secondary seam anywhere else in the window. That last requirement is the load-bearing one, because an unchanged seam vector is not unchanged ink: a window can hold every seam still and be asking about a different letter's stroke entirely, and only the surface's own judgment fields say which letter the unit is about. The `ink_deltas` shape works from the opposite end and is ink-exact rather than structural: it names the surface's own per-config localized ink-delta digests (rebuild/review/ink.py's `delta_digest`, persisted on every unit), so a unit matches only when the window's entire before→after ink change, under every config it diverges on, is byte-identical to a blessed delta — every structural difference the unit still carries is then name-grain only, and any extra ink anywhere fails the match closed. Each shape's own docstring states exactly what it proves, and none claims to bound the window beyond that. Any rule's `except_left` family, met anywhere in the window, refuses the whole unit rather than the one position, so a guarded context can never ride along beside an unguarded one. This is the zero-touch sibling of echo_verdicts.py: echo fill extends the user's past verdicts to pixel-identical lookalikes, while a standing rule extends a recorded once-and-for-all decision to instances the user has never seen (new left letters minted by later migrations), so those units never queue. The guard list is the point of authoring a guarded rule at all: a rule's except_left families are held for review, so the one context the user does want to see still reaches the docket. Records are stamped with the manifest's generated_at, so any human verdict beats a standing fill on merge, and a parked unit (a skip verdict) is not blank and is never filled. The artifact cycle runs this after the echo fill, with a merge_verdicts pass to land the file."""

import argparse
import json
import pathlib
import re
import sys
from collections.abc import Callable
from typing import NamedTuple, NoReturn

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.ink import delta_digest  # noqa: E402
from rebuild.tools.echo_verdicts import latest_verdicts, load_units  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
RULES = ROOT / "rebuild/standing-approvals.yaml"
OUT = ROOT / "verdicts-standing-fill.json"
FORMAT = "ams-standing-approvals/1"
ALLOWED_VERDICTS = ("approve", "either")
CELL_FIELDS = 5
EXIT_EXTENSION = re.compile(r"ex-ext-[1-9][0-9]*")
DELTA_DIGEST = re.compile(r"d-[0-9a-f]{12}")
EMPTY_DELTA_DIGEST = delta_digest(((), (), 0))


def _fail(message) -> NoReturn:
    raise SystemExit(f"rebuild/standing-approvals.yaml: {message}")


def _family(glyph_name):
    """The whole family of an old-font glyph name — everything before the first modifier dot, a ligature's compound name included (`qsTea_qsOy.en-y0` reads `qsTea_qsOy`)."""
    return glyph_name.split(".", 1)[0]


def _joining_family(glyph_name):
    """The single family at the end of an old-font glyph name (`qsDay_qsMay.alt` reads `qsMay`). On a left neighbor this is the letter whose stroke actually touches the pivot, which is what makes it the right reading for the except_left guard."""
    return _family(glyph_name).rsplit("_", 1)[-1]


def _modifiers(glyph_name):
    """The dot-separated modifier tokens of an old-font glyph name (`qsTea.en-y8.ex-ext-1` reads `['en-y8', 'ex-ext-1']`)."""
    return glyph_name.split(".")[1:]


def _is_pivot(glyph_name, pivot):
    return glyph_name == pivot or glyph_name.startswith(pivot + ".")


def _cell_parts(token):
    """The five slash-separated fields of a review-surface cell string: rune, stance, entry, exit, and the +-joined adjustments, which are often empty."""
    return token.split("/")


def _cell_rune(token):
    return _cell_parts(token)[0]


def _cell_adjustments(token):
    parts = _cell_parts(token)
    return parts[4].split("+") if len(parts) > 4 and parts[4] else []


def _is_cell(token):
    parts = _cell_parts(token) if isinstance(token, str) else []
    return len(parts) == CELL_FIELDS and all(parts[:4])


def _components(name):
    """How many input codepoints a glyph or cell name covers, counting a ligature's underscore-joined members."""
    return name.count("_") + 1


def _letter_for_letter(unit):
    """Whether a before-glyph index and an after-cell index name the same letters all the way along the window, which is what the pivot/follower comparisons and the surface's own after-indexed `pair` both rely on. The two sides line up exactly when they merge the same codepoints at the same positions; requiring each side's components to sum to the window's own codepoint count makes the check fail closed should a name ever spell something other than a ligature."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    before = [_components(_family(name)) for name in unit["before"]["glyphs"]]
    after = [_components(_cell_rune(cell)) for cell in unit["after"]["cells"]]
    return before == after and sum(before) == len(codepoints.split(":"))


def _matches_ligature(match, unit, excluded):
    """A pivot letter whose backward join drops as it ligates with its follower: the pivot sits between the two named seams, the follower is swallowed into the named ligature, and the seams flanking the whole delta are unchanged. Unchanged flanking seams bound the join structure and nothing more — they do not prove the unit's judged question is this pivot's — so this shape is only as safe as the single checked-in rule that uses it, and a second rule in this shape wants the localization refusals the extension shape carries. The follower is read here as the right neighbor's `_joining_family`, where the extension shape reads the whole `_family`; the two can only disagree when that neighbor is itself a ligature, and ligature formation is this shape's whole subject, so it keeps the reading it shipped with."""
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    mb, ma = match["before"], match["after"]
    hits = [
        i
        for i in range(1, len(glyphs) - 1)
        if _is_pivot(glyphs[i], mb["pivot"])
        and seams[i - 1] == mb["seam_into"]
        and seams[i] == mb["seam_out"]
        and _joining_family(glyphs[i + 1]) == mb["follower"]
    ]
    if any(_joining_family(glyphs[i - 1]) in excluded for i in hits):
        return False
    for i in hits:
        for j in range(1, len(cells)):
            if _cell_rune(cells[j]) != ma["ligature"]:
                continue
            if after_seams[j - 1] != ma["seam_into"]:
                continue
            if seams[: i - 1] == after_seams[: j - 1] and seams[i + 1 :] == after_seams[j:]:
                return True
    return False


def _matches_extension(match, unit, excluded):
    """A pivot letter that gives up the named exit extension into a seam that holds its named height, with the whole seam vector standing still and the pivot and follower settling into cells the rule names in full. Naming the cells in full is what makes the delta exact: rune, stance, entry and exit pin the bitmap binding on both sides of the seam, and the whole adjustment set pins what the pivot is left carrying, so an extension traded for a shorter one is never read as an extension dropped. Because an unchanged seam vector says nothing about ink elsewhere, localization is taken from the surface's own judgment fields rather than inferred: the unit's primary judged adjacency must be exactly this pivot–follower seam, and any window carrying a secondary seam is visibly asking about somewhere else too and is refused outright. Nothing ligates here — that is enforced, not assumed — which is also why the follower's whole name is the right thing to compare against: a ligature in that slot breaks the letter-for-letter requirement and never reaches this loop. A word-initial pivot has no left neighbor and so nothing for except_left to hold."""
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    mb, ma = match["before"], match["after"]
    if seams != after_seams or not _letter_for_letter(unit):
        return False
    extension = mb["exit_extension"]
    hits = [
        i
        for i in range(len(glyphs) - 1)
        if _is_pivot(glyphs[i], mb["pivot"])
        and extension in _modifiers(glyphs[i])
        and seams[i] == mb["seam_out"]
        and cells[i] in ma["pivot_cells"]
        and _family(glyphs[i + 1]) == mb["follower"]
        and cells[i + 1] in ma["follower_cells"]
    ]
    if any(i and _joining_family(glyphs[i - 1]) in excluded for i in hits):
        return False
    if unit.get("secondary_seams"):
        return False
    return any(unit.get("pair") == {"left": i, "right": i + 1} for i in hits)


def _matches_ink_delta(match, unit, excluded):
    """A window whose entire before→after ink change is one the user has blessed: the unit's persisted `ink_deltas` — one digest per config with any ink change, computed by the surface build over InkComparator.config_diff — must be a nonempty subset of the rule's named digests. Matching asserts exactly what the digest asserts: once unchanged flanks and rigidly-slid followers are stripped, the pixels that appear and disappear are the blessed ones and nothing else, under every config the unit diverges on — so every other difference the unit carries is name-grain only, and a window showing any unlisted ink change under any config fails closed. No judged-pair localization is needed because the delta is the whole window's ink change by construction. There is no pivot position either, so except_left reads against the whole window: an excluded family joining anywhere in it refuses the unit."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if not set(deltas.values()) <= set(match["after"]["ink_deltas"]):
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_ink_delta(rule_id, match) -> None:
    """The ink-delta shape's own coherence, checked once at load: no digest may repeat, and none may be the empty delta — an ink-identical window is machine-approved already, so a rule blessing it could only ever mask a digest typo."""
    digests = match["after"]["ink_deltas"]
    if len(set(digests)) != len(digests):
        _fail(f"rule {rule_id!r}: match.after.ink_deltas repeats a digest")
    if EMPTY_DELTA_DIGEST in digests:
        _fail(
            f"rule {rule_id!r}: match.after.ink_deltas names the empty delta {EMPTY_DELTA_DIGEST}; "
            "an ink-identical window is machine-approved and never needs a rule"
        )


def _validate_extension(rule_id, match) -> None:
    """The extension shape's own coherence, checked once at load so a rule can never quietly mean something else: the named extension has to be an exit-side one, since an entry-side token would pin the seam on the far side of the pivot from the `seam_out` the rule names; the cells have to belong to the letters the rule names; and no pivot cell may still carry an exit extension, because this shape speaks for an extension that is gone and never for one traded in for a shorter one."""
    extension = match["before"]["exit_extension"]
    if not EXIT_EXTENSION.fullmatch(extension):
        _fail(
            f"rule {rule_id!r}: match.before.exit_extension names {extension!r}, which is not an exit-side "
            "extension (ex-ext-N); an entry-side token would pin the seam on the other side of the pivot"
        )
    named = (
        ("pivot_cells", _family(match["before"]["pivot"])),
        ("follower_cells", match["before"]["follower"]),
    )
    for field, rune in named:
        for cell in match["after"][field]:
            if _cell_rune(cell) != rune:
                _fail(f"rule {rule_id!r}: match.after.{field} entry {cell!r} is not a {rune} cell")
    for cell in match["after"]["pivot_cells"]:
        kept = [token for token in _cell_adjustments(cell) if EXIT_EXTENSION.fullmatch(token)]
        if kept:
            _fail(
                f"rule {rule_id!r}: match.after.pivot_cells entry {cell!r} still carries {', '.join(kept)}; "
                "this shape speaks only for an exit extension the pivot has given up"
            )


class Shape(NamedTuple):
    """One expressible delta shape: the match.after field that declares it, the field names match.before and match.after must carry exactly (an empty tuple means the block itself must be absent), which of those fields are lists of cell strings or of delta digests rather than plain scalars, the matcher that reads a unit for it, and its own coherence check."""

    keyed_by: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    cell_lists: tuple[str, ...]
    matcher: Callable[[dict, dict, set[str]], bool]
    validate: Callable[[str, dict], None] | None = None
    digest_lists: tuple[str, ...] = ()


SHAPES = {
    "ligature": Shape(
        keyed_by="ligature",
        before=("pivot", "seam_into", "seam_out", "follower"),
        after=("ligature", "seam_into"),
        cell_lists=(),
        matcher=_matches_ligature,
    ),
    "extension-dropped": Shape(
        keyed_by="follower_cells",
        before=("pivot", "exit_extension", "seam_out", "follower"),
        after=("pivot_cells", "follower_cells"),
        cell_lists=("pivot_cells", "follower_cells"),
        matcher=_matches_extension,
        validate=_validate_extension,
    ),
    "ink-delta": Shape(
        keyed_by="ink_deltas",
        before=(),
        after=("ink_deltas",),
        cell_lists=(),
        matcher=_matches_ink_delta,
        validate=_validate_ink_delta,
        digest_lists=("ink_deltas",),
    ),
}


def load_rules(path) -> list:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        _fail(f"format must be {FORMAT!r}")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        _fail("rules must be a nonempty list")
    seen = set()
    for rule in rules:
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            _fail("every rule needs a nonempty string id")
        if rule_id in seen:
            _fail(f"duplicate rule id {rule_id!r}")
        seen.add(rule_id)
        if rule.get("verdict") not in ALLOWED_VERDICTS:
            _fail(f"rule {rule_id!r}: verdict must be one of {ALLOWED_VERDICTS}")
        if not isinstance(rule.get("note"), str) or not rule["note"]:
            _fail(f"rule {rule_id!r}: note must be a nonempty string")
        match = rule.get("match")
        if not isinstance(match, dict):
            _fail(f"rule {rule_id!r}: match must be a mapping")
        after = match.get("after")
        if not isinstance(after, dict):
            _fail(f"rule {rule_id!r}: match.after must be a mapping")
        declared = [name for name, shape in SHAPES.items() if shape.keyed_by in after]
        if len(declared) != 1:
            keyed = ", ".join(f"{shape.keyed_by} for the {name} shape" for name, shape in SHAPES.items())
            _fail(
                f"rule {rule_id!r}: match.after must declare exactly one delta shape "
                f"({keyed}); it declares {len(declared)}"
            )
        shape = SHAPES[declared[0]]
        for block, fields in (("before", shape.before), ("after", shape.after)):
            if not fields:
                if block in match:
                    _fail(f"rule {rule_id!r}: the {declared[0]} shape carries no match.{block} block")
                continue
            got = match.get(block)
            if not isinstance(got, dict) or set(got) != set(fields):
                _fail(
                    f"rule {rule_id!r}: the {declared[0]} shape needs match.{block} to be exactly "
                    f"{', '.join(fields)}"
                )
            for field in fields:
                value = got[field]
                if field in shape.cell_lists:
                    if not isinstance(value, list) or not value or not all(_is_cell(cell) for cell in value):
                        _fail(
                            f"rule {rule_id!r}: match.{block}.{field} must be a nonempty list of "
                            "rune/stance/entry/exit/adjustments cell strings"
                        )
                elif field in shape.digest_lists:
                    if (
                        not isinstance(value, list)
                        or not value
                        or not all(isinstance(item, str) and DELTA_DIGEST.fullmatch(item) for item in value)
                    ):
                        _fail(
                            f"rule {rule_id!r}: match.{block}.{field} must be a nonempty list of "
                            "d- ink-delta digests"
                        )
                elif not isinstance(value, str) or not value:
                    _fail(f"rule {rule_id!r}: match.{block}.{field} must be a nonempty string")
        if shape.validate is not None:
            shape.validate(rule_id, match)
        except_left = match.get("except_left", [])
        if not isinstance(except_left, list) or not all(
            isinstance(family, str) and family for family in except_left
        ):
            _fail(f"rule {rule_id!r}: match.except_left must be a list of family names")
    return rules


def _matches(match, unit, *, guard=True):
    before, after = unit.get("before"), unit.get("after")
    if not before or not after:
        return False
    excluded = set(match.get("except_left", [])) if guard else set()
    for shape in SHAPES.values():
        if shape.keyed_by in match["after"]:
            return shape.matcher(match, unit, excluded)
    return False


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument(
        "verdicts", help="the verdicts file that defines blankness (an export or the autosave)"
    )
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--rules", default=str(RULES))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    data = json.loads(pathlib.Path(args.verdicts).read_text())
    if data.get("manifest_generated_at") != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the surface is "
            f"{manifest['generated_at']}; unit ids must never be joined across manifests — carry it forward first"
        )
    rules = load_rules(pathlib.Path(args.rules))
    records = latest_verdicts(pathlib.Path(args.verdicts))
    units = [
        unit
        for unit in load_units(surface)
        if not unit.get("no_verdict") and len(unit.get("render_groups") or []) == 1
    ]
    wants_deltas = any(SHAPES["ink-delta"].keyed_by in rule["match"]["after"] for rule in rules)
    if wants_deltas and not any("ink_deltas" in unit for unit in units):
        raise SystemExit(
            "the surface carries no ink_deltas fields, so it predates the ink-delta shape; an ink-delta "
            "rule cannot match anything on it — rebuild the surface (make review-cycle) first"
        )

    fills = []
    lines = []
    for rule in rules:
        matched = [unit for unit in units if _matches(rule["match"], unit)]
        held = [
            unit
            for unit in units
            if _matches(rule["match"], unit, guard=False) and not _matches(rule["match"], unit)
        ]
        blanks = [unit for unit in matched if unit["id"] not in records]
        note = f"[standing: {rule['id']}] {rule['note']}"
        for unit in blanks:
            fills.append(
                {
                    "unit": unit["id"],
                    "verdict": rule["verdict"],
                    "note": note,
                    "at": manifest["generated_at"],
                }
            )
        lines.append(
            f"  {rule['id']}: {len(blanks)} filled, {len(matched) - len(blanks)} already verdicted, "
            f"{len(held)} held for review by except_left"
        )

    fills.sort(key=lambda record: record["unit"])
    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": fills,
    }
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        f"wrote {out.name}: {len(fills)} standing-approval verdicts onto manifest {manifest['generated_at']}"
    )
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
