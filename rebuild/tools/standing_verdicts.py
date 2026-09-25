"""Apply the checked-in standing approvals (rebuild/standing-approvals.yaml) to the live review surface: for every rule, find the blank human units whose before-to-after change matches the rule, and write fill records for them to an importable verdicts file.

A rule declares one shape, a row in `SHAPES`, by the field its `match.after` carries. Each matcher's docstring states what its shape checks, and no shape checks anything about the window beyond that. The shapes are:

- `ligature` (declared by `ligature`): the pivot and its follower become the named ligature, and the seams on either side of that change are unchanged. It does not check that the unit's judged pair is this pivot's (see `_matches_ligature`).

- `extension-dropped` (declared by `follower_cells`): the pivot gives up a named stretch of exit. That is a whole `ex-ext-N` the before glyph carried, the columns down to a shorter extension its after cell keeps, or a named `ex-con-N` on an after cell whose before glyph carried no exit extension. The two sides must line up letter for letter over identical seams, the follower must be in a family the rule names, and the pivot and follower must settle into cells the rule names in full (rune, stance, entry, exit, and the whole adjustment set), which fixes how much of the stretch went. The unit's primary judged pair must be that pivot and follower, with no secondary seam anywhere in the window. That last condition is required because identical seams do not mean identical ink: a window can keep every seam and still be about a different letter's stroke, and only the surface's judgment fields say which letter the unit is about. This shape reads names only.

- `ink-delta` (declared by `ink_deltas`): the unit's persisted per-config ink-delta digests (`delta_digest` over `InkComparator.config_diff` in rebuild/review/ink.py) must all be among the rule's digests. The match is pixel-exact. A name-only difference paints no different pixel, so it is part of the same digest, and any other visible ink change under any config fails the match.

- `slide` (declared by `slide`): the window is re-shaped in the surface's font pair, and its whole visible change must be the named pivot and everything after it moving by the declared column count, whatever the two fonts name the glyphs.

- `ink-gain` (declared by `gained`): the pivot's new form is its old picture plus a named set of own-frame cells, and everything after the pivot moves by the declared count: zero when the fuller form keeps its advance, positive when the added ink lengthens it. ·Roe keeping the baseline bar the old shortened-bottom form dropped matches at zero, and ·Gay's extra exit cell moving ·No one column right matches at one.

- `join-dropped` (declared by `gap`): a named join becomes a break. The pivot keeps its picture and own-frame origin, or, where the rule names in full the cells both letters settle into, only its origin, so it may redraw in place (as a ·No raised to the x-height does when it loses its reach down to ·Thaw). The follower keeps its picture and origin and sits the declared number of columns further away, and everything after it moves by the same gap. The gap may be zero only where the pivot may redraw (the flipped ·No losing its x-height join into ·Cheer leaves ·Cheer where it was). A rule may also declare `follower_give_back`: the follower's joining form had inserted columns at its left edge, so as the join goes its own-frame origin moves right by that count and it redraws inside the named receiver cells (·Gay when the ·No in front of it is raised past reaching it). The unmoved origin is what separates a dropped join from a sidebearing change, which is the `slide` shape.

- `entry-extension-dropped` (declared by `entry_drop`): the pivot gives up a named stretch of left-side entry. Either its own-frame picture is the old one compacted left by that many columns, with every dropped cell in the removed columns and its origin and placement unchanged, or its after form names an `en-con-N` that brings the letter that many columns closer. Everything after the pivot moves closer by the count. ·Low losing the extra baseline pixel the old font drew after ·See matches.

- `entry-contracted` (declared by `entry_contraction`): one or more named left-pivot pairs whose pivot contracts its entry by the declared count. The letter comes that many columns closer however its after form took the contraction: a form that moved its own-frame origin right by the whole count keeps its placement, one that did not move its origin moves its placement the whole count left, and the ink ends up in the same place either way. The lost left-side ink stays inside the contracted columns, any change at the far right is exactly the difference between the exit extensions the before and after glyph names carry, and everything after the pivot moves by the contraction plus that difference. A name-only change after the pivot is allowed where the pivot still paints every cell it appears to lose. `except_pivots` declines before forms that nest under a pivot prefix but need another count or redraw, leaving them to another rule. A named pivot may also lead an existing ligature: the incoming seam must match any named entry height, the compound must carry every other named modifier except exit heights, and the same compound family must stand at the position on both sides. The whole compound's pixels and displacement are judged, so an unrelated change in the rest of the compound fails the match. A pivot outside a ligature is matched by prefix over its whole family.

- `stub-dropped` (declared by `stub_drop`): the pivot gives up a named left-side stub while the ink it keeps stays in place. Its own-frame picture is the old one compacted left by the count, its placement moves right by the count, and every other pixel in the window is unmoved. The placement move is what separates it from `entry-extension-dropped`, whose remaining ink moves closer. ·May losing the leftover left pixel after ·Ah matches. A pivot is a position whose before and after names both carry the named prefixes: a second ·May that keeps its old loop is judged as ordinary ink, because only the before form says which of the two after loops lost the stub.

- `redrawn` (declared by `dropped`): the pivot's new form is its old picture with the named `dropped` cells removed and the named `added` cells present, both read at one common column offset, because an entry extension inserts a column at the pivot's left edge and an entry-extended variant shows the same trade one column over. The own-frame origin and the placement stay where they were unless the new form names more entry contraction than the old one: the frame may take up to all of that extra contraction, and the placement may move left by the part the frame did not take. Everything after the pivot moves by the declared shift plus that placement move. ·Eight's bowl pulling in one column before ·Tea and ·It is one example. ·J'ai's crown coming in after a half-height ·Pea or ·Tea moves the letter and the rest of the word, while the same crown after an ·At the old font had already contracted moves only what the dropped tail moves, and ·Gay's baseline entry pulling in after ·No leaves the letter where it was, because its frame takes the whole contraction. `added` may be empty for a form that only loses ink (·Key's foot losing its terminal pixel before ·May, ·No, and ·It). This is the shape for an exit contraction whenever its windows carry any other change, because `extension-dropped` reads only names and would approve whatever else the window did.

- `join-retargeted` (declared by `retarget`): a named join changes height. The pivot and follower may both redraw but keep their own-frame origins, the pivot keeps its placement, the follower moves by `follower_shift` (zero where the new join leaves it standing, -2 where ·Utter reaching ·May at the x-height pulls ·May back), and everything after the follower moves by `shift`. Half-·Tea joining ·No at the x-height becoming full ·Tea joining flipped ·No at the baseline is an example.

- `join-created` (declared by `joined`): a named pair that was a break now joins at the named height, and the pivot and follower may redraw. The pivot keeps its own-frame origin, and its placement stays or moves up to `pivot_stub_drop` columns right when the left-side entry the old font drew in front of the join comes off (·May's own entry in front of its new baseline join into ·Gay). The follower keeps its own-frame origin, or moves it left by `follower_reach` when its joining form inserts columns at its left edge (·Gay's stroke that reaches the baseline). The follower moves by `shift` plus the pivot's placement move, and everything after it by that plus `follower_advance`. Two counts are needed because a follower that redraws wider gives back what the join closed: the reaches-way-back ·Utter comes a column nearer ·May and leaves the rest of the word where it was. The pivot may name several families, which records one letter's new entry for every left neighbor that now reaches it. `except_pivots` declines before forms that nest under a pivot prefix but need another count: the ·J'ai the old font drew with a stacked crown entry gives the follower a column, while the ·J'ai drawn without one gains a reach that cancels it.

The composed reading, which no rule declares, runs before any single rule is checked. It asks whether two or more approved changes together account for every rendered pixel of one window. For example, where the grounded ·See slides a column closer to what precedes it and ·J'ai also gives up its exit extension, neither rule covers the window alone: `slide` fails on the extension pixel, and `extension-dropped` does not look at ink outside its judged seam.

Only shapes whose `SHAPES` row sets `composable` take part. Each names a local change the walk can check at one position: a displacement, named own-frame cells gained or traded on the pivot, a join that is dropped, created, or changes height, or a left-side stretch or stub the pivot gives up. `ligature` reads the whole window's names and `ink-delta` its whole ink change, so neither says anything about one position.

Each composable rule's candidate positions come from the index record without shaping (`_candidates`), and a window with fewer than two candidate positions in total is never shaped.

The walk (`_composed_walk`) re-shapes the window in the surface's font pair and carries a running column displacement from left to right. At each event:

- slide: the pivot leads the next span, and the displacement grows by the declared slide.
- extension: the pivot sits at the running displacement and loses, on the row its `seam_out` height names, the tail the rule names (the named extension less any shorter one its after cell keeps, or the named contraction). The displacement shrinks by that width. The follower leads the next span, which must be a translation, the same picture compacted left by the follower's dropped entry extension, or, when the follower redrew inside its named cell, a translation of the span without the follower.
- join (dropped): the pivot sits at the running displacement, and the displacement grows by the gap. The follower leads the next span, or, where the rule declares `follower_give_back`, is judged by the event and left out of the span.
- gain: the pivot sits at the running displacement, and the displacement grows by the declared shift.
- entry: the pivot sits left of the running displacement by the part of its entry contraction its own frame did not take (zero for a dropped entry extension), and the displacement moves closer by the entry count, adjusted for an `entry-contracted` rule by any exit-extension change on the pivot. The next span is compared together with the pivot's after picture, so a cell handed between them does not count as a change.
- redrawn: the pivot sits at the running displacement, or left of it by up to the part of its new form's extra entry contraction its frame did not take, and the displacement grows by the declared shift plus that placement move.
- stub: the pivot's placement moves right by the stub count, and the displacement is unchanged.
- retarget: the pivot sits at the running displacement, the follower moves by `follower_shift`, and everything after the follower moves by the full `shift`.
- joined (created): the pivot sits at the running displacement or up to `pivot_stub_drop` columns right of it, the follower moves by `shift` plus that offset, and everything after the follower moves by that plus `follower_advance`.

Every span between events must render as its before picture moved by the displacement at its start.

Two events may share a letter only in these chains:

- A created join behind an entry, ink-gain, or redrawn event at the same position. The earlier event judges the picture the pivot settles into, and the created join judges the seam that picture opens and its follower. Examples: ·Ah's contracted entry after ·J'ai and its new x-height join into ·Gay; ·Tea's full bar under ss03 and the baseline join it takes; ·Eight's smaller loop and the baseline join into ·It that only that loop reaches.
- A created join, redrawn trade, dropped join, or further retarget whose pivot is a retarget's follower. The retarget judges that letter's incoming seam and its placement. A further retarget or dropped join takes nothing of the first retarget's `shift` beyond its follower's move, because its own counts are measured with its pivot standing and already include that letter's advance. A redrawn trade there must leave the letter where the retarget put it, so a new form naming an entry contraction fails. Examples: ·Gay's raised join into ·No with the break ·No now leaves before ·Thaw, or with ·No's own raised join into ·Day or ·No; ·It's lowered join into ·No and ·No's new join into ·Gay; ·Utter's raised join into ·May and the loop ·May draws with no exit left.
- A created join, retarget, extension drop, ink gain, or redrawn trade whose pivot is a created join's follower. The created join judges that letter's incoming seam. A following retarget receives only the created join's `follower_reach`, for the same reason as above, and any other following event receives the whole `follower_advance`. Such a retarget may have moved its pivot's own-frame origin, because the created join's `follower_reach` declared that move. Examples: ·Bay's new baseline join into ·Utter and ·Utter's raised join into ·May, which puts ·May three columns back; ·Et's new baseline join into ·Gay and ·Gay's raised join into ·No; ·Pea's lowered join into ·No, ·No's new join into ·Ah, and ·Ah's dropped tail before ·Bay; ·Ah's new x-height join into ·Gay and ·Gay's own redraw, which meets that seam and gives up its baseline tail.
- A dropped join or extension drop whose follower is itself an event. That event is judged next, under the displacement the first one applied (·At's dropped x-height join and ·It's dropped exit extension).

Any other pair of events at one position, or a created join or retarget whose follower position is also an event, makes the walk return None. A created join whose pivot moved its own-frame origin is an event only behind an entry, ink-gain, or redrawn event at that position. A retarget whose pivot moved its origin is an event only behind a created join at the position before it, or behind an entry, ink-gain, or redrawn event at the same position, which the walk chains the way it chains a created join there.

A candidate whose own contract fails is not an event, and its ink is judged as ordinary span ink, so a rule that fails at a position does not stop the other rules from explaining the window. The pivot is judged piece by piece rather than as part of a union, so a pivot whose after form also drops a cell off the seam row (·J'ai's crown contracting under an ·At tuck) never composes.

Credit needs two or more events. One rule credited at two positions counts, as in a window where ·Ah gives up its exit tail twice, and a single event belongs on that rule's own line. A composed fill's verdict is `either` when any credited rule's verdict is `either` or a non-composable `either` rule also matches the window, and `approve` otherwise. Its note names the credited ids in rules-file order.

A rule's except_left guard refuses the whole unit, never one position, so a guarded context is never filled beside an unguarded one. Most shapes read the guard across the whole window; `extension-dropped` and `ligature` read it at the left neighbor of each matched pivot. A composed reading reads each credited rule's guard in that rule's `guard_scope`, and a guard that holds a composed window holds the whole unit: it is counted on the composed line, never filled, and never passed to the single-rule pass. A rule's except_left families name the contexts the user still wants to review: units in those contexts are held, so they still reach the docket.

Standing fills complement echo_verdicts.py. The echo fill copies the user's verdicts to units whose change is pixel-identical, while a standing rule applies a recorded decision to units the user has never seen, such as windows with new left letters created by later migrations, so those units never queue.

Each fill record's `at` is the manifest's generated_at, so a human verdict recorded on this surface is newer and wins on merge. A parked unit carries a skip verdict, so it is not blank and is never filled. The verdict chain (rebuild/tools/verdict_chain.py) runs this after the echo fill and merges its file with merge_verdicts. The report also gives each rule's total reach, its own line plus its composed credit; the totals do not sum across rules, because a window two rules explain counts toward both.

Every decision depends only on the unit's index record, the two fonts' rendering of its window, and the rules file; the verdict store only decides which decisions become fills. `Decider.decide` computes the decision and `_decision_reach` aggregates a run from the decisions. The memo (`Memo`, the `--memo` flag, which the verdict chain passes) keeps decisions across passes, so a pass evaluates only the units whose key is new and the units a changed rule can reach. `unit_key`, `memo_environment`, `rules_roster`, and `Decider._serve` define the unit keys, the memo stamp, and when a stored decision is served. A stored decision holds rule ids and no note text, so a reworded note re-evaluates nothing and every fill quotes the new wording. When the misses reach `_STANDING_POOL_THRESHOLD`, `_prefill` decides them across a spawn pool at the width `--jobs` gives. The tool derives no width of its own; the verdict chain forwards the artifact cycle's. The fills and the report are byte-identical served or computed, pooled or serial, and rebuild/test_standing_verdicts.py checks this over the frozen mini bundle. The `--require-reach` rollup reads the same decisions, so its pass over the whole domain costs no second evaluation.

A `--targeted` run is the form for authoring a rule. It takes the rule from `--explain` and extra units from `--unit`, evaluates only the rule's name-grain candidates (`_reachable`) plus the listed units, prints that rule's lines byte-identical to the whole-domain run's plus one line per listed unit (`targeted_report`), and writes neither a fill file nor the memo. The whole-domain run is the final pass and the cycle's form.

With `--daemon auto|always|never` and `--socket PATH`, either form can be served by the standing daemon (rebuild/tools/standing_daemon.py, the authority on what it holds and when it declines), which runs this same `main` over the surface it holds and returns the streams and exit code byte-identical to an in-process run; rebuild/test_standing_daemon.py checks this over the mini bundle. The verdict chain calls `main` in process with a `unit_source` and is never served. A fill reads its unit source once, keeps only unit ids and decisions for the report, and spools pool misses to a temporary gzipped NDJSON file.
"""

import argparse
import gzip
import hashlib
import json
import multiprocessing
import pathlib
import re
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from itertools import batched
from typing import Any, NamedTuple, NoReturn

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.pipeline import fingerprint  # noqa: E402
from rebuild.review.ink import IDENTITY_DIFF, InkComparator, delta_digest, features_for  # noqa: E402
from rebuild.validation.classify import PIXEL_SIZE  # noqa: E402
from rebuild.review.unit_index import iter_human_units  # noqa: E402
from rebuild.tools import standing_client  # noqa: E402
from rebuild.tools.review_docket import ACCEPTING_VERDICTS, latest_verdicts, load_human_units  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
RULES = ROOT / "rebuild/standing-approvals.yaml"
OUT = ROOT / "verdicts-standing-fill.json"
FORMAT = "ams-standing-approvals/1"
MEMO_FORMAT = "ams-standing-fill-memo/2"
MEMO_NAME = "standing-fill-memo.ndjson.gz"
# The repo code a fill decision depends on, hashed into the memo stamp: this module's import closure without rebuild/pipeline/, whose modules change the unit keys or the stamp itself rather than a decision. test_the_memo_code_roster_is_this_modules_import_closure checks the list.
MEMO_CODE_MODULES = (
    "rebuild/review/ink.py",
    "rebuild/review/unit_index.py",
    "rebuild/tools/review_docket.py",
    "rebuild/tools/standing_client.py",
    "rebuild/tools/standing_verdicts.py",
    "rebuild/validation/classify.py",
    "rebuild/validation/rowmodel.py",
    "rebuild/validation/shaping.py",
)
ALLOWED_VERDICTS = ("approve", "either")
CELL_FIELDS = 5
EXIT_EXTENSION = re.compile(r"ex-ext-[1-9][0-9]*")
EXIT_CONTRACTION = re.compile(r"ex-con-[1-9][0-9]*")
ENTRY_EXTENSION = re.compile(r"en-ext-[1-9][0-9]*")
ENTRY_CONTRACTION = re.compile(r"en-con-[1-9][0-9]*")
DELTA_DIGEST = re.compile(r"d-[0-9a-f]{12}")
EMPTY_DELTA_DIGEST = delta_digest(IDENTITY_DIFF)
SEAM_ROW = re.compile(r"y([0-9]+)")


def _fail(message) -> NoReturn:
    raise SystemExit(f"rebuild/standing-approvals.yaml: {message}")


def _family(glyph_name):
    """Return the family of an old-font glyph name: everything before the first dot, including a ligature's compound name (`qsTea_qsOy.en-y0` gives `qsTea_qsOy`)."""
    return glyph_name.split(".", 1)[0]


def _joining_family(glyph_name):
    """Return the last family in an old-font glyph name (`qsDay_qsMay.alt` gives `qsMay`). For a left neighbor this is the letter whose stroke touches the pivot, so the except_left guard reads it."""
    return _family(glyph_name).rsplit("_", 1)[-1]


def _modifiers(glyph_name):
    """Return the dot-separated modifier tokens of an old-font glyph name (`qsTea.en-y8.ex-ext-1` gives `['en-y8', 'ex-ext-1']`)."""
    return glyph_name.split(".")[1:]


def _is_pivot(glyph_name, pivot):
    return glyph_name == pivot or glyph_name.startswith(pivot + ".")


def _cell_parts(token):
    """Split a review-surface cell string into its slash-separated fields: rune, stance, entry, exit, and the +-joined adjustments, which are often empty."""
    return token.split("/")


def _cell_rune(token):
    return _cell_parts(token)[0]


def _cell_adjustments(token):
    parts = _cell_parts(token)
    return parts[4].split("+") if len(parts) > 4 and parts[4] else []


def _is_cell(token):
    parts = _cell_parts(token) if isinstance(token, str) else []
    return len(parts) == CELL_FIELDS and all(parts[:4])


def _extension_columns(token):
    """Return the column count N that ends an `ex-ext-N`, `ex-con-N`, `en-ext-N`, or `en-con-N` token."""
    return int(token.rsplit("-", 1)[1])


def _kept_extension(cell):
    """Return the exit extension a review-surface cell carries, as a column count, or zero when its adjustment set names none."""
    return max(
        (_extension_columns(token) for token in _cell_adjustments(cell) if EXIT_EXTENSION.fullmatch(token)),
        default=0,
    )


def _cell_contraction(cell):
    """Return the exit contraction a review-surface cell carries, as a column count, or zero when its adjustment set names none."""
    return max(
        (_extension_columns(token) for token in _cell_adjustments(cell) if EXIT_CONTRACTION.fullmatch(token)),
        default=0,
    )


def _dropped_entry(glyph, cell):
    """Return how many columns of entry extension the before glyph carried that the after cell does not keep."""
    before = max(
        (_extension_columns(part) for part in _modifiers(glyph) if ENTRY_EXTENSION.fullmatch(part)),
        default=0,
    )
    after = max(
        (_extension_columns(token) for token in _cell_adjustments(cell) if ENTRY_EXTENSION.fullmatch(token)),
        default=0,
    )
    return max(0, before - after)


def _glyph_adjustment(glyph, pattern):
    """Return the largest column count on a glyph-name modifier matching `pattern`, or zero when there is none."""
    return max(
        (_extension_columns(part) for part in _modifiers(glyph) if pattern.fullmatch(part)),
        default=0,
    )


