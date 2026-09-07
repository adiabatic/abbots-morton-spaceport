"""Explain why a review-surface unit still queues, in the standing approvals' own terms, so the next once-and-for-all rule is written from evidence instead of rediscovered: for each unit named, print its two grains side by side — the recorded before glyphs and after cells with their seams, and the rendered pieces of both fonts with each piece's placement, own-frame origin and cell count, read as "same shape placed N columns over", "redrawn", or "inkless" — then say what every checked-in rule makes of it (matches, held by except_left, or nothing), whether the composed reading credits any rules and whether that credit reaches the two-rule threshold, and how many human units share exactly this unit's ink-delta digests and how they were verdicted, which is where the user's earlier decision usually turns out to be already recorded. `--extension-cells PIVOT TOKEN SEAM` answers the other question a new extension-dropped rule always asks — which pivot and follower cells it has to name in full — by enumerating every window on the surface where a PIVOT glyph carrying TOKEN (an `ex-ext-N` on the before glyph, or an `ex-con-N` on the after cell whose before glyph never carried an exit extension) exits at SEAM on both sides and settles into a cell without the named extension or with a shorter one, or into a cell carrying the named contraction, with the follower's family, both after cells, and the verdict tally per pair. `--retarget-cells PIVOT BEFORE_SEAM FOLLOWER AFTER_SEAM` is the same survey for a join-retargeted or join-created rule, over every window where a PIVOT glyph's seam into FOLLOWER moves from BEFORE_SEAM to AFTER_SEAM. `--coverage RULE_ID` turns either survey back on a rule that already exists: it re-runs whichever enumeration the rule's shape has, from the rule's own before-side fields and relaxed of everything the rule names, and reports the pivot forms, follower families and follower cells the enumeration reaches that the rule does not yet name, each with its verdict tally — a docket of candidates rather than a widening instruction, since a follower joins the list only once its own recorded decision has been found. `--find TEXT` is the way back from a notation to unit ids: a plain substring match over every human unit's notation, blanks first and capped, because one letter pair matches thousands of records; `--blank-only` and `--limit N` narrow it, and the notation's grammar is `parse_expect`'s in test/test_shaping.py, never re-read here. `--shapes` prints the symptom-to-shape menu, walked off the standing approvals' own SHAPES table so a new shape enters the menu the moment it enters the table, and a run that resolves no unit id prints it too. All the lists it prints — pivot cells, followers, follower cells — come out in code-point order, which is the order the rules file and the skill are written in. Read-only: nothing here writes to the surface or the store."""

import argparse
import collections
import json
import pathlib
import sys
from functools import cache
from typing import NamedTuple

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.ink import features_for  # noqa: E402
from rebuild.tools import standing_verdicts as sv  # noqa: E402
from rebuild.tools.review_docket import latest_verdicts, load_units  # noqa: E402
from rebuild.validation.classify import PIXEL_SIZE  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
VERDICTS = ROOT / "verdicts-autosave.json"
PS_NAMES = ROOT / "postscript_glyph_names.yaml"
UNNAMED_CODEPOINT = 0x110000
UNKNOWN_VERDICT = "UNKNOWN(stale-stamp)"
FIND_LIMIT = 20
NO_FONTS = (
    "this surface carries no fonts/before.otf + fonts/after.otf, so nothing below is read at the rendered "
    "grain: no piece placements, no own-frame origins, no cell counts, no reading of what moved, and no "
    "composed line — rebuild the surface (make review-cycle) to get them"
)
DOCKET_NOTE = (
    "  a docket, not an instruction to widen: each form, follower and cell above joins the rule only once "
    "its own recorded decision has been found — the verdict family, the rune edit, or the sitting that "
    "decided it"
)


@cache
def _family_codepoints():
    """The PostScript name to code point map, which is what lets every list this tool prints come out in the order the rules file and the skill are written in."""
    return yaml.safe_load(PS_NAMES.read_text())


