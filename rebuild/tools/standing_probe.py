"""Explain why a review-surface unit still queues, in the standing approvals' own terms, so the next once-and-for-all rule is written from evidence instead of rediscovered: for each unit named, print its two grains side by side — the recorded before glyphs and after cells with their seams, and the rendered pieces of both fonts with each piece's placement, own-frame origin and cell count, read as "same shape placed N columns over", "redrawn", or "inkless" — then say what every checked-in rule makes of it (matches, held by except_left, or nothing), whether the composed reading credits any rules and whether that credit reaches the two-rule threshold, and how many human units share exactly this unit's ink-delta digests and how they were verdicted, which is where the user's earlier decision usually turns out to be already recorded. `--extension-cells PIVOT TOKEN SEAM` answers the other question a new extension-dropped rule always asks — which pivot and follower cells it has to name in full — by enumerating every window on the surface where a PIVOT glyph carrying TOKEN exits at SEAM on both sides and settles into a cell without it or with a shorter one, with the follower's family, both after cells, and the verdict tally per pair. Read-only: nothing here writes to the surface or the store."""

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.review.ink import features_for  # noqa: E402
from rebuild.tools import standing_verdicts as sv  # noqa: E402
from rebuild.tools.review_docket import latest_verdicts, load_units  # noqa: E402
from rebuild.validation.classify import PIXEL_SIZE  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
VERDICTS = ROOT / "verdicts-autosave.json"


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
    """One position's rendered change in words: both pieces absent is inkless, one absent is ink appearing or vanishing, the same shape key is a placement (and possibly an own-frame origin) move, a different key is a redraw whose cell counts say how much."""
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
    return (
        f"redrawn {len(painted)}→{len(kept)} cells (−{len(painted - kept)} +{len(kept - painted)}), {moved}"
    )


def _describe(unit, rules, context, records, families):
    verdict = records[unit["id"]]["verdict"] if unit["id"] in records else "BLANK"
    deltas = unit.get("ink_deltas") or {}
    pair = unit.get("pair")
    print(f"{unit['id']}  {unit['class']}  echo {unit.get('echo')}  {unit['notation']}  {unit['codepoints']}")
    print(
        f"  configs {', '.join(unit['configs'])}   deltas {', '.join(sorted(set(deltas.values()))) or 'none'}"
        f"   pair {pair['left']}–{pair['right'] if pair else '?'}   secondary seams {unit.get('secondary_seams')}"
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
        elif sv._matches(rule["match"], unit, guard=False, context=context):
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
        tally = collections.Counter(
            records[sibling["id"]]["verdict"] if sibling["id"] in records else "BLANK"
            for sibling in families.get(key, [])
        )
        print(f"  same deltas across the surface: {sum(tally.values())} human units — {dict(tally)}")
    print()


def _extension_cells(units, records, pivot, token, seam):
    pairs: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    for unit in units:
        if not sv._letter_for_letter(unit):
            continue
        glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
        cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
        for index in range(min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1):
            if not (sv._is_pivot(glyphs[index], pivot) and token in sv._modifiers(glyphs[index])):
                continue
            if seams[index] != seam or after_seams[index] != seam:
                continue
            if sv._kept_extension(cells[index]) >= sv._extension_columns(token):
                continue
            verdict = records[unit["id"]]["verdict"] if unit["id"] in records else "BLANK"
            pairs[(cells[index], sv._family(glyphs[index + 1]), cells[index + 1])][verdict] += 1
    print(
        f"windows where a {pivot} glyph carrying {token} exits at {seam} on both sides into a cell without it "
        "or with a shorter one:"
    )
    for (pivot_cell, follower, follower_cell), tally in sorted(
        pairs.items(), key=lambda item: -sum(item[1].values())
    ):
        print(f"  {sum(tally.values()):>5}  {pivot_cell}  →  {follower}  {follower_cell}  {dict(tally)}")
    print("pivot cells:", sorted({pivot_cell for pivot_cell, _follower, _cell in pairs}))
    print("followers:", sorted({follower for _pivot, follower, _cell in pairs}))
    print("follower cells:", sorted({cell for _pivot, _follower, cell in pairs}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument("units", nargs="*", help="unit ids to explain (u-NNNNNN)")
    parser.add_argument("--verdicts", default=str(VERDICTS), help="the verdicts file that defines blankness")
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--rules", default=str(sv.RULES))
    parser.add_argument(
        "--extension-cells",
        nargs=3,
        metavar=("PIVOT", "TOKEN", "SEAM"),
        help="enumerate the pivot and follower cells an extension-dropped rule for PIVOT giving up TOKEN at SEAM would have to name",
    )
    args = parser.parse_args(argv)
    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    verdicts = pathlib.Path(args.verdicts)
    records = {}
    if verdicts.is_file():
        data = json.loads(verdicts.read_text())
        if data.get("manifest_generated_at") == manifest["generated_at"]:
            records = latest_verdicts(verdicts)
        else:
            print(f"{verdicts.name} is stamped for another manifest; every unit reads as blank here")
    rules = sv.load_rules(pathlib.Path(args.rules))
    human = _human(load_units(surface))
    if args.extension_cells:
        _extension_cells(human, records, *args.extension_cells)
    if not args.units:
        return 0
    wanted = set(args.units)
    context = None
    fonts = surface / "fonts" / "before.otf", surface / "fonts" / "after.otf"
    if all(font.is_file() for font in fonts):
        context = sv.SlideContext(*fonts)
    families: dict[frozenset, list] = collections.defaultdict(list)
    for unit in human:
        deltas = unit.get("ink_deltas") or {}
        if deltas:
            families[frozenset(deltas.values())].append(unit)
    for unit in human:
        if unit["id"] in wanted:
            _describe(unit, rules, context, records, families)
            wanted.discard(unit["id"])
    for missing in sorted(wanted):
        print(f"{missing}: not a human unit on this surface (machine-approved, exempt, or unknown)")
    return 0


if __name__ == "__main__":
    main()