def _carries_named_drop(token, glyph, cell):
    """Whether this pivot position is the named drop: an `ex-ext-N` on the before glyph, or an `ex-con-N` on the after cell whose before glyph never carried an exit extension."""
    if EXIT_CONTRACTION.fullmatch(token):
        return token in _cell_adjustments(cell) and not any(
            EXIT_EXTENSION.fullmatch(part) for part in _modifiers(glyph)
        )
    return token in _modifiers(glyph)


def _drop_columns(token, cell):
    """Return how many columns the named token says the pivot gave up at this after cell: the named extension less any shorter one the cell keeps, or the named contraction in full."""
    named = _extension_columns(token)
    return named if EXIT_CONTRACTION.fullmatch(token) else named - _kept_extension(cell)


def _families(value):
    """Return a rule field that names one family or a list of families as a list."""
    return list(value) if isinstance(value, list) else [value]


def _components(name):
    """Return how many input codepoints a glyph or cell name covers, counting a ligature's underscore-joined members."""
    return name.count("_") + 1


_alignment_cache: dict[int, tuple[dict, bool]] = {}


def release_alignment_cache() -> None:
    """Empty the per-unit alignment cache. The standing daemon calls this after every request and a pooled worker after every chunk, so neither keeps a unit after the pass that asked about it."""
    _alignment_cache.clear()


def _letter_for_letter(unit):
    """Whether each before-glyph index and after-cell index name the same letters along the whole window, which the pivot and follower comparisons and the surface's after-indexed `pair` rely on. The sides line up when they merge the same codepoints at the same positions. Requiring each side's components to sum to the window's codepoint count makes the check fail if a name ever covers codepoints some other way than as a ligature. The matchers and the composed walk ask this of a unit several times, so the answer is cached per unit object. The entry is keyed on the unit's `id()` and holds the unit itself beside the answer, so no other object can reuse that address while the entry exists."""
    cached = _alignment_cache.get(id(unit))
    if cached is not None:
        return cached[1]
    codepoints = unit.get("codepoints") or ""
    if codepoints:
        before = [_components(_family(name)) for name in unit["before"]["glyphs"]]
        after = [_components(_cell_rune(cell)) for cell in unit["after"]["cells"]]
        aligned = before == after and sum(before) == len(codepoints.split(":"))
    else:
        aligned = False
    _alignment_cache[id(unit)] = (unit, aligned)
    return aligned


def _matches_ligature(match, unit, excluded, context=None):
    """A pivot letter whose backward join drops as it ligates with its follower: the pivot sits between the two named seams, the pivot and follower become the named ligature, and the seams on either side of that change are unchanged. Unchanged seams on either side constrain the joins and nothing else. They do not show that the unit's judged question is about this pivot, so a second rule in this shape would need the judged-pair and secondary-seam checks the extension-dropped shape makes. The follower is read as the right neighbor's `_joining_family`, where the extension-dropped shape reads the whole `_family`; the two differ only when that neighbor is itself a ligature."""
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


def _matches_extension(match, unit, excluded, context=None):
    """A pivot letter that gives up the named stretch of exit into a seam that keeps its named height, with every seam in the window unchanged, the follower in one of the named families, and the pivot and follower settling into cells the rule names in full. Naming the cells in full makes the change exact. Rune, stance, entry, and exit fix the bitmaps on both sides of the seam, and the adjustment set fixes what the pivot still carries (no extension, a shorter one, or the named contraction), so a rule covers only the columns between the stretch it names and the one its pivot cell keeps. Identical seams say nothing about ink elsewhere, so the unit's own judgment fields decide where the change is: the unit's primary judged pair must be this pivot and follower, and a window with any secondary seam is refused. The two sides must line up letter for letter, and the follower is compared by its whole family name, so a ligature in that slot matches only a rule that names the compound. The follower's after cell must belong to that same family, so a rule naming several followers never accepts one family's cell for another's. A word-initial pivot has no left neighbor, so except_left never holds it."""
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    mb, ma = match["before"], match["after"]
    if seams != after_seams or not _letter_for_letter(unit):
        return False
    extension = mb["exit_extension"]
    followers = _families(mb["follower"])
    hits = [
        i
        for i in range(len(glyphs) - 1)
        if _is_pivot(glyphs[i], mb["pivot"])
        and _carries_named_drop(extension, glyphs[i], cells[i])
        and seams[i] == mb["seam_out"]
        and cells[i] in ma["pivot_cells"]
        and _family(glyphs[i + 1]) in followers
        and cells[i + 1] in ma["follower_cells"]
        and _cell_rune(cells[i + 1]) == _family(glyphs[i + 1])
    ]
    if any(i and _joining_family(glyphs[i - 1]) in excluded for i in hits):
        return False
    if unit.get("secondary_seams"):
        return False
    return any(unit.get("pair") == {"left": i, "right": i + 1} for i in hits)


def _matches_ink_delta(match, unit, excluded, context=None):
    """A window whose entire before→after ink change is one the user has approved: the unit's persisted `ink_deltas`, one digest per config with a visible change (InkComparator.config_diff, computed by the surface build), must all be among the rule's named digests. A digest records the pixels that appear and disappear across the window's rendered union, localized to the changed region and with the shift of what follows taken out, so every other difference the unit carries is a name-only one (such as a stroke handed to a neighbor that still paints it), and a window with any unlisted pixel under any config fails. No judged-pair check is needed, because the digest covers the whole window's pixel change. There is no pivot position, so except_left reads the whole window: an excluded family joining anywhere in it refuses the unit."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if not set(deltas.values()) <= set(match["after"]["ink_deltas"]):
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_ink_delta(rule_id, match) -> None:
    """Check an ink-delta rule at load: no digest may repeat, and none may be the empty delta. An ink-identical window is machine-approved already, so a rule naming the empty delta could only hide a digest typo."""
    digests = match["after"]["ink_deltas"]
    if len(set(digests)) != len(digests):
        _fail(f"rule {rule_id!r}: match.after.ink_deltas repeats a digest")
    if EMPTY_DELTA_DIGEST in digests:
        _fail(
            f"rule {rule_id!r}: match.after.ink_deltas names the empty delta {EMPTY_DELTA_DIGEST}; "
            "an ink-identical window is machine-approved and never needs a rule"
        )


def _validate_extension(rule_id, match) -> None:
    """Check an extension-dropped rule at load. The named token must be an exit-side extension or contraction, since an entry-side token would refer to the seam on the other side of the pivot from `seam_out`. The pivot cells must belong to the pivot's family and the follower cells to one of the follower families. Every pivot cell must give up columns: under an `ex-ext-N` rule no pivot cell may keep an exit extension of N or more columns, because the shape covers the whole extension or the part down to a shorter one, never a tail that stayed or grew. Under an `ex-con-N` rule every pivot cell must carry exactly that contraction and no exit extension, because the contraction is the named drop and a remaining `ex-ext` would be a different stretch."""
    extension = match["before"]["exit_extension"]
    contracted = bool(EXIT_CONTRACTION.fullmatch(extension))
    if not (EXIT_EXTENSION.fullmatch(extension) or contracted):
        _fail(
            f"rule {rule_id!r}: match.before.exit_extension names {extension!r}, which is not an exit-side "
            "extension (ex-ext-N) or contraction (ex-con-N); an entry-side token would pin the seam on the "
            "other side of the pivot"
        )
    named = (
        ("pivot_cells", [_family(match["before"]["pivot"])]),
        ("follower_cells", _families(match["before"]["follower"])),
    )
    for field, runes in named:
        for cell in match["after"][field]:
            if _cell_rune(cell) not in runes:
                _fail(
                    f"rule {rule_id!r}: match.after.{field} entry {cell!r} is not a cell of "
                    f"{' or '.join(runes)}"
                )
    named = _extension_columns(extension)
    for cell in match["after"]["pivot_cells"]:
        if contracted:
            got = _cell_contraction(cell)
            if got != named:
                _fail(
                    f"rule {rule_id!r}: match.after.pivot_cells entry {cell!r} carries an exit contraction of "
                    f"{got} columns against the {named} of {extension}; this shape speaks only for the "
                    "named contraction on every pivot cell"
                )
            kept = _kept_extension(cell)
            if kept:
                _fail(
                    f"rule {rule_id!r}: match.after.pivot_cells entry {cell!r} still carries an exit "
                    f"extension of {kept} columns; a contraction rule names a drop from a default that "
                    "never had one"
                )
            continue
        kept = _kept_extension(cell)
        if kept >= named:
            _fail(
                f"rule {rule_id!r}: match.after.pivot_cells entry {cell!r} keeps an exit extension of {kept} "
                f"columns against the {named} of {extension}; this shape speaks only for columns of an exit "
                "extension the pivot has given up"
            )


def _named_pivot(glyph_name, pivots):
    return any(_is_pivot(glyph_name, pivot) for pivot in pivots)


def _named_contracted_pivot(glyph_name, pivots, seam):
    """Whether a glyph name falls under an entry-contracted rule's pivot prefixes, where a named pivot may also lead an existing ligature. For a ligature, any entry height on the glyph or the pivot must match the incoming `seam`, exit heights are ignored because the lead letter's exit lies inside the compound, and every other modifier the pivot names is still required. A glyph that is not a ligature is matched by `_named_pivot` alone."""
    if _named_pivot(glyph_name, pivots):
        return True
    family = _family(glyph_name)
    if "_" not in family:
        return False
    lead = family.split("_", 1)[0]
    if any(part.startswith("en-y") and part[3:] != seam for part in _modifiers(glyph_name)):
        return False
    modifiers = [part for part in _modifiers(glyph_name) if not re.fullmatch(r"(?:en|ex)-y[0-9]+", part)]
    projected = ".".join([lead, *modifiers])
    for pivot in pivots:
        if _family(pivot) != lead:
            continue
        named = _modifiers(pivot)
        if any(part.startswith("en-y") and part[3:] != seam for part in named):
            continue
        required = [part for part in named if not re.fullmatch(r"(?:en|ex)-y[0-9]+", part)]
        if _is_pivot(projected, ".".join([lead, *required])):
            return True
    return False


def _split_at(run, indices):
    """Cut the run into spans at the given piece indices: everything before the first pivot, then one span per pivot running from that pivot up to the next. Each pivot leads its span, so its ink is judged under the same displacement as the glyphs after it."""
    bounds = [0, *indices, len(run)]
    return [run[start:stop] for start, stop in zip(bounds, bounds[1:])]