def _codepoint_key(token):
    """A cell string or a bare family name ranked in code-point order: the code points of the rune's underscore-joined components, so a bare family precedes every ligature that leads with it, then the whole token, which orders two cells of one letter stably. A name the PostScript map does not cover sorts after every named one rather than breaking the listing."""
    codepoints = _family_codepoints()
    rune = sv._cell_rune(token)
    return ([codepoints.get(part, UNNAMED_CODEPOINT) for part in rune.split("_")], token)


class Blankness(NamedTuple):
    """What the verdicts file can say about this surface: the latest record per unit, and whether the file is stamped for another manifest — in which case it can answer nothing here, and every verdict reads as unknown-under-a-stale-stamp rather than as the positive claim BLANK."""

    records: dict
    stale: bool

    def of(self, unit_id):
        if self.stale:
            return UNKNOWN_VERDICT
        return self.records[unit_id]["verdict"] if unit_id in self.records else "BLANK"


def _human(units):
    return [
        unit
        for unit in units
        if not unit.get("no_verdict") and unit.get("batch") is not None and unit.get("render_groups") == 1
    ]


def _columns(value):
    return value // PIXEL_SIZE if value % PIXEL_SIZE == 0 else value / PIXEL_SIZE


def _piece_text(intern, piece):
    if piece is None:
        return "—"
    cells = intern.cells(piece[1])
    count = "?" if cells is None else str(len(cells))
    return f"x{_columns(piece[2])} y{_columns(piece[3])} o{_columns(piece[4])} {count}c"


def _reading(intern, before, after):
    """One position's rendered change in words: both pieces absent is inkless, one absent is ink appearing or vanishing, the same shape key is a placement (and possibly an own-frame origin) move, a different key is a redraw whose cell counts say how much and whose whole traded set is named — a redrawn rule is written from that trade, so a truncated one would have to be re-derived by hand, and this runs only for units the caller named."""
    if before is None and after is None:
        return "inkless"
    if before is None:
        return "ink appears"
    if after is None:
        return "ink vanishes"
    moved = f"placed {_columns(after[2] - before[2]):+} col"
    if before[3] != after[3]:
        moved += f", height {_columns(after[3] - before[3]):+} row"
    if before[1] == after[1]:
        origin = after[4] - before[4]
        return f"same shape, {moved}" + (f", origin {_columns(origin):+} col" if origin else "")
    painted, kept = intern.cells(before[1]), intern.cells(after[1])
    if painted is None or kept is None:
        return f"redrawn (curved or off-grid), {moved}"
    gone, gained = painted - kept, kept - painted
    dropped = " ".join(f"[{column}, {row}]" for column, row in sorted(gone)) or "nothing"
    added = " ".join(f"[{column}, {row}]" for column, row in sorted(gained)) or "nothing"
    trade = f" [dropped {dropped}; added {added}]"
    return f"redrawn {len(painted)}→{len(kept)} cells (−{len(gone)} +{len(gained)}), {moved}{trade}"


def _describe(unit, rules, context, blankness, families):
    verdict = blankness.of(unit["id"])
    deltas = unit.get("ink_deltas") or {}
    pair = unit.get("pair")
    pair_text = f"{pair['left']}–{pair['right']}" if pair else "none"
    print(f"{unit['id']}  {unit['class']}  echo {unit.get('echo')}  {unit['notation']}  {unit['codepoints']}")
    print(
        f"  configs {', '.join(unit['configs'])}   deltas {', '.join(sorted(set(deltas.values()))) or 'none'}"
        f"   pair {pair_text}   secondary seams {unit.get('secondary_seams')}"
        f"   verdict {verdict}"
    )
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    aligned = sv._letter_for_letter(unit)
    print(f"  letter for letter: {'yes' if aligned else 'no'}")
    before_pieces: dict = {}
    after_pieces: dict = {}
    intern = None
    if context is not None and aligned:
        text = "".join(chr(int(value, 16)) for value in unit["codepoints"].split(":"))
        features = features_for(unit["configs"][0])
        before_names, before_run = context.comparator.named_run("before", text, features)
        after_names, after_run = context.comparator.named_run("after", text, features)
        intern = context.comparator.intern
        if list(before_names) != glyphs:
            print(f"  the before font shapes this text as {list(before_names)}, not as recorded")
        if len(after_names) != len(cells):
            print(f"  the after font shapes this text as {list(after_names)}, {len(cells)} cells recorded")
        before_pieces = sv._pieces_by_glyph(before_names, before_run) or {}
        after_pieces = sv._pieces_by_glyph(after_names, after_run) or {}
    width = max(len(name) for name in glyphs)
    cell_width = max(len(cell) for cell in cells)
    for index, glyph in enumerate(glyphs):
        seam = seams[index] if index < len(seams) else ""
        after_seam = after_seams[index] if index < len(after_seams) else ""
        cell = cells[index] if index < len(cells) else "?"
        line = f"  {index}  {glyph:<{width}}  {seam:<5} {cell:<{cell_width}}  {after_seam:<5}"
        if intern is not None:
            before, after = before_pieces.get(index), after_pieces.get(index)
            line += f"  {_piece_text(intern, before):<22} {_piece_text(intern, after):<22} {_reading(intern, before, after)}"
        print(line)
    for rule in rules:
        if sv._matches(rule["match"], unit, context=context):
            print(f"  rule {rule['id']}: matches")
        elif not sv._guard_is_inert(rule["match"]) and sv._matches(
            rule["match"], unit, guard=False, context=context
        ):
            print(f"  rule {rule['id']}: held by except_left")
    composable = sv._composable(rules)
    if context is not None and len(composable) > 1:
        candidates = [rule["id"] for rule in composable if sv._candidates(rule["match"], unit)]
        credited = sv._composed_walk(composable, unit, context) if len(candidates) > 1 else None
        if credited:
            reach = (
                "reaches the two-rule threshold" if len(credited) > 1 else "one rule only, so its own line"
            )
            print(f"  composed: credits {' + '.join(credited)} ({reach})")
        else:
            print(f"  composed: nothing (candidates from {', '.join(candidates) or 'no rule'})")
    key = frozenset(deltas.values())
    if key:
        tally = collections.Counter(blankness.of(sibling["id"]) for sibling in families.get(key, []))
        print(f"  same deltas across the surface: {sum(tally.values())} human units — {dict(tally)}")
    print()


def _extension_pairs(units, blankness, pivot, token, seam):
    """Every window where a PIVOT glyph carrying TOKEN exits at SEAM on both sides and settles into a cell that has given up columns of it, keyed by (pivot cell, follower family, follower cell) with each key's verdict tally. It is relaxed of everything a rule could name past those three before-side fields — no follower list, no cell lists — which is what makes it both the survey a new extension-dropped rule is written from and the coverage check on one that already exists. That relaxation is why `standing_verdicts._candidates` cannot stand in for it: the extension branch there already filters on the rule's named followers and cells, so it can only ever confirm what the rule says."""
    pairs: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    contracted = bool(sv.EXIT_CONTRACTION.fullmatch(token))
    named = sv._extension_columns(token)
    for unit in units:
        if not sv._letter_for_letter(unit):
            continue
        glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
        cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
        for index in range(min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1):
            if not sv._is_pivot(glyphs[index], pivot):
                continue
            if seams[index] != seam or after_seams[index] != seam:
                continue
            if contracted:
                if not sv._carries_named_drop(token, glyphs[index], cells[index]):
                    continue
            else:
                if token not in sv._modifiers(glyphs[index]):
                    continue
                if sv._kept_extension(cells[index]) >= named:
                    continue
            key = (cells[index], sv._family(glyphs[index + 1]), cells[index + 1])
            pairs[key][blankness.of(unit["id"])] += 1
    return pairs