def _span_cells(intern, span):
    """Return the pixel cells one span of placed pieces paints: the union of each shape's rasterized cells translated to its placement. Return None when any shape is not rectilinear on the grid or any placement is off the grid; callers treat that as no match."""
    cells = set()
    for _name, key, x, y, _origin in span:
        shape_cells = intern.cells(key)
        if shape_cells is None or x % PIXEL_SIZE or y % PIXEL_SIZE:
            return None
        cells.update((x // PIXEL_SIZE + column, y // PIXEL_SIZE + row) for column, row in shape_cells)
    return cells


def _slide_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is the declared slide, shaped under the unit's first config. Both ink runs are cut at their pivot positions, and each pair of spans must paint the same pixels once displaced by the cumulative slide: the span before the first pivot by nothing, the span the first pivot leads by one slide, and one more slide for each further pivot. Each pivot must also keep its shape and height with its own-frame origin moved by the slide, which ties the change to the pivot's sidebearing and not to some other movement that produces the same pixels. No pivot on the before side, pivot counts that differ, a before run that differs from the recorded glyphs, an off-grid placement, or a non-rectilinear outline returns False, so the unit queues."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    try:
        text = "".join(chr(int(value, 16)) for value in codepoints.split(":"))
    except ValueError:
        return False
    slide = match["after"]["slide"]
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return False
    _after_names, after_run = comparator.named_run("after", text, features)
    before_pivots = [
        i
        for i, piece in enumerate(before_run)
        if _named_pivot(piece[0], match["before"]["pivots"])
        and (
            "left" not in match["before"]
            or (i and _joining_family(before_run[i - 1][0]) == match["before"]["left"])
        )
    ]
    after_pivots = [
        i for i, piece in enumerate(after_run) if _named_pivot(piece[0], match["after"]["pivots"])
    ]
    if not before_pivots or len(before_pivots) != len(after_pivots):
        return False
    for before_index, after_index in zip(before_pivots, after_pivots):
        _bn, before_key, _bx, before_y, before_origin = before_run[before_index]
        _an, after_key, _ax, after_y, after_origin = after_run[after_index]
        if before_key != after_key or before_y != after_y:
            return False
        if after_origin != before_origin + slide * PIXEL_SIZE:
            return False
    intern = comparator.intern
    spans = zip(_split_at(before_run, before_pivots), _split_at(after_run, after_pivots))
    for step, (before_span, after_span) in enumerate(spans):
        before_cells = _span_cells(intern, before_span)
        after_cells = _span_cells(intern, after_span)
        if before_cells is None or after_cells is None:
            return False
        if {(column + slide * step, row) for column, row in before_cells} != after_cells:
            return False
    return True


def _matches_slide(match, unit, excluded, context=None):
    """A letter re-spaced against what precedes it, matched at the rendered-pixel grain: the old-font pivot form becomes a named new form, and the window's whole visible change is the pivot and everything after it moving by the declared column count (`_slide_geometry`). Every pixel before the pivot stays, and everything from the pivot on renders identically once moved. Pixels are compared instead of per-glyph pieces, so a name-only change to the right of the pivot that moves ink from one glyph to a neighbor without changing the union (the ·At·J'ai tuck under a moved ·See) does not stop the match. Any other ink change in the window fails this match; the composed reading, which runs first, handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not any(_named_pivot(name, match["before"]["pivots"]) for name in unit["before"]["glyphs"]):
        return False
    if context is None:
        raise ValueError("the slide shape re-shapes windows in the surface's fonts and needs a SlideContext")
    key = (
        "slide",
        tuple(match["before"]["pivots"]),
        tuple(match["after"]["pivots"]),
        match["after"]["slide"],
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _slide_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_slide(rule_id, match) -> None:
    """Check a slide rule at load: the slide must not be zero, since an unmoved window is ink-identical and machine-approved already, and every pivot form on either side must belong to one family, since a slide rule describes one letter's re-spacing."""
    if match["after"]["slide"] == 0:
        _fail(
            f"rule {rule_id!r}: match.after.slide is 0; an unmoved window is ink-identical and "
            "machine-approved already"
        )
    families = {_family(name) for name in match["before"]["pivots"] + match["after"]["pivots"]}
    if len(families) != 1:
        _fail(
            f"rule {rule_id!r}: the pivot lists span families {sorted(families)}; a slide rule "
            "speaks for one letter's re-spacing"
        )


def _gained_cells(match):
    """Return the named own-frame cells an ink-gain rule says the after form adds, as a set of (column, row) pairs."""
    return {tuple(point) for point in match["after"]["gained"]}


def _split_around(run, indices):
    """Cut the run into the spans strictly between the given piece indices: everything before the first, everything between one and the next, and everything after the last. The indexed pieces are left out, so a caller that judges them separately can compare the rest of the window without their ink."""
    starts = [0, *[index + 1 for index in indices]]
    stops = [*indices, len(run)]
    return [run[start:stop] for start, stop in zip(starts, stops)]


def _gain_holds(match, before, after, intern):
    """Whether one pivot piece is the named ink gain: same own-frame origin, both sides on the grid, and the after picture equal to the before picture plus exactly the named cells. The after frame may extend vertically around the old picture, so the before cells are moved into the after frame by the difference in the pieces' vertical placement before the comparison. The caller checks the horizontal placement."""
    if before[4] != after[4]:
        return False
    if before[2] % PIXEL_SIZE or after[2] % PIXEL_SIZE or before[3] % PIXEL_SIZE or after[3] % PIXEL_SIZE:
        return False
    painted, kept = intern.cells(before[1]), intern.cells(after[1])
    if painted is None or kept is None:
        return False
    row_shift = (before[3] - after[3]) // PIXEL_SIZE
    aligned = {(column, row + row_shift) for column, row in painted}
    return kept - aligned == _gained_cells(match) and not (aligned - kept)


def _gain_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is the named cells appearing on the named pivot, shaped under the unit's first config. Each pivot must pass `_gain_holds` at its placement under the running displacement, and every span strictly between the pivots must render identically under the cumulative declared shift. No pivot on the before side, pivot counts that differ, a before run that differs from the recorded glyphs, an off-grid placement, a non-rectilinear outline, a lost cell, an unnamed extra cell, or following ink that moves by another amount returns False, so the unit queues."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    try:
        text = "".join(chr(int(value, 16)) for value in codepoints.split(":"))
    except ValueError:
        return False
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return False
    _after_names, after_run = comparator.named_run("after", text, features)
    before_pivots = [
        i for i, piece in enumerate(before_run) if _named_pivot(piece[0], match["before"]["pivots"])
    ]
    after_pivots = [
        i for i, piece in enumerate(after_run) if _named_pivot(piece[0], match["after"]["pivots"])
    ]
    if not before_pivots or len(before_pivots) != len(after_pivots):
        return False
    intern = comparator.intern
    shift = match["after"]["shift"]
    displacement = 0
    before_spans = _split_around(before_run, before_pivots)
    after_spans = _split_around(after_run, after_pivots)
    for step, (before_span, after_span) in enumerate(zip(before_spans, after_spans)):
        if not _span_settled(intern, before_span, after_span, displacement):
            return False
        if step == len(before_pivots):
            continue
        before = before_run[before_pivots[step]]
        after = after_run[after_pivots[step]]
        if after[2] != before[2] + displacement * PIXEL_SIZE or not _gain_holds(match, before, after, intern):
            return False
        displacement += shift
    return True


def _matches_ink_gain(match, unit, excluded, context=None):
    """A letterform that keeps a named set of cells the old font omitted, matched at the rendered-pixel grain: the old-font pivot form becomes a named new form whose picture is the old one plus those cells, and everything after the pivot moves by the declared column count (`_gain_geometry`). The new frame may extend vertically around the old picture. The unchanged horizontal placement and own-frame origin tie the extra ink to the letterform and not to a slide or a sidebearing change. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not any(_named_pivot(name, match["before"]["pivots"]) for name in unit["before"]["glyphs"]):
        return False
    if context is None:
        raise ValueError(
            "the ink-gain shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        tuple(match["before"]["pivots"]),
        tuple(match["after"]["pivots"]),
        tuple(tuple(point) for point in match["after"]["gained"]),
        match["after"]["shift"],
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _gain_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_ink_gain(rule_id, match) -> None:
    """Check an ink-gain rule at load: every pivot form on either side must belong to one family, since the rule describes one letter's extra cells, and `gained` must name at least one cell. The shift may be zero, because a fuller form can keep its advance."""
    families = {_family(name) for name in match["before"]["pivots"] + match["after"]["pivots"]}
    if len(families) != 1:
        _fail(
            f"rule {rule_id!r}: the pivot lists span families {sorted(families)}; an ink-gain rule "
            "speaks for one letter's extra cells"
        )
    if not match["after"]["gained"]:
        _fail(
            f"rule {rule_id!r}: match.after.gained names no cells; the gain is the whole change an "
            "ink-gain rule blesses"
        )


def _join_pairs(match, unit):
    """Return the before-glyph indices where the named join became a break. The unit must line up letter for letter. At each index the glyph carries the pivot prefix, the next glyph is in a named follower family, the before seam between them is `seam_out` and the after seam is a break, and each after cell belongs to its before glyph's family. Where the rule names `pivot_cells` and `receiver_cells`, the two after cells must be among them."""
    if not _letter_for_letter(unit):
        return []
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    followers = _families(match["before"]["follower"])
    pivot = match["before"]["pivot"]
    seam = match["before"]["seam_out"]
    pivot_cells = match["after"].get("pivot_cells")
    receiver_cells = match["after"].get("receiver_cells")
    reach = min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1
    return [
        i
        for i in range(reach)
        if _is_pivot(glyphs[i], pivot)
        and _family(glyphs[i + 1]) in followers
        and seams[i] == seam
        and after_seams[i] == "break"
        and (pivot_cells is None or cells[i] in pivot_cells)
        and (receiver_cells is None or cells[i + 1] in receiver_cells)
        and _cell_rune(cells[i]) == _family(glyphs[i])
        and _cell_rune(cells[i + 1]) == _family(glyphs[i + 1])
    ]


def _join_piece_holds(before, after):
    """Whether one piece of a dropped join kept its picture: same shape, height, and own-frame origin, on the grid. The caller checks its placement against the running displacement."""
    if before is None or after is None:
        return False
    if before[1] != after[1] or before[3] != after[3] or before[4] != after[4]:
        return False
    return before[2] % PIXEL_SIZE == 0 and after[2] % PIXEL_SIZE == 0 and before[3] % PIXEL_SIZE == 0


def _join_pivot_holds(match, before, after):
    """Whether a dropped join's pivot passes its rule's check: its picture and own-frame origin are unchanged where the rule names no cells, and only its origin where the rule names the cells both letters settle into. In the second case the letter may redraw in place, limited by those cell names as a retargeted join's pivot is."""
    if match["after"].get("pivot_cells"):
        return _retarget_piece_holds(before, after)
    return _join_piece_holds(before, after)


def _join_follower_holds(match, before, after):
    """Whether a dropped join's follower passes its rule's check: its picture and own-frame origin are unchanged where the rule declares no `follower_give_back`. Where it declares one, its own-frame origin moves right by that many columns and its picture may change within the receiver cells the rule names. That is the reverse of a created join's `follower_reach`: the columns the joining form inserted at its left edge come off as the join goes."""
    give_back = match["after"].get("follower_give_back", 0)
    if give_back:
        return _retarget_piece_holds(before, after, -give_back)
    return _join_piece_holds(before, after)


def _join_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is the named join becoming a gap, shaped under the unit's first config. For every pair `_join_pairs` finds, the pivot must pass `_join_pivot_holds` and the follower `_join_follower_holds`. Each span must paint the same pixels once displaced by the cumulative gap: the span before the first follower by nothing, the span the first follower leads by one gap, and one more gap for each further pair. A pivot that may redraw, or a follower with a give-back, is left out of the spans and only its placement is checked. No pair, a before run that differs from the recorded glyphs, a glyph count that differs from the recorded cells, a piece that fails its check, or an off-grid placement returns False, so the unit queues."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    try:
        text = "".join(chr(int(value, 16)) for value in codepoints.split(":"))
    except ValueError:
        return False
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return False
    after_names, after_run = comparator.named_run("after", text, features)
    cells = unit["after"]["cells"]
    if len(after_names) != len(cells):
        return False
    before_pieces = _pieces_by_glyph(before_names, before_run)
    after_pieces = _pieces_by_glyph(after_names, after_run)
    if before_pieces is None or after_pieces is None:
        return False
    pairs = _join_pairs(match, unit)
    if not pairs:
        return False
    for index in pairs:
        if not _join_pivot_holds(match, before_pieces.get(index), after_pieces.get(index)):
            return False
        if not _join_follower_holds(match, before_pieces.get(index + 1), after_pieces.get(index + 1)):
            return False
    intern = comparator.intern
    gap = match["after"]["gap"]
    followers = {index + 1 for index in pairs}
    pivots = set(pairs) if match["after"].get("pivot_cells") else set()
    redrawn = followers if match["after"].get("follower_give_back") else set()
    before_span: list = []
    after_span: list = []
    step = 0
    for index in range(len(before_names)):
        if index in followers or index in pivots:
            if not _span_settled(intern, before_span, after_span, gap * step):
                return False
            before_span, after_span = [], []
            if index in followers:
                step += 1
            piece_before, piece_after = before_pieces.get(index), after_pieces.get(index)
            if piece_before is None or piece_after is None:
                return False
            if piece_after[2] != piece_before[2] + gap * step * PIXEL_SIZE:
                return False
            if index not in pivots and index not in redrawn:
                before_span, after_span = [piece_before], [piece_after]
            continue
        if index in before_pieces:
            before_span.append(before_pieces[index])
        if index in after_pieces:
            after_span.append(after_pieces[index])
    return _span_settled(intern, before_span, after_span, gap * step)


def _matches_join_dropped(match, unit, excluded, context=None):
    """A named join that is now a break, matched at the rendered-pixel grain: the pivot keeps its own-frame origin, and its picture too unless the rule names in full the cells both letters settle into. The follower keeps its picture and origin, or, where the rule declares `follower_give_back`, moves its origin right by that count and redraws inside the receiver cells. The follower sits the declared number of columns further away, and everything after it moves by the same gap (`_join_geometry`). The origin check separates a cursive attachment going away from a sidebearing change, so a slide of the follower fails it. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not _join_pairs(match, unit):
        return False
    if context is None:
        raise ValueError(
            "the join-dropped shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        match["before"]["pivot"],
        match["before"]["seam_out"],
        tuple(_families(match["before"]["follower"])),
        match["after"]["gap"],
        tuple(match["after"].get("pivot_cells", ())),
        tuple(match["after"].get("receiver_cells", ())),
        match["after"].get("follower_give_back", 0),
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _join_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_join_dropped(rule_id, match) -> None:
    """Check a join-dropped rule at load. The gap must not be negative, because a dropped join moves the letters apart. It may be zero only where the rule names the cells both letters settle into: with both pictures unchanged a zero gap is ink-identical and machine-approved already, while a pivot that may redraw can lose its join and leave the follower where it stood (the flipped ·No before ·Cheer). `seam_out` must be a yK height, since a break has no join to drop. `pivot_cells` and `receiver_cells` are optional but come as a pair, because naming both is what lets the pivot redraw, and their cells must belong to the named pivot and follower families. A declared `follower_give_back` must be at least 1 and needs `receiver_cells`: a follower that keeps its left edge leaves the field off, and one that reaches back over it belongs to the join-created shape."""
    if match["after"]["gap"] == 0 and "pivot_cells" not in match["after"]:
        _fail(
            f"rule {rule_id!r}: match.after.gap is 0 with both pictures held; an unmoved window is "
            "ink-identical and machine-approved already"
        )
    if match["after"]["gap"] < 0:
        _fail(
            f"rule {rule_id!r}: match.after.gap is {match['after']['gap']}; a dropped join sits "
            "the letters further apart, never closer"
        )
    if not SEAM_ROW.fullmatch(match["before"]["seam_out"]):
        _fail(
            f"rule {rule_id!r}: match.before.seam_out names {match['before']['seam_out']!r}, "
            "which is not a yK height; a break has no join to drop"
        )
    named = (
        ("pivot_cells", [_family(match["before"]["pivot"])]),
        ("receiver_cells", _families(match["before"]["follower"])),
    )
    if len([field for field, _runes in named if field in match["after"]]) == 1:
        _fail(
            f"rule {rule_id!r}: match.after names one of pivot_cells and receiver_cells; a rule "
            "that frees its pivot to redraw names the cells both letters settle into, or neither "
            "and holds both pictures"
        )
    for field, runes in named:
        for cell in match["after"].get(field, ()):
            if _cell_rune(cell) not in runes:
                _fail(
                    f"rule {rule_id!r}: match.after.{field} entry {cell!r} is not a cell of "
                    f"{' or '.join(runes)}"
                )
    if "follower_give_back" in match["after"]:
        if match["after"]["follower_give_back"] < 1:
            _fail(
                f"rule {rule_id!r}: match.after.follower_give_back is "
                f"{match['after']['follower_give_back']}; a follower that keeps its own pen leaves "
                "the field off, and one that reaches back over it is a created join's, not a "
                "dropped join's"
            )
        if "receiver_cells" not in match["after"]:
            _fail(
                f"rule {rule_id!r}: match.after.follower_give_back needs receiver_cells; the names "
                "the follower settles into are what bound the redraw the give-back frees it to make"
            )


def _entry_columns(match):
    """Return the column count an entry-extension-dropped or entry-contracted rule names."""
    after = match["after"]
    return after.get("entry_drop", after.get("entry_contraction"))


def _entry_shift(match, before_name, after_name):
    """Return how far the glyphs after the pivot move for one entry shortening: minus the named count, plus, for an entry-contracted rule, the change in the pivot's exit extension between the before and after glyph names."""
    shift = -_entry_columns(match)
    if "entry_contraction" in match["after"]:
        shift += _glyph_adjustment(after_name, EXIT_EXTENSION) - _glyph_adjustment(
            before_name, EXIT_EXTENSION
        )
    return shift


def _entry_drop_holds(match, before, after, intern):
    """Return the pivot's placement offset, in columns (zero or negative), under the named entry shortening, or None when the piece is not one. Both pieces must have the same height and sit on the grid. In the first case, an old entry extension comes off: the own-frame origin is unchanged, the after picture is the before picture compacted left by the named columns, and the offset is zero. In the second case, the after glyph names an `en-con-N` of the named count and the before glyph no entry extension. The pictures are aligned by however far the after form moved its own-frame origin (0 to N columns), and the placement moves left by the rest, so the ink ends up in the same place either way. The lost left-side cells must lie inside the contracted columns, and any far-right difference must be a one-row run exactly as wide as the change in exit extension the glyph names state. No other cell may disappear or appear."""
    if before[3] != after[3]:
        return None
    if before[2] % PIXEL_SIZE or after[2] % PIXEL_SIZE or before[3] % PIXEL_SIZE:
        return None
    painted, kept = intern.cells(before[1]), intern.cells(after[1])
    if painted is None or kept is None or not kept:
        return None
    columns = _entry_columns(match)
    shifted = {(column + columns, row) for column, row in kept}
    dropped = painted - shifted
    gained = shifted - painted
    if before[4] == after[4] and dropped and not gained and all(column < columns for column, _row in dropped):
        return 0
    origin_move = after[4] - before[4]
    if origin_move % PIXEL_SIZE:
        return None
    offset = origin_move // PIXEL_SIZE
    if not 0 <= offset <= columns:
        return None
    if offset != columns:
        shifted = {(column + offset, row) for column, row in kept}
        dropped = painted - shifted
        gained = shifted - painted
    lead = offset - columns
    if _glyph_adjustment(after[0], ENTRY_CONTRACTION) != columns:
        return None
    if _glyph_adjustment(before[0], ENTRY_EXTENSION):
        return None
    entry_dropped = {(column, row) for column, row in dropped if column < columns}
    tail_dropped = dropped - entry_dropped
    if not entry_dropped:
        return None
    before_extension = _glyph_adjustment(before[0], EXIT_EXTENSION)
    after_extension = _glyph_adjustment(after[0], EXIT_EXTENSION)
    extension_delta = after_extension - before_extension
    if extension_delta >= 0:
        if tail_dropped or len(gained) != extension_delta:
            return None
        if not gained:
            return lead
        edge = max(column for column, _row in painted)
        if len({row for _column, row in gained}) == 1 and {column for column, _row in gained} == set(
            range(edge + 1, edge + 1 + extension_delta)
        ):
            return lead
        return None
    if gained or len(tail_dropped) != -extension_delta:
        return None
    edge = max(column for column, _row in shifted)
    if len({row for _column, row in tail_dropped}) == 1 and {column for column, _row in tail_dropped} == set(
        range(edge + 1, edge + 1 - extension_delta)
    ):
        return lead
    return None


def _entry_geometry(match, unit, comparator, pivot_positions=None):
    """Whether the window's rendered before→after change is the named left-side entry shortening on the named pivots, shaped under the unit's first config. Each pivot must pass `_entry_drop_holds` and sit at the placement it returns under the running displacement, and every span strictly between the pivots must render identically once displaced by the cumulative shift (`_entry_shift`): the span before the first pivot by nothing, and one more shift after each pivot. Each span after a pivot is compared together with that pivot's after picture, so a following glyph may give up cells the pivot still paints without that counting as a change. The entry-contracted matcher passes the positions its named left families select; the entry-extension-dropped matcher leaves `pivot_positions` unset and every named pivot is judged. No pivot on the before side, pivot counts that differ, a before run that differs from the recorded glyphs, an off-grid placement, a non-rectilinear outline, a dropped cell outside the named columns, or an unnamed visible cell returns False, so the unit queues."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    try:
        text = "".join(chr(int(value, 16)) for value in codepoints.split(":"))
    except ValueError:
        return False
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return False
    _after_names, after_run = comparator.named_run("after", text, features)
    if pivot_positions is None:
        before_pivots = [
            i for i, piece in enumerate(before_run) if _named_pivot(piece[0], match["before"]["pivots"])
        ]
        after_pivots = [
            i for i, piece in enumerate(after_run) if _named_pivot(piece[0], match["after"]["pivots"])
        ]
    else:
        before_pivots = list(pivot_positions)
        after_pivots = list(pivot_positions)
        if any(
            index >= len(before_run)
            or index >= len(after_run)
            or _family(before_run[index][0]) != _family(after_run[index][0])
            or not _named_contracted_pivot(
                before_run[index][0], match["before"]["pivots"], unit["before"]["seams"][index - 1]
            )
            or not _named_contracted_pivot(
                after_run[index][0], match["after"]["pivots"], unit["after"]["seams"][index - 1]
            )
            for index in before_pivots
        ):
            return False
    if not before_pivots or len(before_pivots) != len(after_pivots):
        return False
    intern = comparator.intern
    before_spans = _split_around(before_run, before_pivots)
    after_spans = _split_around(after_run, after_pivots)
    if not _span_settled(intern, before_spans[0], after_spans[0], 0):
        return False
    displacement = 0
    for step in range(len(before_pivots)):
        before = before_run[before_pivots[step]]
        after = after_run[after_pivots[step]]
        lead = _entry_drop_holds(match, before, after, intern)
        if lead is None:
            return False
        if after[2] != before[2] + (displacement + lead) * PIXEL_SIZE:
            return False
        displacement += _entry_shift(match, before[0], after[0])
        if not _span_settled(
            intern,
            before_spans[step + 1],
            after_spans[step + 1],
            displacement,
            after_anchor=after,
        ):
            return False
    return True


def _matches_entry_drop(match, unit, excluded, context=None):
    """A letter that gives up a named stretch of left-side entry, matched at the rendered-pixel grain: either the old form's extra entry columns come off under an unchanged own-frame origin, or a named `en-con-N` on the after form brings the letter that many columns closer, taken into its own-frame origin, its placement, or both (`_entry_geometry`). Everything after the pivot moves closer by that count. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not any(_named_pivot(name, match["before"]["pivots"]) for name in unit["before"]["glyphs"]):
        return False
    if context is None:
        raise ValueError(
            "the entry-extension-dropped shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        "entry-extension-dropped",
        tuple(match["before"]["pivots"]),
        tuple(match["after"]["pivots"]),
        match["after"]["entry_drop"],
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _entry_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _contracted_entry_candidates(match, unit):
    """Return the positions where a named pivot, alone or leading an existing ligature (`_named_contracted_pivot`), stands immediately after one of the rule's named left families and is not one of its `except_pivots`. A ligature position also requires the unit to line up letter for letter."""
    glyphs = unit["before"]["glyphs"]
    left_families = set(_families(match["before"]["left"]))
    declined = match["before"].get("except_pivots", ())
    return [
        index
        for index, name in enumerate(glyphs)
        if index
        and index <= len(unit["before"]["seams"])
        and ("_" not in _family(name) or (_letter_for_letter(unit) and index <= len(unit["after"]["seams"])))
        and _named_contracted_pivot(name, match["before"]["pivots"], unit["before"]["seams"][index - 1])
        and not _named_contracted_pivot(name, declined, unit["before"]["seams"][index - 1])
        and _joining_family(glyphs[index - 1]) in left_families
    ]


def _matches_entry_contracted(match, unit, excluded, context=None):
    """One or more named left–pivot pairs whose pivot contracts its entry by the declared columns, matched at the rendered-pixel grain by `_entry_geometry`, as in the contraction case of the entry-extension-dropped shape. The named left families limit the rule to those pairs. The after glyph must carry the declared `en-con-N`, and the letter must come that many columns closer however its frame took the contraction (own-frame origin, placement, or both). The only far-right change allowed is the exit-extension change the before and after glyph names state. Everything after the pivot must move by the contraction plus that exit-extension change. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    candidates = _contracted_entry_candidates(match, unit)
    if not candidates:
        return False
    if context is None:
        raise ValueError(
            "the entry-contracted shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        tuple(_families(match["before"]["left"])),
        tuple(match["before"]["pivots"]),
        tuple(match["before"].get("except_pivots", ())),
        tuple(match["after"]["pivots"]),
        match["after"]["entry_contraction"],
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _entry_geometry(
            match, unit, context.comparator, pivot_positions=candidates
        )
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _stub_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is the named left-side stub coming off the named pivot while its remaining ink stays in place, shaped under the unit's first config. The pivot's picture is compacted left as in an entry drop (`_entry_drop_holds` read as an entry drop of the declared count, returning a zero placement offset), its placement moves right by the declared column count so the ink it keeps does not move, and every span between pivots renders identically with no displacement. A pivot is a position whose before name carries a before prefix and whose after name carries an after prefix. The walk goes by position because the same after form can be the stub-dropped letter at one position and an unchanged letter of the same family at another (a second ·May keeping its old loop), and only the before name says which. No pivot position, a before run that differs from the recorded glyphs, a different glyph count on the two sides, an off-grid placement, a non-rectilinear outline, a dropped cell outside the named columns, or an `en-con-N` pivot whose own frame takes less than the full count (a negative offset, so a move right by the count carries its kept ink with it) returns False, so the unit queues."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    try:
        text = "".join(chr(int(value, 16)) for value in codepoints.split(":"))
    except ValueError:
        return False
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return False
    after_names, after_run = comparator.named_run("after", text, features)
    if len(after_names) != len(before_names):
        return False
    before_pieces = _pieces_by_glyph(before_names, before_run)
    after_pieces = _pieces_by_glyph(after_names, after_run)
    if before_pieces is None or after_pieces is None:
        return False
    pivots = {
        index
        for index in range(len(before_names))
        if _named_pivot(before_names[index], match["before"]["pivots"])
        and _named_pivot(after_names[index], match["after"]["pivots"])
    }
    if not pivots:
        return False
    intern = comparator.intern
    columns = match["after"]["stub_drop"]
    before_span: list = []
    after_span: list = []
    for index in range(len(before_names)):
        if index not in pivots:
            if index in before_pieces:
                before_span.append(before_pieces[index])
            if index in after_pieces:
                after_span.append(after_pieces[index])
            continue
        if not _span_settled(intern, before_span, after_span, 0):
            return False
        before, after = before_pieces.get(index), after_pieces.get(index)
        if before is None or after is None:
            return False
        if after[2] != before[2] + columns * PIXEL_SIZE or (
            _entry_drop_holds({"after": {"entry_drop": columns}}, before, after, intern) != 0
        ):
            return False
        before_span, after_span = [], []
    return _span_settled(intern, before_span, after_span, 0)


def _matches_stub_drop(match, unit, excluded, context=None):
    """A letter that gives up a named left-side stub while its remaining ink stays put, matched at the rendered-pixel grain: the old-font pivot form becomes a named new form whose own-frame picture is the old one compacted left by the declared column count, with its placement moved right by that count and everything else in the window unmoved (`_stub_geometry`). The placement move separates this from an entry drop, whose remaining ink moves closer while its placement stays. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not any(_named_pivot(name, match["before"]["pivots"]) for name in unit["before"]["glyphs"]):
        return False
    if context is None:
        raise ValueError(
            "the stub-dropped shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        "stub-dropped",
        tuple(match["before"]["pivots"]),
        tuple(match["after"]["pivots"]),
        match["after"]["stub_drop"],
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _stub_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_stub_drop(rule_id, match) -> None:
    """Check a stub-dropped rule at load: the drop must be positive, and every pivot form on either side must belong to one family, since the rule describes one letter's lost left-side stub."""
    if match["after"]["stub_drop"] == 0:
        _fail(
            f"rule {rule_id!r}: match.after.stub_drop is 0; an unmoved window is ink-identical and "
            "machine-approved already"
        )
    if match["after"]["stub_drop"] < 0:
        _fail(
            f"rule {rule_id!r}: match.after.stub_drop is {match['after']['stub_drop']}; a stub drop "
            "sits the remaining ink still, never further left"
        )
    families = {_family(name) for name in match["before"]["pivots"] + match["after"]["pivots"]}
    if len(families) != 1:
        _fail(
            f"rule {rule_id!r}: the pivot lists span families {sorted(families)}; a stub-drop rule "
            "speaks for one letter's lost left-side pixel"
        )


def _validate_entry_drop(rule_id, match) -> None:
    """Check an entry-extension-dropped rule at load, and the part of an entry-contracted rule the two shapes share: the count must be positive, since zero is ink-identical and machine-approved already and a lost stretch brings the letters closer, and every pivot form on either side must belong to one family, since the rule describes one letter's lost left-side stretch."""
    columns = _entry_columns(match)
    if columns == 0:
        _fail(
            f"rule {rule_id!r}: the entry shortening is 0; an unmoved window is ink-identical and "
            "machine-approved already"
        )
    if columns < 0:
        _fail(
            f"rule {rule_id!r}: the entry shortening is {columns}; an entry drop "
            "sits the letters closer together, never further"
        )
    families = {_family(name) for name in match["before"]["pivots"] + match["after"]["pivots"]}
    if len(families) != 1:
        _fail(
            f"rule {rule_id!r}: the pivot lists span families {sorted(families)}; an entry-drop rule "
            "speaks for one letter's lost left-side stretch"
        )


def _validate_entry_contracted(rule_id, match) -> None:
    """Check an entry-contracted rule at load: the checks `_validate_entry_drop` makes, then that `left` names bare Quikscript family names and that every `except_pivots` form falls under a pivot prefix."""
    _validate_entry_drop(rule_id, match)
    left = match["before"]["left"]
    families = _families(left)
    if not all(family.startswith("qs") and "." not in family and "/" not in family for family in families):
        _fail(
            f"rule {rule_id!r}: match.before.left must be a bare Quikscript family name or a list "
            f"of them, got {left!r}"
        )
    pivots = match["before"]["pivots"]
    for declined in match["before"].get("except_pivots", ()):
        if not _named_pivot(declined, pivots):
            _fail(
                f"rule {rule_id!r}: match.before.except_pivots names {declined!r}, which no pivot "
                "prefix reaches"
            )


def _redrawn_trade(match):
    """Return the cell trade a redrawn rule names: the own-frame cells the after form gives up and the ones it adds, each as a set of (column, row) pairs."""
    return (
        {tuple(point) for point in match["after"]["dropped"]},
        {tuple(point) for point in match["after"]["added"]},
    )


def _redrawn_holds(match, before, after, intern):
    """Return how many columns of entry contraction the pivot's own frame took, or None when the piece is not the named redraw. Both pieces must have the same height and sit on the grid, and the after picture must be the before picture with the named dropped cells gone and the named added cells present. Both sets are read at one common column offset, derived from the lost cells, because an entry extension inserts a column at the pivot's left edge and moves the whole frame right, so an entry-extended variant shows the same trade one column over. Deriving the offset from the losses lets `added` be empty. The own-frame origin stays unless the after form names more entry contraction than the before form (`_contraction_room`); then the origin may move right by up to that difference while the ink it keeps stays where it was. The pictures are aligned by that move before the trade is read, and the caller subtracts what the frame took from the room the placement has. A cell lost or gained outside the named trade fails."""
    if before[3] != after[3]:
        return None
    if before[2] % PIXEL_SIZE or after[2] % PIXEL_SIZE or before[3] % PIXEL_SIZE:
        return None
    origin_move = after[4] - before[4]
    if origin_move % PIXEL_SIZE:
        return None
    frame = origin_move // PIXEL_SIZE
    if not 0 <= frame <= _contraction_room(before, after):
        return None
    painted, kept = intern.cells(before[1]), intern.cells(after[1])
    if painted is None or kept is None:
        return None
    kept = {(column + frame, row) for column, row in kept}
    dropped, added = _redrawn_trade(match)
    gone, gained = painted - kept, kept - painted
    if len(gone) != len(dropped) or len(gained) != len(added):
        return None
    offset = min(gone)[0] - min(dropped)[0]
    if {(column + offset, row) for column, row in dropped} != gone:
        return None
    if {(column + offset, row) for column, row in added} != gained:
        return None
    return frame


def _contraction_room(before, after):
    """Return how many more columns of entry contraction the after glyph names than the before glyph, or zero. This is the most a redrawn pivot may move toward its left neighbor, because contraction the old font already drew leaves nothing more to close."""
    return max(
        0,
        _glyph_adjustment(after[0], ENTRY_CONTRACTION) - _glyph_adjustment(before[0], ENTRY_CONTRACTION),
    )


def _pull(before, after, expected, room):
    """Return the pivot's placement offset from where the walk expects it, in columns: zero, or negative down to `-room` when its new form pulls its entry in. Return None when the offset is off the grid, more than `room` columns left, or to the right at all."""
    offset = after[2] - before[2] - expected * PIXEL_SIZE
    if offset % PIXEL_SIZE or not -room * PIXEL_SIZE <= offset <= 0:
        return None
    return offset // PIXEL_SIZE


def _push(before, after, expected, room):
    """Return the pivot's placement offset from where the walk expects it, in columns: zero, or up to `room` columns right when the left-side entry the old font drew in front of the join comes off and the remaining ink starts later. Return None when the offset is off the grid, more than `room` columns right, or to the left at all."""
    offset = after[2] - before[2] - expected * PIXEL_SIZE
    if offset % PIXEL_SIZE or not 0 <= offset <= room * PIXEL_SIZE:
        return None
    return offset // PIXEL_SIZE


def _redrawn_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is the named redraw at every pivot position, shaped under the unit's first config. A pivot is a position whose before name carries a before prefix and whose after name carries an after prefix. The walk is by position because the same before name can be a pivot at one position and unchanged at another (a second ·Eight that keeps its normal loop), and only the after name says which. Each pivot must pass `_redrawn_holds`, and its placement may sit left of the running displacement by at most the contraction room its own frame did not take (`_pull`). At each pivot the displacement grows by the declared shift plus that offset, and every span between pivots must render identically under it. No pivot position, a before run that differs from the recorded glyphs, a different glyph count on the two sides, an off-grid placement, a non-rectilinear outline, or a cell traded outside the named sets returns False, so the unit queues."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    try:
        text = "".join(chr(int(value, 16)) for value in codepoints.split(":"))
    except ValueError:
        return False
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return False
    after_names, after_run = comparator.named_run("after", text, features)
    if len(after_names) != len(before_names):
        return False
    before_pieces = _pieces_by_glyph(before_names, before_run)
    after_pieces = _pieces_by_glyph(after_names, after_run)
    if before_pieces is None or after_pieces is None:
        return False
    pivots = {
        index
        for index in range(len(before_names))
        if _named_pivot(before_names[index], match["before"]["pivots"])
        and _named_pivot(after_names[index], match["after"]["pivots"])
    }
    if not pivots:
        return False
    intern = comparator.intern
    shift = match["after"]["shift"]
    displacement = 0
    before_span: list = []
    after_span: list = []
    for index in range(len(before_names)):
        if index not in pivots:
            if index in before_pieces:
                before_span.append(before_pieces[index])
            if index in after_pieces:
                after_span.append(after_pieces[index])
            continue
        if not _span_settled(intern, before_span, after_span, displacement):
            return False
        before, after = before_pieces.get(index), after_pieces.get(index)
        if before is None or after is None:
            return False
        frame = _redrawn_holds(match, before, after, intern)
        if frame is None:
            return False
        pull = _pull(before, after, displacement, _contraction_room(before, after) - frame)
        if pull is None:
            return False
        displacement += shift + pull
        before_span, after_span = [], []
    return _span_settled(intern, before_span, after_span, displacement)


def _matches_redrawn(match, unit, excluded, context=None):
    """A letter redrawn in place to a named new form, matched at the rendered-pixel grain: the before pivot form becomes an after pivot form whose own-frame picture is the old one with the named cells dropped and added at one common column offset (`_redrawn_geometry`). The own-frame origin and the placement stay where they were unless the new form names more entry contraction than the old one. The frame may take up to all of that extra contraction, and the placement may move left by the part the frame did not take. Everything after the pivot moves by the declared shift plus that placement change; the shift may be zero when the new form keeps the pivot's advance. The added set may be empty, for a form that only loses ink: ·Key's foot dropping its terminal pixel and its follower coming a column closer. The extension-dropped shape reads only names and so cannot see the rest of the window, which is why this shape is the one for an exit contraction in a window that carries anything else. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not any(_named_pivot(name, match["before"]["pivots"]) for name in unit["before"]["glyphs"]):
        return False
    if context is None:
        raise ValueError(
            "the redrawn shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        tuple(match["before"]["pivots"]),
        tuple(match["after"]["pivots"]),
        tuple(sorted(tuple(point) for point in match["after"]["dropped"])),
        tuple(sorted(tuple(point) for point in match["after"]["added"])),
        match["after"]["shift"],
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _redrawn_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_redrawn(rule_id, match) -> None:
    """Check a redrawn rule at load: all pivot forms on both sides belong to one family, since the rule describes one letter's new form; `dropped` names at least one cell; and no cell is in both `dropped` and `added`. `added` may be empty, for a form that only loses ink (·Key's foot losing its terminal pixel before ·May, ·No, and ·It)."""
    families = {_family(name) for name in match["before"]["pivots"] + match["after"]["pivots"]}
    if len(families) != 1:
        _fail(
            f"rule {rule_id!r}: the pivot lists span families {sorted(families)}; a redrawn rule "
            "speaks for one letter's new form"
        )
    if not match["after"]["dropped"]:
        _fail(
            f"rule {rule_id!r}: match.after.dropped names no cells; a form that gives nothing up is "
            "not redrawn, and a pure gain is the ink-gain shape's subject"
        )
    dropped, added = _redrawn_trade(match)
    shared = dropped & added
    if shared:
        _fail(
            f"rule {rule_id!r}: match.after.dropped and match.after.added share {sorted(shared)}; "
            "a cell traded for itself names no change"
        )


def _retarget_pairs(match, unit):
    """Return the before-glyph indices where the named pair changed its join state. The unit must line up letter for letter. At each index the glyph carries one of the pivot prefixes and none of the `except_pivots` forms, the next glyph is in a named follower family, the before seam between them is `seam_out` and the after seam is the rule's `retarget` or `joined` height, the two after cells are in `pivot_cells` and `receiver_cells`, and each after cell belongs to its before glyph's family. `except_pivots` lets a rule skip a before form that falls under one of its prefixes but needs a different count, since a prefix also matches every longer form."""
    if not _letter_for_letter(unit):
        return []
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    followers = _families(match["before"]["follower"])
    pivots = _families(match["before"]["pivot"])
    declined = match["before"].get("except_pivots", ())
    seam = match["before"]["seam_out"]
    retarget = match["after"].get("retarget", match["after"].get("joined"))
    reach = min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1
    return [
        i
        for i in range(reach)
        if any(_is_pivot(glyphs[i], pivot) for pivot in pivots)
        and not _named_pivot(glyphs[i], declined)
        and _family(glyphs[i + 1]) in followers
        and seams[i] == seam
        and after_seams[i] == retarget
        and cells[i] in match["after"]["pivot_cells"]
        and cells[i + 1] in match["after"]["receiver_cells"]
        and _cell_rune(cells[i]) == _family(glyphs[i])
        and _cell_rune(cells[i + 1]) == _family(glyphs[i + 1])
    ]


def _retarget_piece_holds(before, after, reach=0):
    """Whether one piece of a created or retargeted join kept its own-frame origin, or moved it left by exactly `reach` columns (right, for a negative `reach`), with its placements on the pixel grid. Its height and picture may change. The caller checks its placement against the running displacement."""
    if before is None or after is None:
        return False
    if after[4] != before[4] - reach * PIXEL_SIZE:
        return False
    return before[2] % PIXEL_SIZE == 0 and after[2] % PIXEL_SIZE == 0 and before[3] % PIXEL_SIZE == 0


def _retarget_geometry(match, unit, comparator, follower_shift, onward, follower_reach=0, pivot_room=0):
    """Whether the window's rendered before→after change is the named pair gaining a join or changing its join height, shaped under the unit's first config. For every pair `_retarget_pairs` finds, the pivot keeps its own-frame origin and its placement stays put or sits up to `pivot_room` columns further right (`_push`). The follower keeps its own-frame origin or moves it left by `follower_reach`, and its placement moves by `follower_shift` plus the pivot offsets so far, this pair's included. Each span outside the pairs must render identically, displaced by `onward` for each pair before it plus the pivot offsets so far. A retarget passes its `follower_shift` and `shift`; a created join passes its `shift` as `follower_shift`, and its `shift` plus `follower_advance` as `onward`. No pair, a before run that differs from the recorded glyphs, a mismatched glyph count, a piece that moved in a way the rule does not declare, or an off-grid placement returns False, so the unit queues."""
    codepoints = unit.get("codepoints") or ""
    if not codepoints:
        return False
    try:
        text = "".join(chr(int(value, 16)) for value in codepoints.split(":"))
    except ValueError:
        return False
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return False
    after_names, after_run = comparator.named_run("after", text, features)
    cells = unit["after"]["cells"]
    if len(after_names) != len(cells):
        return False
    before_pieces = _pieces_by_glyph(before_names, before_run)
    after_pieces = _pieces_by_glyph(after_names, after_run)
    if before_pieces is None or after_pieces is None:
        return False
    pairs = _retarget_pairs(match, unit)
    if not pairs:
        return False
    taken = [0]
    for index in pairs:
        if any(
            pieces.get(at) is None for pieces in (before_pieces, after_pieces) for at in (index, index + 1)
        ):
            return False
        pivot_before, pivot_after = before_pieces[index], after_pieces[index]
        follower_before, follower_after = before_pieces[index + 1], after_pieces[index + 1]
        if not _retarget_piece_holds(pivot_before, pivot_after):
            return False
        push = _push(pivot_before, pivot_after, taken[-1], pivot_room)
        if push is None:
            return False
        if not _retarget_piece_holds(follower_before, follower_after, follower_reach):
            return False
        if follower_after[2] != follower_before[2] + (follower_shift + taken[-1] + push) * PIXEL_SIZE:
            return False
        taken.append(taken[-1] + push)
    intern = comparator.intern
    pivots = set(pairs)
    followers = {index + 1 for index in pairs}
    before_span: list = []
    after_span: list = []
    step = 0
    for index in range(len(before_names)):
        if index in pivots:
            if not _span_settled(intern, before_span, after_span, onward * step + taken[step]):
                return False
            continue
        if index in followers:
            before_span = []
            after_span = []
            step += 1
            continue
        if index in before_pieces:
            before_span.append(before_pieces[index])
        if index in after_pieces:
            after_span.append(after_pieces[index])
    return _span_settled(intern, before_span, after_span, onward * step + taken[step])


def _matches_join_retarget(match, unit, excluded, context=None):
    """A named join that has changed height, matched at the rendered-pixel grain: the named seam becomes the `retarget` height, the pivot and follower may both redraw but keep their own-frame origins, the pivot keeps its placement, the follower's placement moves by `follower_shift` (negative is nearer, zero leaves it standing), and everything after the follower moves by `shift` (`_retarget_geometry`). The unmoved origins and the unmoved pivot tie the change to the join and the two letters' forms, which rules out a slide or a dropped join. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not _retarget_pairs(match, unit):
        return False
    if context is None:
        raise ValueError(
            "the join-retargeted shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        match["before"]["pivot"],
        match["before"]["seam_out"],
        tuple(_families(match["before"]["follower"])),
        match["after"]["retarget"],
        tuple(match["after"]["pivot_cells"]),
        tuple(match["after"]["receiver_cells"]),
        match["after"]["shift"],
        match["after"]["follower_shift"],
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _retarget_geometry(
            match, unit, context.comparator, match["after"]["follower_shift"], match["after"]["shift"]
        )
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _matches_join_created(match, unit, excluded, context=None):
    """A named pair, or any of several named pivots into one follower, that has newly joined, matched at the rendered-pixel grain: the recorded break becomes the `joined` height, and the pivot and follower may both redraw (`_retarget_geometry`). The pivot keeps its own-frame origin, and its placement stays put or sits up to `pivot_stub_drop` columns further right. The follower keeps its own-frame origin or moves it left by `follower_reach`, and its placement moves by `shift` plus the pivot's offset. Everything after the follower moves by that plus `follower_advance`. Any other ink change in the window fails this match; the composed reading handles a window that also carries a second approved change. The unit's ink-delta digest must be the same under every config it lists, so shaping the first config stands for all of them. except_left reads the whole window."""
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return False
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return False
    if not _retarget_pairs(match, unit):
        return False
    if context is None:
        raise ValueError(
            "the join-created shape re-shapes windows in the surface's fonts and needs a SlideContext"
        )
    key = (
        "join-created",
        tuple(_families(match["before"]["pivot"])),
        tuple(match["before"].get("except_pivots", ())),
        tuple(_families(match["before"]["follower"])),
        match["after"]["joined"],
        tuple(match["after"]["pivot_cells"]),
        tuple(match["after"]["receiver_cells"]),
        match["after"]["shift"],
        match["after"]["follower_advance"],
        match["after"]["follower_reach"],
        match["after"].get("pivot_stub_drop", 0),
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        shift = match["after"]["shift"]
        verdict = context.memo[key] = _retarget_geometry(
            match,
            unit,
            context.comparator,
            shift,
            shift + match["after"]["follower_advance"],
            match["after"]["follower_reach"],
            match["after"].get("pivot_stub_drop", 0),
        )
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_join_retarget(rule_id, match) -> None:
    """Check a join-retargeted rule at load: `seam_out` and `retarget` must both be yK heights and must differ, and the pivot and receiver cells must belong to the named pivot and follower families. A break before has no join to retarget, a break after belongs to the join-dropped shape, and an unchanged height belongs to the extension-dropped shape."""
    if not SEAM_ROW.fullmatch(match["before"]["seam_out"]):
        _fail(
            f"rule {rule_id!r}: match.before.seam_out names {match['before']['seam_out']!r}, "
            "which is not a yK height; a break has no join to retarget"
        )
    if not SEAM_ROW.fullmatch(match["after"]["retarget"]):
        _fail(
            f"rule {rule_id!r}: match.after.retarget names {match['after']['retarget']!r}, "
            "which is not a yK height; a join that becomes a break is the gap shape's subject"
        )
    if match["before"]["seam_out"] == match["after"]["retarget"]:
        _fail(
            f"rule {rule_id!r}: match.after.retarget is the same height as match.before.seam_out; "
            "a seam that holds its height is not a retarget"
        )
    named = (
        ("pivot_cells", [_family(match["before"]["pivot"])]),
        ("receiver_cells", _families(match["before"]["follower"])),
    )
    for field, runes in named:
        for cell in match["after"][field]:
            if _cell_rune(cell) not in runes:
                _fail(
                    f"rule {rule_id!r}: match.after.{field} entry {cell!r} is not a cell of "
                    f"{' or '.join(runes)}"
                )


def _validate_join_created(rule_id, match) -> None:
    """Check a join-created rule at load. `seam_out` must be `break` and `joined` a yK height, since a pair that already joined belongs to the retarget or extension shape and a new break to the join-dropped shape. `follower_reach` must not be negative, since a follower that pulls its left edge in is an entry contraction. A declared `pivot_stub_drop` must be at least 1; a rule without a stub drop leaves the field off. Every `except_pivots` form must fall under one of the named pivots, since a declined form outside them declines nothing and misstates the rule's scope. The pivot and receiver cells must belong to the named pivot and follower families. Naming several pivot families lets one rule record a letter's new entry for every left neighbor that now reaches it."""
    if match["before"]["seam_out"] != "break":
        _fail(
            f"rule {rule_id!r}: match.before.seam_out names {match['before']['seam_out']!r}; "
            "a newly created join must start from a break"
        )
    if not SEAM_ROW.fullmatch(match["after"]["joined"]):
        _fail(
            f"rule {rule_id!r}: match.after.joined names {match['after']['joined']!r}, "
            "which is not a yK height"
        )
    if match["after"]["follower_reach"] < 0:
        _fail(
            f"rule {rule_id!r}: match.after.follower_reach is negative; a follower reaches back over "
            "its old left edge or stands where it was, and a frame that pulls in is a contraction"
        )
    if "pivot_stub_drop" in match["after"] and match["after"]["pivot_stub_drop"] < 1:
        _fail(
            f"rule {rule_id!r}: match.after.pivot_stub_drop is "
            f"{match['after']['pivot_stub_drop']}; a pivot that gives up no left-side entry is the "
            "plain created join, which says so by leaving the field off"
        )
    pivots = _families(match["before"]["pivot"])
    for declined in match["before"].get("except_pivots", ()):
        if not _named_pivot(declined, pivots):
            _fail(
                f"rule {rule_id!r}: match.before.except_pivots names {declined!r}, which no pivot "
                f"the rule matches on ({', '.join(pivots)}) reaches"
            )
    named = (
        ("pivot_cells", [_family(name) for name in pivots]),
        ("receiver_cells", _families(match["before"]["follower"])),
    )
    for field, runes in named:
        for cell in match["after"][field]:
            if _cell_rune(cell) not in runes:
                _fail(
                    f"rule {rule_id!r}: match.after.{field} entry {cell!r} is not a cell of "
                    f"{' or '.join(runes)}"
                )


class Event(NamedTuple):
    """One position where a composable rule's contract held in a composed walk.

    `kind` names the shape: `slide`, `extension`, `gain`, `join` (dropped), `entry`, `stub`, `redrawn`, `retarget`, or `joined` (created). `shift` is the columns the running displacement moves at this position: the declared slide, minus the extension's dropped columns, the declared gap, the entry shortening plus any exit-extension change on the pivot, the created join's shift, the columns a retarget moves its follower, or the declared ink-gain or redrawn shift. For `stub`, `shift` is the pivot's own placement offset instead, and the followers do not move.

    `pivot_judged` is False only for a `retarget` or `joined` event whose pivot moved its own-frame origin. The walk keeps either kind only when it chains behind an entry, gain, or redrawn event at the same position, which has judged the pivot, and also keeps such a retarget behind a created join at the previous position, whose `follower_reach` has already checked that move.

    `lead` is an entry event's expected placement offset from the running displacement, zero or negative; it is nonzero only for an entry contraction the after form's own frame did not fully take into its origin. `room` is how far the pivot's placement may sit from the running displacement: to the left for a redrawn event, by the part of its new form's entry contraction its frame did not take, and to the right for a created join, by its `pivot_stub_drop`. `advance` is the further displacement applied once the walk is past the follower: a created join's `follower_advance`, or what a retarget's `shift` leaves after its `follower_shift`. `reach` is a created join's `follower_reach`, the part of its advance a retarget chained behind it still needs. On a dropped join it is `follower_give_back`, which makes the follower's picture part of the event instead of the head of the next span.
    """

    rule_id: str
    kind: str
    shift: int
    pivot_judged: bool = True
    lead: int = 0
    advance: int = 0
    room: int = 0
    reach: int = 0


def _shape_of(match):
    """Return the SHAPES row whose `keyed_by` field the rule's `match.after` carries, or None when it carries none. `load_rules` requires exactly one, so a loaded rule always has a shape."""
    for shape in SHAPES.values():
        if shape.keyed_by in match["after"]:
            return shape
    return None


def _is_composable(rule):
    """Whether a rule's shape has the `composable` flag, so a composed walk may credit it. The module docstring says which shapes compose and why."""
    shape = _shape_of(rule["match"])
    return shape is not None and shape.composable


def _composable(rules):
    """The rules a composed reading may credit, in rules-file order."""
    return [rule for rule in rules if _is_composable(rule)]


def _is_slide_match(match):
    return SHAPES["slide"].keyed_by in match["after"]


def _is_gain_match(match):
    return SHAPES["ink-gain"].keyed_by in match["after"]


def _is_join_match(match):
    return SHAPES["join-dropped"].keyed_by in match["after"]


def _is_entry_match(match):
    return SHAPES["entry-extension-dropped"].keyed_by in match["after"]


def _is_entry_contracted_match(match):
    return SHAPES["entry-contracted"].keyed_by in match["after"]


def _is_stub_match(match):
    return SHAPES["stub-dropped"].keyed_by in match["after"]


def _is_redrawn_match(match):
    return SHAPES["redrawn"].keyed_by in match["after"]


def _is_retarget_match(match):
    return SHAPES["join-retargeted"].keyed_by in match["after"]


def _is_created_join_match(match):
    return SHAPES["join-created"].keyed_by in match["after"]


def _composable_digest(rules):
    """Return a hashable key for a list of composable rules: each rule's id with its match as sorted JSON. `SlideContext.composed` is keyed on it, so a context shared by two rule sets never returns one set's walk for the other. The ids are part of the key because the stored walk names rule ids."""
    return tuple((rule["id"], json.dumps(rule["match"], sort_keys=True)) for rule in rules)


def _candidate_counts(rules, unit):
    """Return each rule's number of candidate positions in the window (`_candidates`), by id in rules-file order, or an empty dict when the unit lacks a before or after record. `Decider.evaluate` computes it once and uses it for both the composed pre-gate and the memo entry's `relevant` rules."""
    if not unit.get("before") or not unit.get("after"):
        return {}
    return {rule["id"]: len(_candidates(rule["match"], unit)) for rule in rules}


def _candidates(match, unit):
    """Return the window positions where a composable rule could hold, read from the unit's index record without shaping. Slide, ink-gain, entry-drop, stub-drop, and redrawn rules use every position whose before glyph carries a before pivot prefix. Entry-contracted rules use `_contracted_entry_candidates`, join-dropped rules `_join_pairs`, and join-retargeted and join-created rules `_retarget_pairs`. Extension rules use `_extension_positions`, and return none when `seam_out` is not a yK height, because the walk needs a row for the dropped tail. This is the composed pre-gate: a rule with no candidate is never credited, and a window with fewer than two candidate positions across all rules is never shaped."""
    glyphs = unit["before"]["glyphs"]
    if (
        _is_slide_match(match)
        or _is_gain_match(match)
        or _is_entry_match(match)
        or _is_stub_match(match)
        or _is_redrawn_match(match)
    ):
        return [i for i, name in enumerate(glyphs) if _named_pivot(name, match["before"]["pivots"])]
    if _is_entry_contracted_match(match):
        return _contracted_entry_candidates(match, unit)
    if _is_join_match(match):
        return _join_pairs(match, unit)
    if _is_retarget_match(match) or _is_created_join_match(match):
        return _retarget_pairs(match, unit)
    if not SEAM_ROW.fullmatch(match["before"]["seam_out"]):
        return []
    return _extension_positions(match, unit)


def _extension_positions(match, unit):
    """Return the positions where an extension-dropped rule's per-position conditions hold, read from the index record. The pivot carries the pivot prefix and the named drop (`_carries_named_drop`), the seam is `seam_out` on both sides, the pivot and follower after cells are in the named lists, and the follower is in a named family with an after cell of that family. Unlike `_candidates`, this does not require a yK seam, so `_reachable` judges a rule whose seam the walk cannot place the way the rule's own matcher does."""
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    mb, ma = match["before"], match["after"]
    followers = _families(mb["follower"])
    reach = min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1
    return [
        i
        for i in range(reach)
        if _is_pivot(glyphs[i], mb["pivot"])
        and _carries_named_drop(mb["exit_extension"], glyphs[i], cells[i])
        and seams[i] == mb["seam_out"]
        and after_seams[i] == mb["seam_out"]
        and cells[i] in ma["pivot_cells"]
        and _family(glyphs[i + 1]) in followers
        and cells[i + 1] in ma["follower_cells"]
        and _cell_rune(cells[i + 1]) == _family(glyphs[i + 1])
    ]


def _reachable(match, unit):
    """Whether a rule could accept or hold the unit, judged from names alone without shaping. A composable rule needs a candidate position (`_candidates`, or `_extension_positions` for an extension rule); a ligature rule needs a glyph with its pivot prefix; an ink-delta rule needs the unit's persisted digests to be a nonempty subset of its own. Each condition is necessary for the rule's matcher, guarded or not, and for composed credit, since the walk tries a rule only at its candidates. So a unit this rejects appears on none of the rule's report lines: filled, already verdicted, held, or composed. `--targeted` relies on this to evaluate only the admitted units and still print the rule's lines as the whole-domain run would. test_a_rules_lines_never_name_a_unit_outside_its_name_grain_candidates in rebuild/test_standing_verdicts.py checks it for every checked-in rule over the frozen mini bundle."""
    if not unit.get("before") or not unit.get("after"):
        return False
    shape = _shape_of(match)
    if shape is None:
        return False
    if shape is SHAPES["extension-dropped"]:
        return bool(_extension_positions(match, unit))
    if shape.composable:
        return bool(_candidates(match, unit))
    if shape is SHAPES["ligature"]:
        return any(_is_pivot(name, match["before"]["pivot"]) for name in unit["before"]["glyphs"])
    deltas = unit.get("ink_deltas")
    return (
        isinstance(deltas, dict)
        and bool(deltas)
        and set(deltas.values()) <= set(match["after"]["ink_deltas"])
    )


def _pieces_by_glyph(names, run):
    """Map each glyph position of a shaped run to its ink piece, consuming the run's pieces in order as the names match. An inkless glyph (a space, a ZWNJ, an empty marker) has no piece and is left out, so it is never an event. Return None when a piece is left unconsumed, which callers treat as no match."""
    pieces = {}
    index = 0
    for position, name in enumerate(names):
        if index < len(run) and run[index][0] == name:
            pieces[position] = run[index]
            index += 1
    return pieces if index == len(run) else None


def _slide_event(match, rule_id, index, after_names, before_pieces, after_pieces):
    """Return a slide Event at `index` when the after glyph carries an after pivot prefix and the pivot keeps its picture and vertical placement while its own-frame origin moves by the declared slide, or None, which leaves the piece to be judged as span ink. The origin check ties the change to the pivot's sidebearing, so an unrelated shift that happens to produce the same pixels does not count. This is `_slide_geometry`'s pivot check for one position."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    slide = match["after"]["slide"]
    if before[1] != after[1] or before[3] != after[3]:
        return None
    if after[4] != before[4] + slide * PIXEL_SIZE:
        return None
    return Event(rule_id, "slide", slide)


def _gain_event(match, rule_id, index, after_names, intern, before_pieces, after_pieces):
    """Return a gain Event at `index` when the after glyph carries an after pivot prefix and `_gain_holds` passes, or None, which leaves the piece to be judged as span ink. The pivot keeps its own-frame origin, and its after picture is its before picture plus exactly the named cells; the after frame may extend vertically. The walk checks the pivot's placement against the running displacement."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    if not _gain_holds(match, before, after, intern):
        return None
    return Event(rule_id, "gain", match["after"]["shift"])


def _entry_event(match, rule_id, index, after_names, intern, before_pieces, after_pieces, seam=None):
    """Return an entry Event at `index` when the after glyph is a named after pivot and `_entry_drop_holds` passes, or None, which leaves the piece to be judged as span ink. For an entry-contracted rule the pivot must keep its family, and the after glyph is matched by `_named_contracted_pivot` against the after seam into it, `seam`. The event's `lead` is the pivot's placement offset that `_entry_drop_holds` returns, and its shift is `_entry_shift`. The walk checks the placement against the running displacement."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if "entry_contraction" in match["after"]:
        if _family(before[0]) != _family(after[0]) or not _named_contracted_pivot(
            after_names[index], match["after"]["pivots"], seam
        ):
            return None
    elif not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    lead = _entry_drop_holds(match, before, after, intern)
    if lead is None:
        return None
    return Event(rule_id, "entry", _entry_shift(match, before[0], after[0]), lead=lead)


def _stub_event(match, rule_id, index, after_names, intern, before_pieces, after_pieces):
    """Return a stub Event at `index` when the after glyph carries an after pivot prefix and its picture is the before picture compacted left by the declared columns (`_entry_drop_holds` read as an entry drop of that count, returning a zero placement offset), or None, which leaves the piece to be judged as span ink. The walk checks that the pivot's placement moved right by that count."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    columns = match["after"]["stub_drop"]
    if _entry_drop_holds({"after": {"entry_drop": columns}}, before, after, intern) != 0:
        return None
    return Event(rule_id, "stub", columns)


def _redrawn_event(match, rule_id, index, after_names, intern, before_pieces, after_pieces):
    """Return a redrawn Event at `index` when the after glyph carries an after pivot prefix and `_redrawn_holds` passes, or None, which leaves the piece to be judged as span ink. The event's `room` is the contraction room its own frame did not take; the walk decides how much of it the pivot's placement uses (`_pull`)."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    frame = _redrawn_holds(match, before, after, intern)
    if frame is None:
        return None
    room = _contraction_room(before, after) - frame
    return Event(rule_id, "redrawn", match["after"]["shift"], room=room)


def _extension_event(match, rule_id, index, intern, before_pieces, after_pieces, cell):
    """Return an extension Event at `index` when the pivot drops exactly the named exit tail, or None, which leaves the pivot and follower to be judged as span ink. The pivot keeps its vertical placement and own-frame origin, sits on the pixel grid, and paints its before picture minus a tail. Every dropped cell lies right of the after picture's rightmost column, on the row the `seam_out` height names, and the tail is as wide as `_drop_columns` says: the named extension less any shorter one the after cell keeps, or the named contraction in full. The pivot needs its own grid check because no span ever includes it, and `_span_cells` checks every other piece. The seam row is the height divided by the pixel size, which is only correct on the grid. The follower must have ink on both sides so the walk can skip or chain it, but its picture is not checked here, because the rule names its after cell and a redraw inside that cell (·May losing the stacked entry, ·I's smaller loop) is part of what the rule approves."""
    seam = SEAM_ROW.fullmatch(match["before"]["seam_out"])
    if seam is None:
        return None
    row = int(seam.group(1))
    columns = _drop_columns(match["before"]["exit_extension"], cell)
    before, after = before_pieces.get(index), after_pieces.get(index)
    follower_before, follower_after = before_pieces.get(index + 1), after_pieces.get(index + 1)
    if before is None or after is None or follower_before is None or follower_after is None:
        return None
    if before[3] != after[3] or before[4] != after[4]:
        return None
    if before[2] % PIXEL_SIZE or after[2] % PIXEL_SIZE or before[3] % PIXEL_SIZE:
        return None
    painted, kept = intern.cells(before[1]), intern.cells(after[1])
    if painted is None or kept is None or not kept or not kept < painted:
        return None
    dropped = painted - kept
    edge = max(column for column, _row in kept)
    if max(column for column, _row in painted) - edge != columns:
        return None
    if any(column <= edge for column, _row in dropped):
        return None
    if any(before[3] // PIXEL_SIZE + cell_row != row for _column, cell_row in dropped):
        return None
    return Event(rule_id, "extension", -columns)


def _join_event(match, rule_id, index, before_pieces, after_pieces):
    """Return a join Event at `index` when the pivot passes `_join_pivot_holds` and the follower has ink on both sides, or None, which leaves both pieces to be judged as span ink. When the rule declares `follower_give_back`, the follower must also pass `_join_follower_holds`, and the walk treats the follower as part of this event instead of the head of the next span. Without a give-back, a follower that is itself an event is judged by that event, and any other follower heads the next span, so a redrawn follower still fails there."""
    if not _join_pivot_holds(match, before_pieces.get(index), after_pieces.get(index)):
        return None
    if before_pieces.get(index + 1) is None or after_pieces.get(index + 1) is None:
        return None
    give_back = match["after"].get("follower_give_back", 0)
    if give_back and not _join_follower_holds(match, before_pieces[index + 1], after_pieces[index + 1]):
        return None
    return Event(rule_id, "join", match["after"]["gap"], reach=give_back)


def _retarget_event(match, rule_id, index, before_pieces, after_pieces):
    """Return a retarget Event at `index` when the pivot has ink on both sides and the follower keeps its own-frame origin on the grid (`_retarget_piece_holds`), or None, which leaves both pieces to be judged as span ink. Height and picture may change. `pivot_judged` is False when the pivot moved its own-frame origin; the walk keeps such an event only behind a created join at the previous position, whose follower reach has already checked that move, or behind an entry, gain, or redrawn event at the same position, so a letter that redrew its own left edge never passes through a retarget alone. `shift` is how far the follower moves and `advance` is the rest of the declared shift, applied past the follower. Both are measured with the pivot standing, so they already include the pivot's own advance: a created join in front passes on only its follower reach (`_handed_on`), and a retarget in front passes on nothing."""
    if before_pieces.get(index) is None or after_pieces.get(index) is None:
        return None
    if not _retarget_piece_holds(before_pieces.get(index + 1), after_pieces.get(index + 1)):
        return None
    follower_shift = match["after"]["follower_shift"]
    return Event(
        rule_id,
        "retarget",
        follower_shift,
        _retarget_piece_holds(before_pieces[index], after_pieces[index]),
        advance=match["after"]["shift"] - follower_shift,
    )


def _created_join_event(match, rule_id, index, before_pieces, after_pieces):
    """Return a created-join (`joined`) Event at `index` when the follower keeps its own-frame origin or moves it left by `follower_reach`, on the grid, and the pivot has ink on both sides, or None, which leaves both pieces to be judged as span ink. Height and picture may change. `pivot_judged` is False when the pivot moved its own-frame origin; the walk keeps such an event only when it chains behind an entry, gain, or redrawn event at the same position, which has judged the pivot. In the walk the pivot sits at the running displacement or up to `pivot_stub_drop` columns right of it, the follower moves by the shift plus that offset, and the follower's advance delta is carried past it."""
    if not _retarget_piece_holds(
        before_pieces.get(index + 1), after_pieces.get(index + 1), match["after"]["follower_reach"]
    ):
        return None
    if before_pieces.get(index) is None or after_pieces.get(index) is None:
        return None
    pivot_judged = _retarget_piece_holds(before_pieces[index], after_pieces[index])
    return Event(
        rule_id,
        "joined",
        match["after"]["shift"],
        pivot_judged,
        advance=match["after"]["follower_advance"],
        room=match["after"].get("pivot_stub_drop", 0),
        reach=match["after"]["follower_reach"],
    )


def _span_settled(intern, before_span, after_span, displacement, after_anchor=None):
    """Whether one span between events renders as the same picture once displaced: the union of the before pieces' cells, moved by the running displacement, must equal the union of the after pieces' cells. When `after_anchor`, an after piece the walk has already validated, is added to both unions, cells handed invisibly between it and the span do not count as a change. A span or anchor with a non-rectilinear outline or an off-grid placement returns False."""
    painted = _span_cells(intern, before_span)
    rendered = _span_cells(intern, after_span)
    anchored = set() if after_anchor is None else _span_cells(intern, [after_anchor])
    if painted is None or rendered is None or anchored is None:
        return False
    displaced = {(column + displacement, row) for column, row in painted}
    return displaced | anchored == rendered | anchored


def _span_compacted(intern, before_span, after_span, displacement, columns):
    """Whether one span is the displaced before picture compacted left by `columns`: ink comes off only within the leftmost `columns` columns, and the remaining cells move left by the same count. This is the stacked entry coming off an extension rule's named follower, which `_span_settled` cannot see because it is not a translation."""
    painted = _span_cells(intern, before_span)
    rendered = _span_cells(intern, after_span)
    if painted is None or rendered is None or not rendered:
        return False
    displaced = {(column + displacement, row) for column, row in painted}
    if not displaced:
        return False
    shifted = {(column + columns, row) for column, row in rendered}
    dropped = displaced - shifted
    if shifted - displaced or not dropped:
        return False
    edge = min(column for column, _row in displaced)
    return all(column < edge + columns for column, _row in dropped)


def _span_explained(
    intern, before_span, after_span, displacement, compact=0, skippable=False, after_anchor=None
):
    """Whether one span is accounted for: a translation under the running displacement (`_span_settled`, with `after_anchor` if given), or, without an anchor, that picture compacted left by `compact` columns, or, when `skippable`, a translation of the span without its first piece. The last two apply to an extension's named follower, which may drop a stacked entry or redraw inside its named cell."""
    if _span_settled(intern, before_span, after_span, displacement, after_anchor=after_anchor):
        return True
    if after_anchor is not None:
        return False
    if compact and _span_compacted(intern, before_span, after_span, displacement, compact):
        return True
    if skippable and before_span:
        return _span_settled(intern, before_span[1:], after_span[1:], displacement)
    return False


def _handed_on(event, follower_event):
    """Return the displacement a created join carries past its follower when that follower is the next event: only the follower's reach when the next event is a retarget, whose counts are measured with its pivot standing and so already include that letter's advance, and otherwise the whole declared advance."""
    return event.reach if follower_event.kind == "retarget" else event.advance


def _composed_walk(rules, unit, context):
    """Walk the window left to right under a running column displacement and return each credited rule's event positions, or None when the composable rules cannot account for every rendered pixel together. The module docstring describes each event kind's placement and the chains in which two events share a letter; this function implements them.

    The unit's ink-delta digest must be the same under every config, and the window is shaped under the first. Both shaped runs must match the index record letter for letter. Every candidate of every rule is tested against its event contract (`_slide_event` and the others). A candidate that fails is not an event, and its ink is judged as span ink, so a rule that fails at a position does not stop the other rules from explaining the window. Two judged events at one position, or a retarget or created-join event whose follower position is also an event, return None unless they form one of the chains the module docstring lists. Every span between events must pass `_span_explained` under the displacement at its start.

    There is no minimum event count here, so tests can compare a single-event walk with each single-shape matcher. `_composed` requires two events.
    """
    deltas = unit.get("ink_deltas")
    if not isinstance(deltas, dict) or not deltas:
        return None
    if len(set(deltas.values())) != 1 or set(deltas) != set(unit.get("configs") or []):
        return None
    if not _letter_for_letter(unit):
        return None
    try:
        text = "".join(chr(int(value, 16)) for value in unit["codepoints"].split(":"))
    except ValueError:
        return None
    comparator = context.comparator
    features = features_for(unit["configs"][0])
    before_names, before_run = comparator.named_run("before", text, features)
    if list(before_names) != unit["before"]["glyphs"]:
        return None
    after_names, after_run = comparator.named_run("after", text, features)
    cells = unit["after"]["cells"]
    if len(after_names) != len(cells):
        return None
    for name, cell in zip(after_names, cells):
        letter = name.startswith("qs")
        if letter != cell.startswith("qs") or (letter and _cell_rune(cell) != _family(name)):
            return None
    before_pieces = _pieces_by_glyph(before_names, before_run)
    after_pieces = _pieces_by_glyph(after_names, after_run)
    if before_pieces is None or after_pieces is None:
        return None
    intern = comparator.intern
    found: dict[int, list[Event]] = {}
    for rule in rules:
        match = rule["match"]
        for index in _candidates(match, unit):
            if _is_slide_match(match):
                event = _slide_event(match, rule["id"], index, after_names, before_pieces, after_pieces)
            elif _is_gain_match(match):
                event = _gain_event(
                    match, rule["id"], index, after_names, intern, before_pieces, after_pieces
                )
            elif _is_entry_match(match):
                event = _entry_event(
                    match, rule["id"], index, after_names, intern, before_pieces, after_pieces
                )
            elif _is_entry_contracted_match(match):
                event = _entry_event(
                    match,
                    rule["id"],
                    index,
                    after_names,
                    intern,
                    before_pieces,
                    after_pieces,
                    unit["after"]["seams"][index - 1],
                )
            elif _is_stub_match(match):
                event = _stub_event(
                    match, rule["id"], index, after_names, intern, before_pieces, after_pieces
                )
            elif _is_redrawn_match(match):
                event = _redrawn_event(
                    match, rule["id"], index, after_names, intern, before_pieces, after_pieces
                )
            elif _is_join_match(match):
                event = _join_event(match, rule["id"], index, before_pieces, after_pieces)
            elif _is_retarget_match(match):
                event = _retarget_event(match, rule["id"], index, before_pieces, after_pieces)
            elif _is_created_join_match(match):
                event = _created_join_event(match, rule["id"], index, before_pieces, after_pieces)
            else:
                event = _extension_event(
                    match,
                    rule["id"],
                    index,
                    intern,
                    before_pieces,
                    after_pieces,
                    cells[index],
                )
            if event is not None:
                found.setdefault(index, []).append(event)
    events: dict[int, Event] = {}
    chained: dict[int, Event] = {}
    reached: dict[int, Event] = {}
    for index, claims in found.items():
        judged = [claim for claim in claims if claim.pivot_judged]
        unjudged = [claim for claim in claims if not claim.pivot_judged]
        kinds = {claim.kind for claim in judged}
        if len(judged) == 2 and "joined" in kinds and kinds & {"gain", "redrawn"}:
            unjudged = [claim for claim in judged if claim.kind == "joined"] + unjudged
            judged = [claim for claim in judged if claim.kind != "joined"]
        if len(judged) > 1:
            return None
        if not judged:
            if len(unjudged) == 1:
                reached[index] = unjudged[0]
            continue
        events[index] = judged[0]
        if unjudged and judged[0].kind in ("entry", "gain", "redrawn"):
            if len(unjudged) > 1:
                return None
            chained[index] = unjudged[0]
    for index, event in reached.items():
        prior = events.get(index - 1)
        if event.kind == "retarget" and prior is not None and prior.kind == "joined":
            events[index] = event
    behind_retarget = {
        index + 1
        for index, event in events.items()
        if event.kind == "retarget"
        and index + 1 in events
        and events[index + 1].kind in ("joined", "redrawn", "retarget", "join")
    }
    behind_join = {
        index + 1
        for index, event in list(events.items()) + list(chained.items())
        if event.kind == "joined"
        and index + 1 in events
        and events[index + 1].kind in ("joined", "retarget", "extension", "gain", "redrawn")
    }
    if any(
        index + 1 in events and index + 1 not in behind_retarget and index + 1 not in behind_join
        for index, event in events.items()
        if event.kind in ("retarget", "joined") or index in chained
    ):
        return None
    credited: dict[str, list[int]] = {}
    before_span: list = []
    after_span: list = []
    displacement = 0
    carried = 0
    compact = 0
    skippable = False
    after_anchor = None
    glyphs = unit["before"]["glyphs"]
    index = 0
    while index < len(before_names):
        event = events.get(index)
        if event is None:
            if index in before_pieces:
                before_span.append(before_pieces[index])
            if index in after_pieces:
                after_span.append(after_pieces[index])
            index += 1
            continue
        if not _span_explained(
            intern,
            before_span,
            after_span,
            displacement,
            compact,
            skippable,
            after_anchor,
        ):
            return None
        compact = 0
        skippable = False
        after_anchor = None
        position = index
        if event.kind == "slide":
            before_span, after_span = [before_pieces[index]], [after_pieces[index]]
            displacement += event.shift
            index += 1
        elif event.kind in ("gain", "entry", "redrawn"):
            pull = _pull(before_pieces[index], after_pieces[index], displacement + event.lead, event.room)
            if pull is None:
                return None
            displacement += carried + event.shift + pull
            carried = 0
            before_span, after_span = [], []
            if event.kind == "entry":
                after_anchor = after_pieces[index]
            index += 1
            joined = chained.get(position)
            if joined is not None:
                displacement += joined.shift
                if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                    return None
                after_anchor = None
                credited.setdefault(joined.rule_id, []).append(position)
                if index in behind_join:
                    carried = _handed_on(joined, events[index])
                else:
                    displacement += joined.advance
                    index += 1
        elif event.kind == "stub":
            if after_pieces[index][2] != before_pieces[index][2] + (displacement + event.shift) * PIXEL_SIZE:
                return None
            before_span, after_span = [], []
            index += 1
        elif event.kind == "retarget":
            if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                return None
            displacement += carried + event.shift
            carried = 0
            if after_pieces[index + 1][2] != before_pieces[index + 1][2] + displacement * PIXEL_SIZE:
                return None
            before_span, after_span = [], []
            trailing = events.get(position + 1) if position + 1 in behind_retarget else None
            if trailing is not None and trailing.kind in ("retarget", "join"):
                index += 1
            else:
                displacement += event.advance
                index += 2
                if trailing is not None:
                    displacement += trailing.shift
                    credited.setdefault(trailing.rule_id, []).append(position + 1)
                    if trailing.kind == "joined":
                        if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                            return None
                        if index in behind_join:
                            carried = _handed_on(trailing, events[index])
                        else:
                            displacement += trailing.advance
                            index += 1
        elif event.kind == "joined":
            push = _push(before_pieces[index], after_pieces[index], displacement, event.room)
            if push is None:
                return None
            displacement += carried + event.shift + push
            carried = 0
            if after_pieces[index + 1][2] != before_pieces[index + 1][2] + displacement * PIXEL_SIZE:
                return None
            before_span, after_span = [], []
            if index + 1 in behind_join:
                carried = _handed_on(event, events[index + 1])
                index += 1
            else:
                displacement += event.advance
                index += 2
        else:
            if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                return None
            displacement += carried + event.shift
            carried = 0
            follower_index = index + 1
            if follower_index in events:
                before_span, after_span = [], []
                index += 1
            else:
                follower_before, follower_after = (
                    before_pieces[follower_index],
                    after_pieces[follower_index],
                )
                if follower_after[2] != follower_before[2] + displacement * PIXEL_SIZE:
                    return None
                before_span, after_span = ([], []) if event.reach else ([follower_before], [follower_after])
                if event.kind == "extension":
                    compact = _dropped_entry(glyphs[follower_index], cells[follower_index])
                    skippable = True
                index += 2
        credited.setdefault(event.rule_id, []).append(position)
    if not _span_explained(
        intern,
        before_span,
        after_span,
        displacement,
        compact,
        skippable,
        after_anchor,
    ):
        return None
    return credited


def _composed(rules, unit, context, digest=None, counts=None):
    """Return the composed reading a fill may use: each credited rule's event positions, before any guard is read, or None. It returns None without shaping when the rules have fewer than two candidate positions in total, and None when the walk credits fewer than two events. A single event belongs on that rule's own line, while one rule credited at two positions counts as a composition. The walk is memoized per rules digest and unit id in `context.composed`. A caller that already has `digest` (`_composable_digest(rules)`) or `counts` (`_candidate_counts(rules, unit)`) passes them, as `Decider.evaluate` does; otherwise they are computed here."""
    if not unit.get("before") or not unit.get("after"):
        return None
    if counts is None:
        counts = _candidate_counts(rules, unit)
    if sum(counts.values()) < 2:
        return None
    key = (_composable_digest(rules) if digest is None else digest, unit["id"])
    if key not in context.composed:
        context.composed[key] = _composed_walk(rules, unit, context)
    events = context.composed[key]
    return events if events is not None and sum(len(at) for at in events.values()) > 1 else None


def _composed_held(rules, unit, events, context):
    """Whether an except_left guard holds this composed window. A credited rule's guard is read in its shape's `guard_scope`: across the whole window for `window`, or at the left neighbor of each credited position for `left-neighbor`. A non-composable rule holds the window when its own matcher accepts it unguarded and rejects it guarded, since it would have held the window in the single-rule pass. A hold applies to the whole unit, not only to one rule's credit."""
    glyphs = unit["before"]["glyphs"]
    for rule in rules:
        match = rule["match"]
        if _guard_is_inert(match):
            continue
        excluded = set(match["except_left"])
        indices = events.get(rule["id"])
        if indices:
            shape = _shape_of(match)
            if shape is not None and shape.guard_scope == "window":
                if any(_joining_family(name) in excluded for name in glyphs):
                    return True
            elif any(index and _joining_family(glyphs[index - 1]) in excluded for index in indices):
                return True
        elif not _is_composable(rule):
            if _matches(match, unit, guard=False, context=context) and not _matches(
                match, unit, context=context
            ):
                return True
    return False


def _composed_verdict(rules, unit, events, context):
    """Return the composed fill's verdict and the id of the non-credited rule that weakened it, or None for the id. The verdict is `either` when any credited rule's verdict is `either`, or when a non-composable `either` rule's own matcher also accepts the window; otherwise it is `approve`. `_composed_note` writes the fill's note from the live rules."""
    credited = [rule for rule in rules if rule["id"] in events]
    verdict = "either" if any(rule["verdict"] == "either" for rule in credited) else "approve"
    weakened = None
    if verdict == "approve":
        for rule in rules:
            if _is_composable(rule) or rule["verdict"] != "either":
                continue
            if _matches(rule["match"], unit, context=context):
                verdict, weakened = "either", rule["id"]
                break
    return verdict, weakened


class SlideContext:
    """The font-backed state for the shapes that re-shape windows and for the composed walk. `comparator` is an InkComparator over the surface's before and after fonts, and `fonts` keeps that pair so `_prefill` can build each pool worker's context over the same fonts (`_standing_pool_init`). `memo` caches each font-backed matcher's geometric result per shape, rule parameters, and unit, so the guarded and unguarded passes over one rule shape a window once. `composed` caches each composed walk per rules digest and unit. Every key names one unit, so `Decider._release` empties both after each unit it decides or serves, and a pool worker empties them after each chunk (`_standing_pool_chunk`), which keeps a worker's peak memory to one chunk's windows."""

    def __init__(self, before_font, after_font) -> None:
        self.fonts = (before_font, after_font)
        self.comparator = InkComparator(before_font, after_font)
        self.memo: dict[tuple, bool] = {}
        self.composed: dict[tuple, dict[str, list[int]] | None] = {}


class Shape(NamedTuple):
    """One row of SHAPES: a delta shape a rule can declare. `keyed_by` is the `match.after` field that declares it. `before` and `after` are the fields `match.before` and `match.after` must carry, and an empty `before` means that block must be absent; `optional` and `before_optional` are fields they may also carry. `cell_lists`, `digest_lists`, `name_lists`, `int_fields`, `family_fields`, and `point_lists` tell `load_rules` how to check a field's type: a list of cell strings, a list of ink-delta digests, a list of glyph-name prefixes, an integer column count, a family name or list of them, or a list of [column, row] cells. Any other field must be a nonempty string. `matcher` reads a unit and `validate` checks the rule at load. `composable` says whether a composed walk may credit the shape, `font_backed` whether its matcher re-shapes windows in the surface's fonts, and `needs_ink_deltas` whether it reads the units' persisted ink deltas. `guard_scope` is where a composed reading reads the rule's except_left guard: `window`, `left-neighbor`, or None for a shape that never composes."""

    keyed_by: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    cell_lists: tuple[str, ...]
    matcher: Callable[[dict, dict, set[str], "SlideContext | None"], bool]
    validate: Callable[[str, dict], None] | None = None
    optional: tuple[str, ...] = ()
    before_optional: tuple[str, ...] = ()
    digest_lists: tuple[str, ...] = ()
    name_lists: tuple[str, ...] = ()
    int_fields: tuple[str, ...] = ()
    family_fields: tuple[str, ...] = ()
    point_lists: tuple[str, ...] = ()
    composable: bool = False
    font_backed: bool = False
    needs_ink_deltas: bool = False
    guard_scope: str | None = None


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
        family_fields=("follower",),
        composable=True,
        guard_scope="left-neighbor",
    ),
    "ink-delta": Shape(
        keyed_by="ink_deltas",
        before=(),
        after=("ink_deltas",),
        cell_lists=(),
        matcher=_matches_ink_delta,
        validate=_validate_ink_delta,
        digest_lists=("ink_deltas",),
        needs_ink_deltas=True,
    ),
    "slide": Shape(
        keyed_by="slide",
        before=("pivots",),
        after=("pivots", "slide"),
        cell_lists=(),
        matcher=_matches_slide,
        validate=_validate_slide,
        name_lists=("pivots",),
        int_fields=("slide",),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "ink-gain": Shape(
        keyed_by="gained",
        before=("pivots",),
        after=("pivots", "gained", "shift"),
        cell_lists=(),
        matcher=_matches_ink_gain,
        validate=_validate_ink_gain,
        name_lists=("pivots",),
        int_fields=("shift",),
        point_lists=("gained",),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "join-dropped": Shape(
        keyed_by="gap",
        before=("pivot", "seam_out", "follower"),
        after=("gap",),
        cell_lists=("pivot_cells", "receiver_cells"),
        matcher=_matches_join_dropped,
        validate=_validate_join_dropped,
        optional=("pivot_cells", "receiver_cells", "follower_give_back"),
        int_fields=("gap", "follower_give_back"),
        family_fields=("follower",),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "entry-extension-dropped": Shape(
        keyed_by="entry_drop",
        before=("pivots",),
        after=("pivots", "entry_drop"),
        cell_lists=(),
        matcher=_matches_entry_drop,
        validate=_validate_entry_drop,
        name_lists=("pivots",),
        int_fields=("entry_drop",),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "entry-contracted": Shape(
        keyed_by="entry_contraction",
        before=("left", "pivots"),
        after=("pivots", "entry_contraction"),
        cell_lists=(),
        matcher=_matches_entry_contracted,
        validate=_validate_entry_contracted,
        before_optional=("except_pivots",),
        name_lists=("pivots", "except_pivots"),
        int_fields=("entry_contraction",),
        family_fields=("left",),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "stub-dropped": Shape(
        keyed_by="stub_drop",
        before=("pivots",),
        after=("pivots", "stub_drop"),
        cell_lists=(),
        matcher=_matches_stub_drop,
        validate=_validate_stub_drop,
        name_lists=("pivots",),
        int_fields=("stub_drop",),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "redrawn": Shape(
        keyed_by="dropped",
        before=("pivots",),
        after=("pivots", "dropped", "added", "shift"),
        cell_lists=(),
        matcher=_matches_redrawn,
        validate=_validate_redrawn,
        name_lists=("pivots",),
        int_fields=("shift",),
        point_lists=("dropped", "added"),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "join-retargeted": Shape(
        keyed_by="retarget",
        before=("pivot", "seam_out", "follower"),
        after=("retarget", "pivot_cells", "receiver_cells", "shift", "follower_shift"),
        cell_lists=("pivot_cells", "receiver_cells"),
        matcher=_matches_join_retarget,
        validate=_validate_join_retarget,
        int_fields=("shift", "follower_shift"),
        family_fields=("follower",),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
    "join-created": Shape(
        keyed_by="joined",
        before=("pivot", "seam_out", "follower"),
        after=("joined", "pivot_cells", "receiver_cells", "shift", "follower_advance", "follower_reach"),
        cell_lists=("pivot_cells", "receiver_cells"),
        matcher=_matches_join_created,
        validate=_validate_join_created,
        optional=("pivot_stub_drop",),
        before_optional=("except_pivots",),
        name_lists=("except_pivots",),
        int_fields=("shift", "follower_advance", "follower_reach", "pivot_stub_drop"),
        family_fields=("pivot", "follower"),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
}


def _shape_names(chosen, conjunction):
    """Name the shapes whose SHAPES row satisfies `chosen`, in SHAPES order, as an English list ending in `conjunction`, so error messages stay in step with the rows."""
    names = [name for name, shape in SHAPES.items() if chosen(shape)]
    return f"{', '.join(names[:-1])}, {conjunction} {names[-1]}" if len(names) > 1 else "".join(names)


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
            optional = shape.optional if block == "after" else shape.before_optional
            if not fields:
                if block in match:
                    _fail(f"rule {rule_id!r}: the {declared[0]} shape carries no match.{block} block")
                continue
            got = match.get(block)
            if not isinstance(got, dict) or not set(fields) <= set(got) <= set(fields) | set(optional):
                _fail(
                    f"rule {rule_id!r}: the {declared[0]} shape needs match.{block} to be exactly "
                    f"{', '.join(fields)}" + (f", optionally with {', '.join(optional)}" if optional else "")
                )
            for field in got:
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
                elif field in shape.name_lists:
                    if (
                        not isinstance(value, list)
                        or not value
                        or not all(isinstance(item, str) and item and "/" not in item for item in value)
                    ):
                        _fail(
                            f"rule {rule_id!r}: match.{block}.{field} must be a nonempty list of "
                            "glyph-name prefixes (a family or family.modifier name, never a "
                            "/-separated cell string)"
                        )
                elif field in shape.int_fields:
                    if not isinstance(value, int) or isinstance(value, bool):
                        _fail(f"rule {rule_id!r}: match.{block}.{field} must be an integer column count")
                elif field in shape.point_lists:
                    if (
                        not isinstance(value, list)
                        or not all(
                            isinstance(item, list)
                            and len(item) == 2
                            and all(isinstance(n, int) and not isinstance(n, bool) for n in item)
                            for item in value
                        )
                        or len({tuple(item) for item in value}) != len(value)
                    ):
                        _fail(
                            f"rule {rule_id!r}: match.{block}.{field} must be a list of distinct "
                            "[column, row] own-frame cells"
                        )
                elif field in shape.family_fields:
                    families = _families(value)
                    if (
                        not families
                        or not all(isinstance(item, str) and item for item in families)
                        or len(set(families)) != len(families)
                    ):
                        _fail(
                            f"rule {rule_id!r}: match.{block}.{field} must be a family name or a "
                            "nonempty list of distinct family names"
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


def _guard_is_inert(match):
    """Whether a rule's except_left guard is empty. `guard` affects `_matches` only through the `excluded` set, so for such a rule the guarded and unguarded passes return the same result, and callers skip the unguarded one. A shape that lets `guard` affect anything else must update this predicate."""
    return not match.get("except_left")


def _matches(match, unit, *, guard=True, context=None):
    before, after = unit.get("before"), unit.get("after")
    if not before or not after:
        return False
    excluded = set(match.get("except_left", [])) if guard else set()
    for shape in SHAPES.values():
        if shape.keyed_by in match["after"]:
            return shape.matcher(match, unit, excluded, context)
    return False


class Reach(NamedTuple):
    """One rule's reach on a run: the unit ids its own matcher accepted, split into the blanks it filled and the units that already had a verdict; the ids its except_left held back; and its composed credit, counted separately as the number of units composed lines credited it at and the number of those composed lines. The composed pass claims a window before any single rule is checked, so a rule that is only ever credited in compositions shows nothing on its own line and is still in use. Under `--open-only`, `verdicted` holds only matched units whose verdict is outside ACCEPTING_VERDICTS, which are the units the tripwire names."""

    filled: list[str]
    verdicted: list[str]
    held: list[str]
    composed_credit: int
    composed_lines: int


class Run(NamedTuple):
    """One pass of the standing approvals over a surface: the fill records to write, the composed pass's [filled, already verdicted, held] counts per credited-id tuple in rules-file order, and each rule's Reach by id."""

    fills: list[dict]
    composed_counts: dict[tuple[str, ...], list[int]]
    reaches: dict[str, Reach]


class Composed(NamedTuple):
    """The composed reading's decision for one window: the credited rule ids in rules-file order, whether a guard holds the whole unit, and, for a window no guard holds, the verdict a fill carries and the id of the non-composable `either` rule that weakened it (None when none did). A held window's verdict is not computed, because nothing writes it. The decision holds no note text: `_composed_note` reads the notes from the live rules when the fill is written, so a reworded note does not invalidate a stored decision."""

    credited: tuple[str, ...]
    held: bool
    verdict: str | None
    weakened: str | None = None


def _composed_note(by_id, composed: Composed) -> str:
    """The note a composed fill carries, read from the live rules like a single-rule fill's: the credited ids and their notes in rules-file order, plus the rule outside the credited set that weakened the verdict, if one did."""
    ids = " + ".join(composed.credited)
    note = f"[standing: {ids}] " + "; ".join(by_id[rule_id]["note"] for rule_id in composed.credited)
    return note + (f" (either: {composed.weakened})" if composed.weakened else "")


class Decision(NamedTuple):
    """Everything a run needs about one unit apart from the verdict store: the composed reading when one claims the window, else the rules whose own matchers accept it and the rules whose except_left holds it. A claimed window has no per-rule results, because the single-rule pass never sees it. `relevant` is not part of the verdict; it is part of the memo entry's key: the composable rules with a candidate position in this window (`_candidate_counts`), in rules-file order. Only these rules add a term to the composed pre-gate or an event to `_composed_walk`, so a change to any other rule cannot change the composed part. It is empty when the composed reading is off (`Decider.gate`)."""

    composed: Composed | None
    matched: frozenset[str]
    held: frozenset[str]
    relevant: tuple[str, ...] = ()


def unit_key(unit, family_digests) -> str | None:
    """The memo key of one unit, or None for a unit the build never stamped, which is evaluated every pass and never stored. The build's `content_key` covers the window, its configs, both sides' glyphs, cells, and seams, and the judged `pair`. `ink_deltas` is hashed beside it because the stamp excludes it while the ink-delta shape reads it. The after font's compiled-glyph digest for every family the after cells name covers the outlines, advances, and cursive anchors every font-backed shape uses to shape the window, so a drawing change re-evaluates only the windows that use the changed family. The before font is covered by the memo's stamp (`memo_environment`). The key is truncated to 32 hex characters (128 bits), far from any collision at one key per human unit."""
    stamp = unit.get("content_key")
    if not stamp:
        return None
    runes = sorted({_cell_rune(cell) for cell in (unit.get("after") or {}).get("cells") or ()})
    lines = [stamp, json.dumps(unit.get("ink_deltas"), sort_keys=True)]
    lines += [f"{rune}\t{family_digests.get(rune, '-')}" for rune in runes]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:32]


def memo_code_paths(root=ROOT) -> list[pathlib.Path]:
    return [pathlib.Path(root) / relative for relative in MEMO_CODE_MODULES]


def memo_environment(surface, root=ROOT) -> tuple[str, dict[str, str]]:
    """Return the memo's stamp and the after font's per-family digests that `unit_key` uses. The stamp hashes the memo format; the deciding code (`memo_code_paths`); the before font without its `head` and `name` tables (`fingerprint.font_content_digest`, so the `make all` of a version bump leaves it unchanged while any outline, advance, anchor, or layout change moves it); the after font's family-independent remainder (helper glyphs, cmap, and GPOS wiring); and `uv.lock`'s dependency pins (`fingerprint.lock_digest`), which fix the HarfBuzz version the shaper uses. A change to any of these drops the whole memo. The rules file is not in the stamp. The `Roster` in the header and each entry's `relevant` ids track it per entry, so a rules commit drops only the entries the changed rules can reach, and a reworded note drops none. A surface without fonts gets `-` for both font lines; no rule can shape a window on such a surface."""
    before_font = pathlib.Path(surface) / "fonts" / "before.otf"
    after_font = pathlib.Path(surface) / "fonts" / "after.otf"
    family_digests: dict[str, str] = {}
    helpers = "-"
    if after_font.is_file():
        family_digests, helpers = fingerprint.after_font_glyph_digests(after_font)
    lines = [
        f"format\t{MEMO_FORMAT}",
        f"code\t{fingerprint.hash_paths(root, memo_code_paths(root))}",
        f"before_font\t{fingerprint.font_content_digest(before_font) if before_font.is_file() else '-'}",
        f"after_helpers\t{helpers}",
        f"lock\t{fingerprint.lock_digest(pathlib.Path(root) / 'uv.lock')}",
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), family_digests


class Roster(NamedTuple):
    """What the memo header records about the rules file, and what a `Decider` compares the live file against. `rules` maps each id, in rules-file order, to the rule's match digest and verdict. `always` lists, in the same order, the non-composable rules the composed reading consults for every window it claims, because they have a non-empty except_left (`_composed_held`) or an `either` verdict (`_composed_verdict`). `composed_gate` says whether the composed reading is on (`Decider.gate`). Together with an entry's `relevant` ids, this is everything a stored decision depends on besides its unit key, and it contains no note text."""

    rules: dict[str, tuple[str, str]]
    always: tuple[str, ...]
    composed_gate: bool


def _match_digest(match) -> str:
    return hashlib.sha256(json.dumps(match, sort_keys=True).encode()).hexdigest()[:16]


def rules_roster(rules, composed_gate) -> Roster:
    """The roster of a loaded rules file. Each match digest ignores key order and includes `except_left`, which `load_rules` keeps inside `match`. It is not `fingerprint.standing_approvals_digest`, which is one digest over the whole file in the file's own key order."""
    return Roster(
        {rule["id"]: (_match_digest(rule["match"]), rule["verdict"]) for rule in rules},
        tuple(
            rule["id"]
            for rule in rules
            if not _is_composable(rule)
            and (not _guard_is_inert(rule["match"]) or rule["verdict"] == "either")
        ),
        composed_gate,
    )


def _roster_from_header(header) -> Roster | None:
    """The roster a memo header carries, or None for a missing or malformed one, which `Decider` treats as every rule having changed."""
    rules, always, gate = header.get("rules"), header.get("always"), header.get("composed_gate")
    if not isinstance(rules, dict) or not isinstance(always, list) or not isinstance(gate, bool):
        return None
    if not all(
        isinstance(entry, list) and len(entry) == 2 and all(isinstance(part, str) for part in entry)
        for entry in rules.values()
    ) or not all(isinstance(rule_id, str) for rule_id in always):
        return None
    return Roster({rule_id: (entry[0], entry[1]) for rule_id, entry in rules.items()}, tuple(always), gate)


def _decision_record(decision: Decision) -> list:
    composed = decision.composed
    return [
        (
            None
            if composed is None
            else [list(composed.credited), composed.held, composed.verdict, composed.weakened]
        ),
        sorted(decision.matched),
        sorted(decision.held),
        list(decision.relevant),
    ]


def _decision_from_record(record: list) -> Decision:
    """Rebuild a decision from its memo record, interning the rule ids: the memo holds one entry per human unit, the entries repeat a small set of rule ids, and `json` gives every line its own copies."""
    composed, matched, held, relevant = record
    return Decision(
        (
            None
            if composed is None
            else Composed(tuple(map(sys.intern, composed[0])), composed[1], composed[2], composed[3])
        ),
        frozenset(map(sys.intern, matched)),
        frozenset(map(sys.intern, held)),
        tuple(map(sys.intern, relevant)),
    )


class Memo:
    """The persisted decisions: a header holding the stamp from `memo_environment` and the `Roster` of the rules file the decisions were made under, then one line per unit key.

    A memo with another stamp, or one that cannot be read, opens empty; a miss only costs the evaluation the memo would have saved. A header without a readable roster means every rule has changed, and `Decider` serves nothing from it.

    `write` keeps only the entries whose key belongs to a unit on the surface it was given, whether served, freshly computed, or carried unread from the file, so the file stays bounded by the human domain. It writes the roster the run used into the header. An unread entry is carried only when that roster equals the stored one. A narrowed run never checks a closed unit's entry against changed rules, and writing that entry under the live roster would let the next pass serve it as if it had been checked, so a run under a changed roster keeps only what it computed or served. The gzip mtime is pinned and the level is 1, as in the unit store, because the file is written once and read once per pass.
    """

    def __init__(self, path, environment, family_digests, entries=None, stored: Roster | None = None) -> None:
        self.path = pathlib.Path(path)
        self.environment = environment
        self.family_digests = family_digests
        self.entries: dict[str, Decision] = entries or {}
        self.stored = stored
        self.fresh: dict[str, Decision] = {}
        self.served: set[str] = set()
        self._keys: dict[str, str | None] = {}

    @classmethod
    def open(cls, path, environment, family_digests, *, fresh=False) -> "Memo":
        """The memo on disk, empty when there is nothing usable there or the caller asked to recompute everything (`--fresh-memo`, which still rewrites the file afterward)."""
        entries: dict[str, Decision] = {}
        stored = None
        path = pathlib.Path(path)
        if not fresh and path.is_file():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    header = json.loads(next(stream))
                    if header.get("format") == MEMO_FORMAT and header.get("environment") == environment:
                        stored = _roster_from_header(header)
                        for line in stream:
                            key, record = json.loads(line)
                            entries[key] = _decision_from_record(record)
            except OSError, EOFError, ValueError, TypeError, StopIteration:
                entries, stored = {}, None
        return cls(path, environment, family_digests, entries, stored)

    def key_for(self, unit) -> str | None:
        unit_id = unit["id"]
        if unit_id not in self._keys:
            self._keys[unit_id] = unit_key(unit, self.family_digests)
        return self._keys[unit_id]

    def write(self, units, roster: Roster | None = None) -> int:
        """Write the memo back, bounded to `units` and headed by `roster`, and return how many entries it holds. An entry the run neither computed nor served is carried only when `roster` is the one it was stored under."""
        return self._write_keys((self.key_for(unit) for unit in units), roster)

    def write_primed(self, roster: Roster | None = None) -> int:
        """Write back the entries for every unit whose key `key_for` has computed; the CLI computes a key for every unit in the domain as it streams them."""
        return self._write_keys(self._keys.values(), roster)

    def _write_keys(self, keys, roster: Roster | None) -> int:
        carry = roster == self.stored
        kept: dict[str, Decision] = {}
        for key in keys:
            if key is None:
                continue
            decision = self.fresh.get(key)
            if decision is None and (carry or key in self.served):
                decision = self.entries.get(key)
            if decision is not None:
                kept[key] = decision
        header = {
            "format": MEMO_FORMAT,
            "environment": self.environment,
            "rules": (
                None if roster is None else {rule_id: list(entry) for rule_id, entry in roster.rules.items()}
            ),
            "always": None if roster is None else list(roster.always),
            "composed_gate": None if roster is None else roster.composed_gate,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as handle:
            with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
                stream.write((json.dumps(header) + "\n").encode())
                for key in sorted(kept):
                    stream.write((json.dumps([key, _decision_record(kept[key])]) + "\n").encode())
        return len(kept)


class Decider:
    """The per-unit decision function behind a run. Each unit is decided once per run however many times it is asked about, so the narrowed pass and the `--require-reach` pass over the whole domain share results. A decision is served from the memo when the memo holds an entry under the unit's key that the live rules still support (`_serve`). `served`, `computed` and `unkeyed` count how each decision was reached.

    The comparison of the live rules with the memo's stored `Roster` is computed once here, so a rules commit re-checks only the rules that changed instead of the whole domain. `_stale` holds every stored id whose match digest or verdict changed or that the live file no longer has. `_dropped` is the subset that vanished or whose match changed, since a verdict-only change leaves the rule's matcher results valid. `_probe` holds the live rules that are new or whose match changed, the only rules any per-unit candidate or reach check runs this pass. `_always_moved` says whether the rules the composed reading consults for every window changed at all, and `_gate_moved` whether the composed reading was switched on or off. A memo with no stored roster counts every rule as changed.
    """

    def __init__(self, rules, context, memo: Memo | None = None) -> None:
        self.rules = rules
        self.context = context
        self.memo = memo
        self.composable = _composable(rules)
        self.composable_digest = _composable_digest(self.composable)
        self.gate = len(self.composable) > 1 and context is not None
        self.roster = rules_roster(rules, self.gate)
        self._order = {rule["id"]: index for index, rule in enumerate(rules)}
        stored = memo.stored if memo is not None else None
        live = self.roster.rules
        if stored is None:
            self._gate_moved = self._always_moved = True
            self._stale = self._dropped = frozenset(live)
            self._probe = list(rules)
        else:
            self._gate_moved = stored.composed_gate != self.gate
            self._always_moved = [(rule_id, stored.rules.get(rule_id)) for rule_id in stored.always] != [
                (rule_id, live[rule_id]) for rule_id in self.roster.always
            ]
            self._dropped = frozenset(
                rule_id
                for rule_id, (digest, _verdict) in stored.rules.items()
                if rule_id not in live or live[rule_id][0] != digest
            )
            self._stale = self._dropped | frozenset(
                rule_id
                for rule_id, entry in stored.rules.items()
                if rule_id in live and live[rule_id] != entry
            )
            self._probe = [
                rule
                for rule in rules
                if rule["id"] not in stored.rules or stored.rules[rule["id"]][0] != live[rule["id"]][0]
            ]
        self._decided: dict[str, Decision] = {}
        self._servings: dict[str, tuple[Decision, bool] | None] = {}
        self.served = 0
        self.computed = 0
        self.unkeyed = 0

    def evaluate(self, unit) -> Decision:
        """Compute the decision. The composed reading runs first, because it claims a window before any single rule is checked. For an unclaimed window each rule's own matcher runs, and a guarded rule that fails is run again unguarded to find whether its guard held the window. The candidate counts for the composed pre-gate are computed once and also give the entry's `relevant` rules."""
        composed = None
        relevant: tuple[str, ...] = ()
        if self.gate:
            counts = _candidate_counts(self.composable, unit)
            relevant = tuple(rule_id for rule_id, count in counts.items() if count)
            events = _composed(self.composable, unit, self.context, self.composable_digest, counts)
            if events is not None:
                credited = tuple(rule["id"] for rule in self.rules if rule["id"] in events)
                held = _composed_held(self.rules, unit, events, self.context)
                verdict, weakened = (
                    (None, None) if held else _composed_verdict(self.rules, unit, events, self.context)
                )
                composed = Composed(credited, held, verdict, weakened)
        matched: list[str] = []
        held_by: list[str] = []
        if composed is None:
            for rule in self.rules:
                match = rule["match"]
                if _matches(match, unit, context=self.context):
                    matched.append(rule["id"])
                elif not _guard_is_inert(match) and _matches(match, unit, guard=False, context=self.context):
                    held_by.append(rule["id"])
        return Decision(composed, frozenset(matched), frozenset(held_by), relevant)

    def _serve(self, unit, entry: Decision) -> tuple[Decision, bool] | None:
        """Return the decision a memo entry still stands for under the live rules, with whether a matcher ran to repair it, or None when only `evaluate` can decide. Every uncertain case returns None:

        - The composed gate changed, so no entry stands.
        - A claimed window whose `relevant` ids include a stale rule, whose always-consulted rules changed, or whose `relevant` ids are no longer in rules-file order. A rule that had a candidate here and then changed or vanished can change the pre-gate sum, the walk's ambiguity check, a credit, a guard, or the verdict, and the credited tuple is stored in file order.
        - An unclaimed window whose `relevant` ids include a dropped rule, for the same reasons apart from the guard and the verdict.
        - A probed composable rule has a candidate position here, so it would take part in the walk.

        Otherwise the entry is served. A claimed window is served unchanged. An unclaimed one is repaired per rule: the dropped rules are removed from its matched and held sets, and each probed rule that its shape's name-grain precondition admits (`_reachable`) is run guarded and, for a non-empty guard, unguarded. A composable rule with no candidate is admitted only when it is an extension rule whose seam the walk cannot place, checked through `_extension_positions` as `_reachable` does. The repair is correct only if `_reachable` admits every unit a rule's own matcher accepts or holds; test_a_rules_lines_never_name_a_unit_outside_its_name_grain_candidates in rebuild/test_standing_verdicts.py checks that over the frozen mini bundle. A window with no shaped side skips the probe check and is served unchanged, since no matcher accepts one.
        """
        if self._gate_moved:
            return None
        relevant = set(entry.relevant)
        composed = entry.composed
        if composed is not None:
            if self._always_moved or relevant & self._stale:
                return None
            if sorted(entry.relevant, key=self._order.__getitem__) != list(entry.relevant):
                return None
        elif relevant & self._dropped:
            return None
        if not unit.get("before") or not unit.get("after"):
            return entry, False
        repairs = []
        for rule in self._probe:
            match = rule["match"]
            if _is_composable(rule):
                if _candidates(match, unit):
                    return None
                if (
                    composed is None
                    and _shape_of(match) is SHAPES["extension-dropped"]
                    and _extension_positions(match, unit)
                ):
                    repairs.append(rule)
            elif composed is None and _reachable(match, unit):
                repairs.append(rule)
        if composed is not None:
            return entry, False
        matched = set(entry.matched) - self._dropped
        held = set(entry.held) - self._dropped
        for rule in repairs:
            match = rule["match"]
            if _matches(match, unit, context=self.context):
                matched.add(rule["id"])
            elif not _guard_is_inert(match) and _matches(match, unit, guard=False, context=self.context):
                held.add(rule["id"])
        return Decision(None, frozenset(matched), frozenset(held), entry.relevant), bool(repairs)

    def _release(self) -> None:
        """Empty the context's shape and walk memos. Every key in them is for one unit, and `_decided` and `_servings` answer repeat requests, so emptying them after each unit loses nothing. `decide` calls this after every unit it serves or computes, `_serving` after every memo entry it checks (which `_prefill` does outside `decide`), and a pooled worker after every chunk (`_standing_pool_chunk`). The alignment cache is separate: on the serial path it lasts the whole run, and `release_alignment_cache` says where it is emptied."""
        if self.context is not None:
            self.context.memo.clear()
            self.context.composed.clear()

    def _serving(self, unit) -> tuple[Decision, bool] | None:
        """Run `_serve` on the unit's memo entry once per unit, however often `_prefill` and `decide` ask. A served key is added to `Memo.served`, so `Memo.write` keeps it under a changed roster, and a repaired or trimmed decision replaces the stored entry, so the file never keeps a rule the live file dropped. The context's memos are released after `_serve` (`_release`), so a check made outside `decide` leaves no window in them."""
        unit_id = unit["id"]
        if unit_id not in self._servings:
            serving = None
            key = self.memo.key_for(unit) if self.memo is not None else None
            if self.memo is not None and key is not None:
                entry = self.memo.entries.get(key)
                if entry is not None:
                    try:
                        serving = self._serve(unit, entry)
                    finally:
                        self._release()
                    if serving is not None:
                        self.memo.served.add(key)
                        if serving[0] != entry:
                            self.memo.entries[key] = serving[0]
            self._servings[unit_id] = serving
        return self._servings[unit_id]

    def take(self, unit, decision: Decision) -> Decision:
        """Record a decision `decide` computed this run, by `evaluate` or by `_serve` repairing an entry: compute the unit's memo key, then record the decision as `take_id` does."""
        if self.memo is not None:
            self.memo.key_for(unit)
        return self.take_id(unit["id"], decision)

    def take_id(self, unit_id: str, decision: Decision) -> Decision:
        """Record a decision for a unit whose memo key is already computed: a keyed unit's goes into the memo's fresh entries and `computed`, an unkeyed unit's into `unkeyed`, and both into `_decided`. `_prefill` calls this for pooled results."""
        key = self.memo._keys[unit_id] if self.memo is not None else None
        if key is None:
            self.unkeyed += 1
        else:
            self.computed += 1
            assert self.memo is not None
            self.memo.fresh[key] = decision
        self._decided[unit_id] = decision
        return decision

    def decide(self, unit) -> Decision:
        """Return the unit's decision: from `_decided` on a repeat request, else served or computed. The context's memos are released after every unit (`_release`), so the context holds one unit's windows at a time."""
        decision = self._decided.get(unit["id"])
        if decision is not None:
            return decision
        try:
            serving = self._serving(unit)
            if serving is None:
                return self.take(unit, self.evaluate(unit))
            decision, repaired = serving
            if repaired:
                return self.take(unit, decision)
            self.served += 1
            self._decided[unit["id"]] = decision
            return decision
        finally:
            self._release()


# Below this many misses, starting a pool costs more than it saves. Startup (spawn, the module import, the rules, and two font loads) takes about 0.2 s per worker. A miss costs about 1.3 ms serially and about 11 us to pickle each way, which puts the break-even near five hundred misses at width four. A warm pass over unchanged rules computes tens of units and stays far below this; a pass that commits a rule reaching thousands of units goes above it. The plumbing rows in rebuild/out/cycle-timings.ndjson are where these rates were measured.
_STANDING_POOL_THRESHOLD = 2_000
# Units per pooled task: large enough that pickling and messaging are a small part of the work, and small enough that tasks balance across the workers and a worker's peak is one chunk's windows, since each worker releases its context memos and its alignment cache after every chunk. STANDING_FILL_WORKER_BYTES in rebuild/tools/artifact_cycle.py is measured at this chunk width.
_STANDING_POOL_CHUNK = 2_000

_standing_pool_state: dict = {}


def _standing_pool_init(rules, fonts) -> None:
    """Build one worker's state: a memo-less `Decider` over the parent's rules, with a `SlideContext` over the parent's font pair, or no context when the parent had none, in which case no rule shapes a window."""
    context = None if fonts is None else SlideContext(*fonts)
    _standing_pool_state["decider"] = Decider(rules, context)


def _standing_pool_chunk(units) -> list[tuple[str, list]]:
    """Decide one pooled chunk and return each unit's id with its `_decision_record`, the format the memo stores, so `_decision_from_record` reads both. The worker's memos are emptied after every chunk, so its peak holds one chunk's windows."""
    decider = _standing_pool_state["decider"]
    try:
        return [(unit["id"], _decision_record(decider.evaluate(unit))) for unit in units]
    finally:
        decider._release()
        release_alignment_cache()


def _prefill(decider: Decider, asked, jobs: int) -> None:
    """Decide every unit in `asked` in one pass over it. With `jobs` at 1 each unit is decided serially. Otherwise a unit that is already decided or that the memo can serve is decided immediately (an entry `_serve` repairs counts as servable, since the repair runs only a matcher or two), and each miss is written to a temporary gzipped spool. Fewer than `_STANDING_POOL_THRESHOLD` misses are decided serially from the spool. More go to a spawn pool, which is given at most one wave of `width` chunks at a time, and only ids and decisions are kept from its results. `_serving` and `decide` release the context's memos after each unit (`Decider._release`), so the spool pass holds no unit's windows after it."""
    if jobs <= 1:
        for unit in asked:
            decider.decide(unit)
        return
    with tempfile.TemporaryFile() as handle:
        missed: set[str] = set()
        with gzip.GzipFile(fileobj=handle, mode="wb", compresslevel=1, mtime=0) as stream:
            for unit in asked:
                if unit["id"] in decider._decided or decider._serving(unit) is not None:
                    decider.decide(unit)
                else:
                    missed.add(unit["id"])
                    stream.write((json.dumps(dict(unit)) + "\n").encode())
        handle.seek(0)
        with gzip.GzipFile(fileobj=handle, mode="rb") as stream:
            units = (json.loads(line) for line in stream)
            if len(missed) < _STANDING_POOL_THRESHOLD:
                for unit in units:
                    decider.decide(unit)
                return
            fonts = None if decider.context is None else decider.context.fonts
            width = min(jobs, (len(missed) + _STANDING_POOL_CHUNK - 1) // _STANDING_POOL_CHUNK)
            spawn = multiprocessing.get_context("spawn")
            with spawn.Pool(width, initializer=_standing_pool_init, initargs=(decider.rules, fonts)) as pool:
                for wave in batched(batched(units, _STANDING_POOL_CHUNK), width):
                    for records in pool.imap_unordered(_standing_pool_chunk, wave):
                        for unit_id, record in records:
                            missed.remove(unit_id)
                            decider.take_id(unit_id, _decision_from_record(record))
                    del wave
        assert not missed, f"the pool never answered for {len(missed)} units"


def rule_reach(rules, units, records, stamp, context=None, decide=None) -> Run:
    """Decide every unit with `decide` (by default a memo-less `Decider.decide`) and aggregate the results with `_decision_reach`, the aggregation the CLI uses, so the result holds the numbers a run prints."""
    if decide is None:
        decide = Decider(rules, context).decide
    return _decision_reach(rules, ((unit["id"], decide(unit)) for unit in units), records, stamp)


def _decision_reach(rules, decisions, records, stamp) -> Run:
    """Aggregate `(unit id, decision)` pairs, in surface order, into a `Run` without keeping unit records. The fills and the per-rule tallies come from the same decisions, so the records a run writes and the report it prints agree."""
    order = {rule["id"]: index for index, rule in enumerate(rules)}
    by_id = {rule["id"]: rule for rule in rules}
    fills = []
    credited_units: dict[str, list[str]] = {}
    composed_counts: dict[tuple[str, ...], list[int]] = {}
    matched_by: dict[str, list[str]] = {rule["id"]: [] for rule in rules}
    held_by: dict[str, list[str]] = {rule["id"]: [] for rule in rules}
    for unit_id, decision in decisions:
        composed = decision.composed
        if composed is None:
            for rule_id in decision.matched:
                matched_by[rule_id].append(unit_id)
            for rule_id in decision.held:
                held_by[rule_id].append(unit_id)
            continue
        for rule_id in composed.credited:
            credited_units.setdefault(rule_id, []).append(unit_id)
        counts = composed_counts.setdefault(composed.credited, [0, 0, 0])
        if composed.held:
            counts[2] += 1
        elif unit_id in records:
            counts[1] += 1
        else:
            counts[0] += 1
            fills.append(
                {
                    "unit": unit_id,
                    "verdict": composed.verdict,
                    "note": _composed_note(by_id, composed),
                    "at": stamp,
                }
            )

    reaches: dict[str, Reach] = {}
    for rule in rules:
        matched = matched_by[rule["id"]]
        blanks = [unit_id for unit_id in matched if unit_id not in records]
        note = f"[standing: {rule['id']}] {rule['note']}"
        for unit_id in blanks:
            fills.append({"unit": unit_id, "verdict": rule["verdict"], "note": note, "at": stamp})
        reaches[rule["id"]] = Reach(
            filled=blanks,
            verdicted=[unit_id for unit_id in matched if unit_id in records],
            held=held_by[rule["id"]],
            composed_credit=len(credited_units.get(rule["id"], ())),
            composed_lines=sum(1 for ids in composed_counts if rule["id"] in ids),
        )

    fills.sort(key=lambda record: record["unit"])
    ordered = sorted(composed_counts, key=lambda ids: [order[rule_id] for rule_id in ids])
    return Run(fills, {credited: composed_counts[credited] for credited in ordered}, reaches)


def open_units(units, records):
    """The units a run can still change: blanks, which are the only units a fill is written for, and units whose verdict is outside ACCEPTING_VERDICTS, which the tripwire reports. Every decision depends only on its own unit, the composed claim included, so leaving the other units out changes no fill. The full domain adds only the already-verdicted column and the reach rollup, which describe the verdict store, not the fills."""
    return [
        unit
        for unit in units
        if unit["id"] not in records or records[unit["id"]]["verdict"] not in ACCEPTING_VERDICTS
    ]


def _count(number, noun):
    """`number` followed by `noun`, with an s added unless the number is 1."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _own_line_total(reach):
    """The units one rule's own report line counts: filled, already verdicted, and held by except_left."""
    return len(reach.filled) + len(reach.verdicted) + len(reach.held)


def _tally_line(name, filled, verdicted, held, open_only):
    """One report line for a rule or a credited tuple. Under `--open-only` the already-verdicted column is omitted, not shown as zero, because the run never saw the units it would count."""
    column = "" if open_only else f"{verdicted} already verdicted, "
    return f"  {name}: {filled} filled, {column}{held} held for review by except_left"


def _tally_lines(rules, run, open_only):
    """One line per rule in rules-file order, then one line per composed reading."""
    lines = []
    for rule in rules:
        reach = run.reaches[rule["id"]]
        lines.append(
            _tally_line(rule["id"], len(reach.filled), len(reach.verdicted), len(reach.held), open_only)
        )
    for credited, (filled, verdicted, held) in run.composed_counts.items():
        lines.append(_tally_line(" + ".join(credited), filled, verdicted, held, open_only))
    return lines


def _rollup_lines(rules, reaches):
    """Each rule's total reach: the units its own line counts, the units composed lines credited it at, their sum, and how many composed lines name it. Then a REACHED NOTHING line for each rule that reached nothing, since a row of zeros is easy to miss."""
    lines = [
        "  per-rule reach (a window a composed line explains counts toward every rule that line credits, "
        "so these totals deliberately do not sum to the run):"
    ]
    for rule in rules:
        reach = reaches[rule["id"]]
        own = _own_line_total(reach)
        lines.append(
            f"    {rule['id']}: {own} on its own line, {reach.composed_credit} credited across "
            f"{_count(reach.composed_lines, 'composed line')}, {own + reach.composed_credit} in all"
        )
    for rule in rules:
        reach = reaches[rule["id"]]
        if _own_line_total(reach) or reach.composed_credit:
            continue
        lines.append(
            f"  REACHED NOTHING: {rule['id']} matched no window on its own and no composed line credited "
            "it. A narrow rule aimed at a form this surface does not carry yet reads exactly like this, so "
            "it lands as it stands; if the form is already migrated, the rule wants another look."
        )
    return lines


def _tripwire_lines(reaches, records):
    """A WARNING line naming the matched units whose verdict is outside ACCEPTING_VERDICTS, or no line when there are none. A standing rule that matches a window the user judged some other way is the sign of an over-broad rule. ACCEPTING_VERDICTS is wider than ALLOWED_VERDICTS because `identical` also accepts the new rendering (the reviewer found the highlighted part visually unchanged), so a rule matching such a unit agrees with the user."""
    caught = [
        f"{unit_id} under {rule_id} ({records[unit_id]['verdict']})"
        for rule_id, reach in reaches.items()
        for unit_id in reach.verdicted
        if records[unit_id]["verdict"] not in ACCEPTING_VERDICTS
    ]
    if not caught:
        return []
    return [
        f"  WARNING: a verdict outside {'/'.join(sorted(ACCEPTING_VERDICTS))} sits on "
        f"{_count(len(caught), 'matched unit')} — {', '.join(caught)}; a rule reaching a window the user "
        "judged otherwise is the shape an over-broad rule takes."
    ]


def _vocabulary_lines(rules, units):
    """Report each except_left family that no window on this surface joins from, a typo the REACHED NOTHING line cannot catch. The rule still matches what it always did and only its guard is inactive, so the line is informational and nothing fails. A guard that names a family the surface carries but holds nothing on this pass is not reported."""
    joining = {
        _joining_family(name) for unit in units for name in (unit.get("before") or {}).get("glyphs") or ()
    }
    return _joining_vocabulary_lines(rules, joining)


def _joining_vocabulary_lines(rules, joining):
    return [
        f"  except_left vocabulary: {rule['id']} guards against {family}, which no window on this surface "
        "joins from — the rule matches exactly what it always did, and its guard simply has nothing here "
        "to hold."
        for rule in rules
        for family in rule["match"].get("except_left", [])
        if family not in joining
    ]


def _explain_lines(rule_id, reach, records):
    """One rule's matched unit ids in the three columns its report line counts: the blanks it filled, the units already verdicted (with the verdict), and the units its except_left held. When every matched unit has a verdict, the whole reach is in the middle column, and no other output lists those ids."""
    verdicted = [f"{unit_id} ({records[unit_id]['verdict']})" for unit_id in reach.verdicted]
    return [
        f"  explain {rule_id}:",
        f"    filled ({len(reach.filled)}): {' '.join(reach.filled) or 'none'}",
        f"    already verdicted ({len(verdicted)}): {' '.join(verdicted) or 'none'}",
        f"    held by except_left ({len(reach.held)}): {' '.join(reach.held) or 'none'}",
    ]


def _listed_lines(rules, rule, listed, records, decide):
    """One line per listed unit, in the order listed: the verdict the store holds (`blank` when none), whether the unit is a name-grain candidate of the targeted rule (`_reachable`), and what the run decided. That is the composed reading that claims it, with the fill's verdict or `held` when a guard holds the window; else the rules whose own matchers accept it and the rules whose except_left holds it, in rules-file order; else that no rule matches it. It shows why a unit the user named is missing from the explain block. It reads the same `Decision` the run counted, so listing a unit changes no other line."""
    order = {each["id"]: index for index, each in enumerate(rules)}
    lines = []
    for unit in listed:
        unit_id = unit["id"]
        verdict = records[unit_id]["verdict"] if unit_id in records else "blank"
        candidacy = "a candidate" if _reachable(rule["match"], unit) else "not a candidate"
        decision = decide(unit)
        composed = decision.composed
        if composed is not None:
            outcome = "held" if composed.held else composed.verdict
            reading = f"composed {' + '.join(composed.credited)} ({outcome})"
        else:
            clauses = []
            if decision.matched:
                clauses.append("matched by " + " ".join(sorted(decision.matched, key=order.__getitem__)))
            if decision.held:
                clauses.append(
                    "held by except_left " + " ".join(sorted(decision.held, key=order.__getitem__))
                )
            reading = "; ".join(clauses) or "no rule speaks for it"
        lines.append(f"  listed {unit_id} ({verdict}): {candidacy} of {rule['id']}; {reading}")
    return lines


def targeted_report(rules, rule, units, listed_ids, records, stamp, decide):
    """The report of a `--targeted` run: the lines of the whole-domain report that concern one rule, computed over only the units the rule could match at the name grain (`_reachable`) plus the listed ids. Units stay in surface order, because `Reach` and the explain block list ids in iteration order and the lines must match the whole-domain run's. The subset contains every unit the whole-domain run would put on this rule's lines, and every decision depends only on its own unit, so the rule's own line, the composed lines crediting it, its rollup line, its tripwire, and its explain block are byte-identical to the whole-domain run's. Other rules' lines are left out, because over this subset they would be partial and the rollup would report every other rule as reaching nothing. The vocabulary line still reads the whole surface, since it uses glyph names and no decisions, and each listed unit gets its own line (`_listed_lines`)."""
    listed = list(dict.fromkeys(listed_ids))
    wanted = set(listed)
    match = rule["match"]
    by_id = {}
    joining = set()
    total = evaluated = 0

    def decisions():
        nonlocal total, evaluated
        for unit in units:
            total += 1
            joining.update(_joining_family(name) for name in (unit.get("before") or {}).get("glyphs") or ())
            if unit["id"] in wanted:
                by_id[unit["id"]] = unit
            if unit["id"] in wanted or _reachable(match, unit):
                evaluated += 1
                yield unit["id"], decide(unit)

    run = _decision_reach(rules, decisions(), records, stamp)
    rule_id = rule["id"]
    lines = [
        f"  targeted at {rule_id}: {evaluated} of {total} human units evaluated — the rule's "
        f"name-grain candidates plus {len(listed)} listed — and no fill file written"
    ]
    lines += _listed_lines(
        rules, rule, [by_id[unit_id] for unit_id in listed if unit_id in by_id], records, decide
    )
    lines += [
        f"  listed {unit_id}: not a human unit on this surface" for unit_id in listed if unit_id not in by_id
    ]
    crediting = {ids: counts for ids, counts in run.composed_counts.items() if rule_id in ids}
    lines += _tally_lines([rule], Run(run.fills, crediting, run.reaches), False)
    lines += _rollup_lines([rule], run.reaches)
    lines += _tripwire_lines({rule_id: run.reaches[rule_id]}, records)
    lines += _joining_vocabulary_lines([rule], joining)
    lines += _explain_lines(rule_id, run.reaches[rule_id], records)
    return lines


def main(
    argv=None,
    *,
    units=None,
    context=None,
    unit_source: Callable[[], Iterable[Mapping[str, Any]]] | None = None,
):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument(
        "verdicts", help="the verdicts file that defines blankness (an export or the autosave)"
    )
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--rules", default=str(RULES))
    parser.add_argument("--out", default=None, help=f"where the fill file goes (default {OUT.name})")
    parser.add_argument(
        "--explain",
        metavar="RULE",
        help="also print this rule's matched unit ids, split into the blanks it filled, the ones a verdict already covers, and the ones its except_left held",
    )
    parser.add_argument(
        "--targeted",
        action="store_true",
        help="evaluate only the --explain rule's name-grain candidates plus any --unit ids, print that rule's lines — own line, composed lines crediting it, rollup, tripwire and explain block, byte-identical to the whole-domain run's — and one decision line per listed unit; writes no fill file and never touches the memo. The authoring loop's form: a surface load rather than a domain evaluation. The whole-domain run stays the final pass and the cycle's form.",
    )
    parser.add_argument(
        "--unit",
        action="append",
        metavar="UNIT_ID",
        default=[],
        help="with --targeted: also evaluate this unit and say what the run decided about it; repeatable",
    )
    parser.add_argument(
        "--open-only",
        action="store_true",
        help="run the rules over only the blanks and the units verdicted outside approve/either/identical — the artifact cycle's form. The fills are byte-identical and the WARNING reads the same; what goes is the already-verdicted column and the per-rule reach rollup, whose numbers are readings of the store rather than of the fills. Combine with --require-reach, as the cycle does, to keep the rollup and the refusal.",
    )
    parser.add_argument(
        "--require-reach",
        action="store_true",
        help="after writing the fills, fail when any checked-in rule reaches no window of this surface — judged over the whole human domain with a blank store, so a rule whose every window a human has already judged still counts as reaching. The artifact cycle's form: a rule whose swath a rune change dissolved turns the plumbing step red, and `make verdict-ready` reads NOT READY, until the rule is deleted from the rules file or the form it waits for migrates.",
    )
    parser.add_argument(
        "--memo",
        metavar="PATH",
        help="persist every unit's decision here, keyed on the unit's content key, its ink deltas and the after font's digests for the families its window names, under a stamp over the deciding code and the fonts, with each entry naming the rules that could speak for its unit; a later run with the same stamp evaluates the units whose key is new and the units a rule that moved since the memo was written can reach, and a reworded note re-evaluates nothing. The fills and the report are byte-identical served or computed. The verdict chain passes this; a dry run against candidate rules leaves it off so it never overwrites the chain's memo with another rules file's decisions.",
    )
    parser.add_argument(
        "--fresh-memo",
        action="store_true",
        help="with --memo: evaluate every unit regardless of what the memo holds, and rewrite it",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="how many worker processes decide the units the memo cannot serve, once that pile is deep enough to pay for a pool's startup; 1 is the serial pass. This tool derives no width of its own: the verdict chain forwards the artifact cycle's, priced there beside the gates the plumbing step shares the box with, and a hand run over the whole domain states the width the cycle's plan prints for the box (`make artifact-cycle ARGS='--dry-run'`, its `plumbing --standing-fill-jobs` line), served by the daemon or not. The fills, the memo and the report are byte-identical at any width.",
    )
    standing_client.add_arguments(parser)
    args = parser.parse_args(argv)
    if args.fresh_memo and args.memo is None:
        parser.error("--fresh-memo needs --memo")
    if args.open_only and args.explain is not None:
        parser.error(
            "--explain reads the whole domain's already-verdicted column and cannot be combined with --open-only"
        )
    if args.targeted and args.explain is None:
        parser.error("--targeted takes its rule from --explain")
    if args.targeted and (args.out is not None or args.memo is not None or args.require_reach):
        parser.error(
            "a targeted run writes no fill file and no memo, and reach is a reading of the whole domain: "
            "drop --out/--memo/--require-reach"
        )
    if args.unit and not args.targeted:
        parser.error("--unit needs --targeted")
    if units is None and context is None and unit_source is None:
        served = standing_client.ask("fill", argv, args.surface, mode=args.daemon, socket_path=args.socket)
        if served is not None:
            return standing_client.relay(served)

    surface = pathlib.Path(args.surface)
    manifest = json.loads((surface / "manifest.json").read_text())
    data = json.loads(pathlib.Path(args.verdicts).read_text())
    if data.get("manifest_generated_at") != manifest["generated_at"]:
        raise SystemExit(
            f"{args.verdicts} is stamped {data.get('manifest_generated_at')} but the surface is "
            f"{manifest['generated_at']}; unit ids must never be joined across manifests — carry it forward first"
        )
    rules = load_rules(pathlib.Path(args.rules))
    if args.explain is not None and not any(rule["id"] == args.explain for rule in rules):
        raise SystemExit(f"--explain names {args.explain!r}, which is not a rule id in {args.rules}")
    records = latest_verdicts(pathlib.Path(args.verdicts))
    source = (
        unit_source() if unit_source is not None else (iter_human_units(surface) if units is None else units)
    )
    eligible = (
        unit
        for unit in source
        if not unit.get("no_verdict") and unit.get("batch") is not None and unit.get("render_groups") == 1
    )
    composable = _composable(rules)
    declared = [shape for shape in (_shape_of(rule["match"]) for rule in rules) if shape is not None]
    wants_deltas = any(shape.needs_ink_deltas for shape in declared) or len(composable) > 1
    context_error = None
    if not (any(shape.font_backed for shape in declared) or len(composable) > 1):
        context = None
    elif context is None:
        before_font, after_font = surface / "fonts" / "before.otf", surface / "fonts" / "after.otf"
        if not (before_font.is_file() and after_font.is_file()):
            context_error = (
                f"a {_shape_names(lambda shape: shape.font_backed, 'or')} rule, and any composed reading "
                "two or more composable rules could earn, re-shape their candidate windows in the "
                "surface's own font pair, and this surface carries no fonts/before.otf + fonts/after.otf "
                "— rebuild the surface (make review-cycle) first"
            )
        else:
            context = SlideContext(before_font, after_font)

    memo = None
    if args.memo is not None:
        environment, family_digests = memo_environment(surface)
        memo = Memo.open(args.memo, environment, family_digests, fresh=args.fresh_memo)
    decider = Decider(rules, context, memo)

    domain_ids = []
    candidate_ids = []
    joining = set()
    has_deltas = False

    def asked_units():
        nonlocal has_deltas
        for unit in eligible:
            unit_id = unit["id"]
            domain_ids.append(unit_id)
            has_deltas |= unit.get("ink_deltas") is not None
            joining.update(_joining_family(name) for name in (unit.get("before") or {}).get("glyphs") or ())
            if memo is not None:
                memo.key_for(unit)
            candidate = (
                not args.open_only
                or unit_id not in records
                or records[unit_id]["verdict"] not in ACCEPTING_VERDICTS
            )
            if candidate:
                candidate_ids.append(unit_id)
            if context_error is None and (candidate or args.require_reach or args.targeted):
                yield unit

    target_lines = []
    if args.targeted:
        rule = next(rule for rule in rules if rule["id"] == args.explain)
        target_lines = targeted_report(
            rules, rule, asked_units(), args.unit, records, manifest["generated_at"], decider.decide
        )
    else:
        _prefill(decider, asked_units(), args.jobs)
    if wants_deltas and not has_deltas:
        raise SystemExit(
            "the surface carries no ink_deltas fields, so it predates the "
            f"{_shape_names(lambda shape: shape.needs_ink_deltas, 'and')} shapes; such a rule cannot "
            "match anything on it — rebuild the surface (make review-cycle) first"
        )
    if context_error is not None:
        raise SystemExit(context_error)
    if args.targeted:
        for line in target_lines:
            print(line)
        return 0

    run = _decision_reach(
        rules,
        ((unit_id, decider._decided[unit_id]) for unit_id in candidate_ids),
        records,
        manifest["generated_at"],
    )
    reaches = run.reaches
    if args.require_reach:
        reaches = _decision_reach(
            rules,
            ((unit_id, decider._decided[unit_id]) for unit_id in domain_ids),
            {},
            manifest["generated_at"],
        ).reaches

    lines = []
    if memo is not None:
        held = memo.write_primed(decider.roster)
        lines.append(
            f"  memo: served {decider.served}, computed {decider.computed}, unkeyed {decider.unkeyed}; "
            f"{memo.path.name} holds {held} {'entry' if held == 1 else 'entries'}"
        )
    lines += _tally_lines(rules, run, args.open_only)
    if args.require_reach or not args.open_only:
        lines += _rollup_lines(rules, reaches)
    lines += _tripwire_lines(run.reaches, records)
    lines += _joining_vocabulary_lines(rules, joining)
    if args.explain is not None:
        lines += _explain_lines(args.explain, run.reaches[args.explain], records)

    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": run.fills,
    }
    out = pathlib.Path(args.out) if args.out else OUT
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        f"wrote {out.name}: {len(run.fills)} standing-approval verdicts onto manifest "
        f"{manifest['generated_at']}"
    )
    for line in lines:
        print(line)
    if args.require_reach:
        unreached = [
            rule["id"]
            for rule in rules
            if not (_own_line_total(reaches[rule["id"]]) + reaches[rule["id"]].composed_credit)
        ]
        if unreached:
            print(
                f"  the plumbing refuses: {', '.join(unreached)} reached no window of this surface. There "
                "is no retired marker and no allowance for a rule that has run out of windows — delete it "
                f"from {args.rules}, or leave it and this stays red until the form it waits for migrates."
            )
            return 1
    return 0


if __name__ == "__main__":
    main()