def _retarget_pairs(units, blankness, pivot, before_seam, follower, after_seam):
    """Every window where a PIVOT glyph's seam into a follower moves from BEFORE_SEAM to AFTER_SEAM, keyed the same way. `pivot` may be one glyph-name prefix or a list of them, as a join-created rule's own pivot side may be. `follower` may be None, which reaches every follower family — the relaxed form `--coverage` needs, since a rule that names its followers can never be told which ones it is missing by an enumeration that filters on them."""
    pairs: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    for unit in units:
        if not sv._letter_for_letter(unit):
            continue
        glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
        cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
        reach = min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1
        for index in range(reach):
            if not any(sv._is_pivot(glyphs[index], name) for name in sv._families(pivot)):
                continue
            family = sv._family(glyphs[index + 1])
            if follower is not None and family != follower:
                continue
            if seams[index] != before_seam or after_seams[index] != after_seam:
                continue
            pairs[(cells[index], family, cells[index + 1])][blankness.of(unit["id"])] += 1
    return pairs


def _tallied(pairs, position):
    """The distinct values one axis of a pairs table takes — pivot cell, follower family, or follower cell — each with the verdict tally of every window that reached it, in code-point order."""
    tallies: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for key, tally in pairs.items():
        tallies[key[position]].update(tally)
    return [(value, tallies[value]) for value in sorted(tallies, key=_codepoint_key)]


def _pair_lines(pairs):
    for key, tally in sorted(pairs.items(), key=lambda item: -sum(item[1].values())):
        pivot_cell, follower, follower_cell = key
        print(f"  {sum(tally.values()):>5}  {pivot_cell}  →  {follower}  {follower_cell}  {dict(tally)}")


def _extension_cells(units, blankness, pivot, token, seam):
    pairs = _extension_pairs(units, blankness, pivot, token, seam)
    drop = (
        f"into a cell carrying {token} or a longer contraction"
        if sv.EXIT_CONTRACTION.fullmatch(token)
        else "into a cell without it or with a shorter one"
    )
    print(f"windows where a {pivot} glyph carrying {token} exits at {seam} on both sides {drop}:")
    _pair_lines(pairs)
    print("pivot cells:", [value for value, _tally in _tallied(pairs, 0)])
    print("followers:", [value for value, _tally in _tallied(pairs, 1)])
    print("follower cells:", [value for value, _tally in _tallied(pairs, 2)])


def _retarget_cells(units, blankness, pivot, before_seam, follower, after_seam):
    pairs = _retarget_pairs(units, blankness, pivot, before_seam, follower, after_seam)
    print(f"windows where a {pivot} glyph's seam into {follower} moves from {before_seam} to {after_seam}:")
    for key, tally in sorted(pairs.items(), key=lambda item: -sum(item[1].values())):
        print(f"  {sum(tally.values()):>5}  {key[0]}  →  {key[2]}  {dict(tally)}")
    print("pivot cells:", [value for value, _tally in _tallied(pairs, 0)])
    print("follower cells:", [value for value, _tally in _tallied(pairs, 2)])


def _find(units, blankness, needle, blank_only, limit):
    """Every human unit whose notation contains the given text, blanks first and capped. The cap is load-bearing rather than tidy: one letter pair reaches thousands of records, so an uncapped listing is unreadable and the total is what the reader actually wants. Plain substring and nothing more — the `data-expect` grammar has an authority already (`parse_expect` in test/test_shaping.py) and a second reading of it here could only drift from it."""
    hits = [unit for unit in units if needle in (unit.get("notation") or "")]
    if blank_only and blankness.stale:
        print("--blank-only cannot be answered from a stale verdicts stamp; every match is listed instead")
        blank_only = False
    if blank_only:
        hits = [unit for unit in hits if blankness.of(unit["id"]) == "BLANK"]
    hits.sort(key=lambda unit: blankness.of(unit["id"]) != "BLANK")
    blanks = sum(1 for unit in hits if blankness.of(unit["id"]) == "BLANK")
    print(
        f"{len(hits)} human units whose notation contains {needle!r} ({blanks} blank); "
        f"showing {min(limit, len(hits))}, blanks first:"
    )
    for unit in hits[:limit]:
        print(f"  {unit['id']}  {blankness.of(unit['id'])}  {unit['class']}  {unit['notation']}")


def _shape_name(match):
    """Which SHAPES row a rule's match declares, by name. `standing_verdicts._shape_of` answers with the row itself; the name is what a message about the rule has to say."""
    return next((name for name, shape in sv.SHAPES.items() if shape.keyed_by in match["after"]), None)


def _symptom(matcher):
    """The symptom sentence a matcher's docstring opens with, cut at whichever comes first of its first colon and its first sentence end. Every matcher in SHAPES opens with one, which is what makes the derived listing a menu rather than a transcription of one."""
    text = (matcher.__doc__ or "").strip()
    cuts = [index for index in (text.find(":"), text.find(". ")) if index > 0]
    return text[: min(cuts)] if cuts else text


def _shapes():
    """The symptom-to-shape menu, walked off `standing_verdicts.SHAPES` at runtime: each row's name, the `match.after` field that declares it, and the symptom its matcher's docstring opens with. Nothing is transcribed, so a shape entering the table enters this menu with it, and the tool's module docstring stays the authority on what each shape proves."""
    print(
        "delta shapes a standing-approval rule can declare — standing_verdicts.py's module docstring is the "
        "authority on what each one proves:"
    )
    for name, shape in sv.SHAPES.items():
        print(f"  {name}  — declared by match.after.{shape.keyed_by}")
        print(f"      {_symptom(shape.matcher)}")


COVERAGE_SHAPES = {
    "extension-dropped": ("--extension-cells", "pivot_cells", "follower_cells"),
    "join-retargeted": ("--retarget-cells", "pivot_cells", "receiver_cells"),
    "join-created": ("--retarget-cells", "pivot_cells", "receiver_cells"),
}


def _coverage_pairs(units, blankness, shape, match):
    if shape == "extension-dropped":
        before = match["before"]
        return _extension_pairs(
            units, blankness, before["pivot"], before["exit_extension"], before["seam_out"]
        )
    target = "retarget" if shape == "join-retargeted" else "joined"
    return _retarget_pairs(
        units,
        blankness,
        match["before"]["pivot"],
        match["before"]["seam_out"],
        None,
        match["after"][target],
    )


def _coverage(units, blankness, rules, rule_id):
    """What a rule's own survey reaches that the rule does not yet name. It re-runs the relaxed enumeration for the rule's shape from the rule's before-side fields, then reports each pivot form, follower family and follower cell outside the rule's lists with the verdict tally of the windows that reached it — the docket a rule's next extension is argued from, one entry at a time and each still needing its own recorded decision."""
    rule = next((rule for rule in rules if rule["id"] == rule_id), None)
    if rule is None:
        print(f"{rule_id}: no rule by that id in this rules file")
        return
    match = rule["match"]
    shape = _shape_name(match)
    if shape not in COVERAGE_SHAPES:
        flags = ", ".join(f"{name} ({flag})" for name, (flag, *_) in COVERAGE_SHAPES.items())
        print(
            f"rule {rule_id!r} declares the {shape} shape, which has no relaxed enumeration to run: "
            f"--coverage answers for {flags}, the shapes whose surveys enumerate cells at all"
        )
        return
    _flag, pivot_field, follower_field = COVERAGE_SHAPES[shape]
    named = (
        ("pivot forms", 0, set(match["after"][pivot_field])),
        ("follower families", 1, set(sv._families(match["before"]["follower"]))),
        ("follower cells", 2, set(match["after"][follower_field])),
    )
    pairs = _coverage_pairs(units, blankness, shape, match)
    print(f"coverage for rule {rule_id!r} ({shape} shape), enumerated relaxed of everything it names:")
    missing = False
    for label, position, listed in named:
        unnamed = [(value, tally) for value, tally in _tallied(pairs, position) if value not in listed]
        if not unnamed:
            print(f"  {label}: the rule names every one this enumeration reaches")
            continue
        missing = True
        print(f"  {label} the rule does not name:")
        for value, tally in unnamed:
            print(f"    {sum(tally.values()):>5}  {value}  {dict(tally)}")
    if missing:
        print(DOCKET_NOTE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument("units", nargs="*", help="unit ids to explain (u-3mJ7kPq2Xw9)")
    parser.add_argument("--verdicts", default=str(VERDICTS), help="the verdicts file that defines blankness")
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--rules", default=str(sv.RULES))
    parser.add_argument(
        "--extension-cells",
        nargs=3,
        metavar=("PIVOT", "TOKEN", "SEAM"),
        help="enumerate the pivot and follower cells an extension-dropped rule for PIVOT giving up TOKEN (ex-ext-N or ex-con-N) at SEAM would have to name",
    )
    parser.add_argument(
        "--retarget-cells",
        nargs=4,
        metavar=("PIVOT", "BEFORE_SEAM", "FOLLOWER", "AFTER_SEAM"),
        help="enumerate the pivot and follower cells a join-retargeted or join-created rule for PIVOT's seam into FOLLOWER moving from BEFORE_SEAM to AFTER_SEAM would have to name",
    )
    parser.add_argument(
        "--coverage",
        metavar="RULE_ID",
        help="re-run a checked-in rule's own survey relaxed of everything it names, and report what it reaches that the rule does not",
    )
    parser.add_argument(
        "--find",
        metavar="TEXT",
        help="the human units whose notation contains TEXT, as a plain substring, blanks first",
    )
    parser.add_argument(
        "--blank-only", action="store_true", help="restrict --find to units carrying no verdict"
    )
    parser.add_argument(
        "--limit", type=int, default=FIND_LIMIT, help="how many --find matches to print (the total is stated)"
    )
    parser.add_argument(
        "--shapes", action="store_true", help="print the symptom-to-shape menu and, with no unit named, stop"
    )
    args = parser.parse_args(argv)
    asked = (args.units, args.extension_cells, args.retarget_cells, args.find, args.coverage)
    if args.shapes:
        _shapes()
        if not any(asked):
            return 0
    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    verdicts = pathlib.Path(args.verdicts)
    records = {}
    stale = False
    if verdicts.is_file():
        data = json.loads(verdicts.read_text())
        if data.get("manifest_generated_at") == manifest["generated_at"]:
            records = latest_verdicts(verdicts)
        else:
            stale = True
            print(
                f"{verdicts.name} is stamped for another manifest, so it can answer nothing about this "
                f"surface: every verdict below reads {UNKNOWN_VERDICT} rather than BLANK"
            )
    blankness = Blankness(records, stale)
    rules = sv.load_rules(pathlib.Path(args.rules))
    human = _human(load_units(surface))
    listed = False
    if args.extension_cells:
        _extension_cells(human, blankness, *args.extension_cells)
        listed = True
    if args.retarget_cells:
        _retarget_cells(human, blankness, *args.retarget_cells)
        listed = True
    if args.find:
        _find(human, blankness, args.find, args.blank_only, args.limit)
        listed = True
    if args.coverage:
        _coverage(human, blankness, rules, args.coverage)
        listed = True
    described = 0
    if args.units:
        context = None
        fonts = surface / "fonts" / "before.otf", surface / "fonts" / "after.otf"
        if all(font.is_file() for font in fonts):
            context = sv.SlideContext(*fonts)
        else:
            print(NO_FONTS)
        families: dict[frozenset, list] = collections.defaultdict(list)
        for unit in human:
            deltas = unit.get("ink_deltas") or {}
            if deltas:
                families[frozenset(deltas.values())].append(unit)
        wanted = set(args.units)
        for unit in human:
            if unit["id"] in wanted:
                _describe(unit, rules, context, blankness, families)
                wanted.discard(unit["id"])
                described += 1
        for missing in sorted(wanted):
            print(f"{missing}: not a human unit on this surface (machine-approved, exempt, or unknown)")
    if not args.shapes and not (described or listed):
        _shapes()
    return 0


if __name__ == "__main__":
    main()
