"""Apply the checked-in standing approvals (rebuild/standing-approvals.yaml) to the live review surface: for every rule, find the blank human units whose before→after delta matches the rule's pattern and emit fill records for them into an importable verdicts file.

Each expressible delta shape is a row in SHAPES, and a rule declares exactly one of them — which one is keyed by the field its `match.after` carries.

- The `ligature` shape is a pivot letter whose backward join drops as it ligates with its follower; it holds the seams flanking the delta fixed.

- The `follower_cells` shape is a pivot letter that gives up a named stretch of exit — the whole of a named `ex-ext-N` the before glyph carried, or the columns down to a shorter one its after cell keeps, or the columns a named `ex-con-N` on the after cell pulls back from a default that never carried an exit-extension token: the two sides must line up letter for letter over an identical seam vector, the follower must be one of the families the rule names, the pivot and the follower must settle into cells the rule names in full — rune, stance, entry, exit and the whole adjustment set, which is what says how much of the stretch went — and the unit's own primary judged adjacency must be exactly that pivot–follower seam with no secondary seam anywhere else in the window. That last requirement is the load-bearing one, because an unchanged seam vector is not unchanged ink: a window can hold every seam still and be asking about a different letter's stroke entirely, and only the surface's own judgment fields say which letter the unit is about.

- The `ink_deltas` shape works from the opposite end and is ink-exact rather than structural: it names the surface's own per-config localized ink-delta digests (rebuild/review/ink.py's `delta_digest`, persisted on every unit), so a unit matches only when the window's entire before→after ink change, under every config it diverges on, is byte-identical to a blessed delta — every structural difference the unit still carries is then name-grain only, and any extra ink anywhere fails the match closed.

- The `slide` shape judges the rendered pixels rather than either grain of names: it re-shapes the window in the surface's own font pair and matches when the whole visible change is its named pivot letter and everything after it sliding by a declared column count — which is what lets it survive a union-invisible name-grain re-spelling riding along in the same window, the composition that mints a fresh whole-window digest and orphans an ink-delta rule.

- The `gained` shape judges the same rendered pixels for a letterform that keeps cells the old font omitted: the old-font pivot form gives way to a named new form that is the same picture plus a named set of own-frame cells, and everything after the pivot moves by the declared column count — zero when the fuller form keeps its advance, positive when the added ink lengthens it. So a window whose only change is ·Roe keeping the baseline bar the old shortened-bottom form dropped matches at zero, ·Gay's extra exit cell carrying ·No one column right matches at one, and a window that also carries a blessed slide still needs the composed reading.

- The `gap` shape judges a named join that is now a break: the pivot keeps its exact picture and own-frame origin, the follower keeps its exact picture and origin and sits a declared number of columns further, and everything after the follower sits the same extra gap away — which is what a cursive attachment going away looks like, as distinct from a sidebearing change (the slide shape, whose origin moves with the letter).

- The `entry_drop` shape judges the same rendered pixels for a letter that gives up a named stretch of left-side entry: the old-font pivot form gives way to a named new form whose own-frame picture is the old one compacted left by that many columns — every dropped cell sitting in the columns that came off, the remaining cells shifting left by the same count, origin and placement standing still — and everything after the pivot sliding closer by that count, so a window whose only change is ·Low losing the extra baseline pixel the old font drew after ·See matches, and a window that also carries a blessed slide still needs the composed reading.

- The `entry-contracted` shape judges the same rendered pixels for one or more named left–pivot pairs whose pivot pulls its entry inward by a declared number of columns: the letter comes that many columns closer, carried by however the after form's own frame took the contraction — a frame that moved its own-frame origin right by the whole count holds its placement still, one that moved its origin not at all carries its placement the whole count left, and the ink lands in the same place either way. The left-side loss stays inside the contracted columns, any far-right tail change is exactly the difference between the exit extensions named by the before and after glyphs, and everything after the pivot moves by the contraction combined with that exact exit-extension delta. A name-grain respelling in the suffix may ride along when the pivot still paints every cell it appears to lose, so the visible pivot-plus-suffix union remains exact; any visible ink change fails closed for the composed reading.

- The `stub_drop` shape judges the same rendered pixels for a letter that gives up a named left-side stub while the ink it keeps stays where it was: the old-font pivot form gives way to a named new form whose own-frame picture is the old one compacted left by that many columns, its own-frame origin standing still while its placement moves right by the same count — the left edge catching up to the ink that remains — and every other pixel in the window unmoved, which is what tells stub-dropped from entry-extension-dropped, whose remaining ink slides closer while its placement stands still: a window whose only change is ·May losing the leftover left pixel after ·Ah matches, and a window that also carries a blessed join-drop still needs the composed reading. A pivot here is a position, not a name: a second ·May keeping its old loop beside the one that lost the stub rides as span ink, because only the before side's named form says which of the two after loops is the drop.

- The `redrawn` shape judges the same rendered pixels for a letter redrawn in place to a named new form: the old-font pivot form gives way to a named new form whose own-frame picture is the old one with the named cells gone and the named cells added — both sets read at one common column offset, because an entry extension inserts a column at the pivot's left edge and carries the whole frame right with it, so an entry-extended variant shows the same trade one column over — the own-frame origin standing still, and everything after the pivot sliding by the declared count, which may be zero when the new form keeps the pivot's advance: ·Eight's bowl pulling in one column before ·Tea and ·It, beside the dropped connector extension that slides ·It closer, is the founding example, and a window that also carries a second blessed change still needs the composed reading. The pivot's own placement stands still too, unless its new form names an entry contraction, which lets the letter sit up to that many columns closer to whatever precedes it — however much of the contraction the seam behind it had left to give, since a left neighbor the old font had already drawn that tight leaves nothing to close — and everything after the pivot carries whatever it took there on top of the declared count: ·J'ai's crown coming in after a half-height ·Pea or ·Tea moves the letter and the rest of the word, while the same crown after an ·At the old font had already contracted moves only what the dropped tail moves. Its added set may be empty, which is a form that only gives ink up — ·Key's foot dropping its terminal pixel before ·May, ·No and ·It, its follower coming a column closer — and that is the shape an exit contraction wants whenever the survey's windows carry anything else at all, because the `follower_cells` shape reads only names and would bless whatever else the window did.

- The `join-created` shape judges a named pair that newly joins: the pivot and follower may redraw, the pivot keeping its own-frame origin while the follower either keeps its own or reaches back over its old left edge by a declared column count, the pivot stays under the standing displacement, the follower moves by a second declared count, everything after it moves by that count combined with the follower's own declared advance delta, and the recorded break becomes the named height. A follower reaches back when the form that takes the join inserts columns at its left edge — ·Gay's reachable-at-the-baseline stroke does, which is why a rule has to say so rather than let a moved origin read as a slide. The shift and the advance are two counts because a follower that redraws wider gives back what the join closed — the reaches-way-back ·Utter comes a column nearer ·May and leaves the rest of the word standing — and one number cannot say both. Its pivot side may name several families at once, which is how one letter's new entry is recorded in a single rule for every left neighbor that now reaches it — a window with any other ink change needs the composed reading.

- The `retarget` shape judges a named join that has changed height: the pivot and the follower may both redraw, they keep their own-frame origin, the pivot keeps its column placement, the follower comes a declared number of columns nearer — none at all where the new seam leaves it standing, two where ·Utter reaching ·May at the x-height pulls it back into itself — and everything after the follower sits a second declared number of columns over. Half-·Tea joining ·No at the x-height becoming full ·Tea joining flipped ·No at the baseline is what that looks like, as distinct from a join becoming a break (the gap shape, whose pictures stay and whose follower sits further).

Each shape's own docstring states exactly what it proves, and none claims to bound the window beyond that.

Above them sits a reading no rule declares — the composed one, which runs first and asks whether two or more rules together account for every rendered pixel of one window. The founding example makes it unavoidable: a window where the grounded ·See slides a column closer to what precedes it *and* ·J'ai gives up its exit extension carries two separately-blessed changes at once, and neither rule can speak for it alone — the slide shape fails closed on the extension pixel, the extension shape is structurally blind to ink outside its judged seam.

Which shapes compose is the `composable` flag on their SHAPES row, and what earns it is naming a local pixel change the walk can prove — a displacement, a named set of own-frame cells appearing on or traded on the pivot, a named join becoming a gap, a named left-side stretch or stub the pivot gives up, or a named join being created or changing height; a shape that reads a whole window's name-grain structure, or its whole ink change byte for byte, says nothing about any one position and so has nothing to contribute to a walk.

A name-grain pre-gate keeps the pass cheap: each composable rule's candidate positions come straight off the index record, and a window where fewer than two rules have a candidate is never shaped at all.

The walk then re-shapes the window in the surface's font pair and carries a running column displacement across it:

- a slide event moves it by the declared count with the pivot leading the next span
- an extension event drops off the named seam row the tail the pivot gave up — the named extension, less any shorter one its after cell keeps, or the named contraction — and moves again with the named follower leading — that span a translation, or the same picture compacted left by a dropped entry extension, or the named follower skipped when it redrew some other way
- a join-dropped event leaves the pivot in place under the standing displacement, adds the declared gap, and moves again with the follower leading
- an ink-gain event adds the named cells on the pivot, judged piece by piece, and moves by the declared count with the next glyph leading the next span
- an entry-drop event leaves the pivot under the standing displacement, or the part of a contraction its own frame did not take further left still, compacting its remaining ink left by the named columns, and moves the displacement closer with the next glyph leading the next span
- a redrawn event trades the named cells on the pivot under the standing displacement, or up to the entry contraction its new form names further left still, and moves the displacement by its declared count plus whatever the pivot took there, with the next glyph leading the next span
- a join-created event leaves the pivot under the standing displacement while its follower leads the declared shift and the glyphs past the follower lead that shift combined with the follower's advance delta — or, when its pivot is itself an entry-contraction event, chains behind that event: the contraction judges the pivot's entry and origin, the created join judges only its follower, and the follower leads by both shifts combined, which is what lets ·Ah's contracted entry after ·J'ai and its new x-height join into ·Gay explain one window between them — and it chains the same way when its pivot is a join-retargeted event's follower, the retarget judging that letter's incoming seam and the created join its outgoing one, which is what lets ·It's lowered join into ·No and ·No's new join into ·Gay explain one window between them; and its own follower may in turn be the next event rather than the head of a span — a retarget on that letter's outgoing seam, the exit extension that letter gives up, or the trade its own new form makes — which is what lets ·Pea's lowered join into ·No, ·No's new join into ·Ah and ·Ah's dropped tail before ·Bay explain one window between the three of them; it holds however the join itself arose, so ·Ah's new x-height join into ·Gay and ·Gay's own redraw, which meets that seam and gives its baseline tail up in one stroke, explain one window between them, and with ·J'ai leading, ·Ah's contracted entry reads as a third event in the same window
- a join-retargeted event leaves the pivot in place under the standing displacement — both it and the follower may redraw — brings the follower the columns it declares nearer, which is none at all where the retargeted join leaves it standing, moves the displacement the rest of the way to the declared shift, and moves again with the glyph after the follower leading the next span — or, when its pivot is a join-created event's follower, chains behind that event the other way round: the created join judges that letter's incoming seam and the retarget its outgoing one, so the glyph after the retarget's follower leads by both shifts combined, which is what lets ·Et's new baseline join into ·Gay and ·Gay's raised join into ·No explain one window between them

Every stretch between events is a span whose before picture, displaced by whatever has accumulated, must equal its after picture exactly.

A join-dropped or extension event whose follower is itself an event chains instead of consuming that follower: the follower is the next event under the displacement the first event just applied, which is what lets ·At's dropped x-height join and ·It's dropped exit extension explain one window between them.

A candidate whose own contract fails is simply not an event and its ink is judged as ordinary span ink, so adding a rule to this file can never un-explain a window; two rules claiming one position, or a join-retargeted or join-created event whose follower position is itself claimed, is ambiguous and refuses. The sanctioned shares of a position are the four chains above — a created join behind an entry contraction is a claim on the letter's seam beside a claim on its entry, and a created join behind a retarget on that retarget's follower, or a retarget, an exit-extension drop or a redrawn trade behind a created join on that join's follower, is a claim on the letter's outgoing seam beside a claim on its incoming one, none of them two claims on the same thing — and a created join whose pivot moved its origin is not an event at all without a contraction to chain behind. An extension's follower is the named after cell, not a pixel-identity translation, so a named redraw (·May losing the entry the old font stacked on the same seam, ·I's smaller loop after ·Tea) is the rule's own subject and does not block composition.

One refusal stays deliberate: because the pivot is judged piece by piece rather than in a union, a pivot whose after form also drops a cell off the seam row (·J'ai's crown contracting under an ·At tuck) never composes. An ink-gain whose after form loses a cell or gains one the rule did not name never fires as an event, which leaves that ink to be judged as ordinary span ink; an entry-drop whose remaining cells do not equal the old picture shifted left by the named columns, or that drops a cell outside those columns, never fires either; a redrawn candidate whose trade is not exactly the named cells at one common column offset never fires either.

Credit needs two or more rules — a window one rule accounts for alone belongs to that rule's own line — and a composed fill's verdict is the weakest over the credited rules and over every non-composable rule that matches the window too, its note naming the credited ids in rules-file order.

Any rule's `except_left` family, met anywhere in the window, refuses the whole unit rather than the one position, so a guarded context can never ride along beside an unguarded one; a composed reading reads each credited rule's guard in that rule's own shape's scope, and any refusal holds the whole unit — counted on the composed line, never filled, and never handed back to the single-rule pass.

This is the zero-touch sibling of echo_verdicts.py: echo fill extends the user's past verdicts to pixel-identical lookalikes, while a standing rule extends a recorded once-and-for-all decision to instances the user has never seen (new left letters minted by later migrations), so those units never queue. The guard list is the point of authoring a guarded rule at all: a rule's except_left families are held for review, so the one context the user does want to see still reaches the docket.

Records are stamped with the manifest's generated_at, so any human verdict beats a standing fill on merge, and a parked unit (a skip verdict) is not blank and is never filled. The artifact cycle runs this after the echo fill, with a merge_verdicts pass to land the file. The run's report rolls every composed line's credit back up per rule, so each rule's whole reach reads in one place — deliberately not a column that sums across rules, since a window two rules explain between them counts toward each of them.

Every decision above is per-unit pure: what the composed reading credits a window with, whether a guard holds it, and which rules' own matchers accept or hold it are a function of the unit's index record, the two fonts' rendering of its window, and the rules file, and of nothing else — the verdict store only decides which of those decisions become fills. `Decider.decide` is that function, `rule_reach` assembles a run out of its answers, and the memo (`Memo`, the `--memo` flag; the verdict chain passes it) persists the answers across passes so a surface-moving pass evaluates only the units whose key is new. A unit's key is its build-time `content_key` stamp, joined with the persisted `ink_deltas` that stamp deliberately leaves out and with the after font's compiled-glyph digest for every family the window's after cells name (`fingerprint.after_font_glyph_digests`, the same per-family grain the review unit cache and the oracle's position store invalidate at, so a drawing or anchor change reaches exactly the windows that can feel it); a unit the surface never stamped is evaluated every pass and never stored. The memo's own stamp is the rules file's raw bytes, since the fill quotes each rule's `note` into every record, the code that decides (`MEMO_CODE_MODULES`, held to this module's import closure by rebuild/test_standing_verdicts.py), the before font wholesale, the after font's family-blind remainder, and `uv.lock` for the shaper; any of those moving drops the memo entirely, and over-invalidation is the safe direction. What is written back is bounded to the units on this surface, so it never outgrows the human domain, and the fills and the report are byte-identical served or computed, which rebuild/test_standing_verdicts.py proves over the frozen mini bundle. The `--require-reach` rollup reads the same answers, so its pass over the whole domain costs no second evaluation.
"""

import argparse
import gzip
import hashlib
import json
import pathlib
import re
import sys
from collections.abc import Callable
from typing import NamedTuple, NoReturn

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rebuild.pipeline import fingerprint  # noqa: E402
from rebuild.review.ink import IDENTITY_DIFF, InkComparator, delta_digest, features_for  # noqa: E402
from rebuild.validation.classify import PIXEL_SIZE  # noqa: E402
from rebuild.tools.review_docket import ACCEPTING_VERDICTS, latest_verdicts, load_units  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
RULES = ROOT / "rebuild/standing-approvals.yaml"
OUT = ROOT / "verdicts-standing-fill.json"
FORMAT = "ams-standing-approvals/1"
MEMO_FORMAT = "ams-standing-fill-memo/1"
MEMO_NAME = "standing-fill-memo.ndjson.gz"
# The code a fill decision is a function of: this module and every repo module it reaches that reads a unit or shapes a window. rebuild/test_standing_verdicts.py holds the roster to the walked import graph, stopping at the key side — the pipeline modules, whose edits move the keys or the stamp rather than a decision.
MEMO_CODE_MODULES = (
    "rebuild/review/ink.py",
    "rebuild/review/unit_index.py",
    "rebuild/tools/review_docket.py",
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


def _extension_columns(token):
    """How many columns an ex-ext-N token names."""
    return int(token.rsplit("-", 1)[1])


def _kept_extension(cell):
    """The exit extension a review-surface cell still carries, as a column count — zero when its adjustment set names none."""
    return max(
        (_extension_columns(token) for token in _cell_adjustments(cell) if EXIT_EXTENSION.fullmatch(token)),
        default=0,
    )


def _cell_contraction(cell):
    """The exit contraction a review-surface cell carries, as a column count — zero when its adjustment set names none."""
    return max(
        (_extension_columns(token) for token in _cell_adjustments(cell) if EXIT_CONTRACTION.fullmatch(token)),
        default=0,
    )


def _dropped_entry(glyph, cell):
    """How many columns of entry extension the before glyph carried that the after cell does not keep."""
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
    """The largest column count carried by a glyph-name modifier matching `pattern`, or zero when it carries none."""
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
    """How many columns the named token says the pivot gave up at this after cell — the named extension less any shorter one the cell keeps, or the named contraction in full."""
    named = _extension_columns(token)
    return named if EXIT_CONTRACTION.fullmatch(token) else named - _kept_extension(cell)


def _families(value):
    """A rule field that names families: one family name, or a list of them, read as the list either way."""
    return list(value) if isinstance(value, list) else [value]


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


def _matches_ligature(match, unit, excluded, context=None):
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


def _matches_extension(match, unit, excluded, context=None):
    """A pivot letter that gives up the named stretch of exit into a seam that holds its named height, with the whole seam vector standing still, the follower drawn from the families the rule names, and the pivot and follower settling into cells the rule names in full. Naming the cells in full is what makes the delta exact: rune, stance, entry and exit pin the bitmap binding on both sides of the seam, and the whole adjustment set pins what the pivot is left carrying — no extension at all, the shorter one the rule names, or the contraction the rule names — so a rule speaks for exactly the columns between the stretch it names and the one its pivot cell keeps, and an extension traded for a shorter one is never read as one dropped outright, nor a contraction as a dropped extension, nor the reverse. Because an unchanged seam vector says nothing about ink elsewhere, localization is taken from the surface's own judgment fields rather than inferred: the unit's primary judged adjacency must be exactly this pivot–follower seam, and any window carrying a secondary seam is visibly asking about somewhere else too and is refused outright. Nothing ligates here — that is enforced, not assumed — which is also why the follower's whole name is the right thing to compare against: a ligature in that slot breaks the letter-for-letter requirement and never reaches this loop. The follower's after cell must be that same family's, so a rule naming several followers can never read one family's cell as standing in for another's. A word-initial pivot has no left neighbor and so nothing for except_left to hold."""
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
    """The extension shape's own coherence, checked once at load so a rule can never quietly mean something else: the named token has to be an exit-side extension or contraction, since an entry-side token would pin the seam on the far side of the pivot from the `seam_out` the rule names; the cells have to belong to the letters the rule names — the pivot's family, and one of the follower families — and every pivot cell has to actually give up columns. An `ex-ext-N` rule forbids a cell keeping an exit extension as long as the named one, because this shape speaks for the whole extension or the difference down to the shorter one a cell keeps, never for a tail that stayed or grew. An `ex-con-N` rule requires every pivot cell to carry exactly that contraction and none of an exit extension, because the contraction *is* the named drop and a leftover `ex-ext` would be a different stretch than the one the rule claimed."""
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


def _split_at(run, indices):
    """The run cut into spans at the given piece indices: everything before the first pivot, then one span per pivot running from that pivot up to the next. Each pivot leads the span it starts, so its own ink is judged under the same displacement as everything it drags along."""
    bounds = [0, *indices, len(run)]
    return [run[start:stop] for start, stop in zip(bounds, bounds[1:])]


def _span_cells(intern, span):
    """The pixel picture one span of placed pieces paints: the union of each shape's rasterized cells translated to its placement, or None when any shape is not a grid-rectilinear picture or any placement is off-grid — which a caller reads as no picture claim being possible."""
    cells = set()
    for _name, key, x, y, _origin in span:
        shape_cells = intern.cells(key)
        if shape_cells is None or x % PIXEL_SIZE or y % PIXEL_SIZE:
            return None
        cells.update((x // PIXEL_SIZE + column, y // PIXEL_SIZE + row) for column, row in shape_cells)
    return cells


def _slide_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is exactly the declared slide, re-derived from the fonts: shape the window under one of the unit's configs, cut both ink runs at their pivot positions, and require each corresponding span's pixel picture to be identical once displaced by the cumulative slide — the span before the first pivot by nothing, the span the first pivot leads by the full slide, and one more slide for every further pivot. Each pivot piece must also keep its exact shape at its height with its own-frame origin displaced by exactly the slide, which pins the mechanism to the pivot's sidebearing rather than to any drift that happens to land the same pixels. Anything the contract cannot hold — no pivot on the before side, pivot counts that disagree, a shaped run that contradicts the unit's recorded glyphs, an off-grid placement, a non-rectilinear outline — reads as no match, so the unit queues."""
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
    """A letter re-spaced against what precedes it, matched at the rendered-pixel grain: the old-font pivot form gives way to a named new form and the window's whole visible change is the pivot and everything after it sliding by the declared column count — every pixel before the pivot stands still, and everything from the pivot on renders pixel-for-pixel identical once slid. Judging pixels rather than per-glyph pieces is the shape's point: a name-grain re-spelling to the pivot's right that hands ink from one glyph to a neighbor without changing the union (the ·At·J'ai tuck riding under a slid ·See is the founding example) is invisible in the picture and must not orphan the rule the way it orphans a whole-window ink-delta digest — while a change that shows so much as one pixel anywhere in the window fails *this* match closed. That last is where the composed reading picks up rather than the end of the story: a window this shape refuses only because a second separately-blessed change moved a pixel it has no vocabulary for may still be explained by both rules together, and the composed pass has already claimed such a window before this matcher ever sees it. One shaped config speaks for all of them: a unit's glyph runs are constant across its configs by the surface's own dedupe, so its per-config deltas can only agree, and the matcher holds that as a precondition (one distinct persisted digest covering exactly the unit's config set) instead of assuming it. except_left reads as the ink-delta shape's does: no pivot position bounds the window, so an excluded family joining anywhere in it refuses the unit."""
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
    """The slide shape's own coherence, checked once at load: the slide must actually move (zero columns is the identity, which is machine-approved already, so a rule declaring it could only mask a typo), and every pivot form named on either side must belong to one family — a slide rule speaks for one letter's re-spacing, so a second family in the lists could only be a paste error."""
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
    """The named own-frame cells an ink-gain rule says the after form keeps, as a set of (column, row) pairs."""
    return {tuple(point) for point in match["after"]["gained"]}


def _split_around(run, indices):
    """The run cut into the spans that sit strictly between the given piece indices: everything before the first, everything between one and the next, and everything after the last. The indexed pieces themselves are omitted, so a caller that judges those pieces on their own can ask whether the rest of the window is an identity without the indexed ink in the picture."""
    starts = [0, *[index + 1 for index in indices]]
    stops = [*indices, len(run)]
    return [run[start:stop] for start, stop in zip(starts, stops)]


def _gain_holds(match, before, after, intern):
    """Whether one pivot piece is the named ink-gain: same own-frame origin, both sides on the grid, and the placed before picture standing still inside the after frame plus exactly the named cells. The after frame may extend vertically around the old picture, so the before cells are translated into the after frame by the pieces' vertical placement difference before the exact comparison."""
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
    """Whether the window's rendered before→after change is exactly the named cells appearing on the named pivot, re-derived from the fonts: shape the window under one of the unit's configs, judge each pivot piece as the placed old picture standing still inside the new frame plus those cells at the same horizontal placement and own-frame origin, and require every span strictly between the pivots to render identically under the cumulative declared shift. The new frame may extend vertically around the old picture; `_gain_holds` aligns the two frames from their placed Y coordinates before comparing them. Anything the contract cannot hold — no pivot on the before side, pivot counts that disagree, a shaped run that contradicts the unit's recorded glyphs, an off-grid placement, a non-rectilinear outline, a lost cell, an unnamed extra cell, or following ink that moves by another amount — reads as no match, so the unit queues."""
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
    """A letterform that keeps a named set of cells the old font omitted, matched at the rendered-pixel grain: the old-font pivot form gives way to a named new form whose placed old pixels stand still inside the new own frame plus those cells, and everything after the pivot moves by the declared column count. The new frame may extend vertically around the old picture; same horizontal placement and own-frame origin pin the extra ink to the letterform rather than to a slide or a sidebearing change, and any other ink change anywhere in the window fails this match closed — which is where the composed reading picks up, so a window this shape refuses only because a second separately-blessed change moved a pixel it has no vocabulary for may still be explained by both rules together. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the slide and ink-delta shapes do."""
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
    """The ink-gain shape's own coherence, checked once at load: every pivot form named on either side must belong to one family — a gain rule speaks for one letter's extra cells, so a second family in the lists could only be a paste error — and it has to name at least one of them, since the gain is the whole change this shape blesses. The required shift may be zero because a fuller form can keep its advance."""
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
    """Glyph indices where the named join dropped: the pivot prefix, the follower's family, and the named height becoming a break, letter for letter, with the follower's after cell still that same family's."""
    if not _letter_for_letter(unit):
        return []
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    followers = _families(match["before"]["follower"])
    pivot = match["before"]["pivot"]
    seam = match["before"]["seam_out"]
    reach = min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1
    return [
        i
        for i in range(reach)
        if _is_pivot(glyphs[i], pivot)
        and _family(glyphs[i + 1]) in followers
        and seams[i] == seam
        and after_seams[i] == "break"
        and _cell_rune(cells[i]) == _family(glyphs[i])
        and _cell_rune(cells[i + 1]) == _family(glyphs[i + 1])
    ]


def _join_piece_holds(before, after):
    """Whether one piece of a dropped join kept its picture: same shape, same height, same own-frame origin, both on the grid. Placement under the running displacement is the walk's job, not this contract's."""
    if before is None or after is None:
        return False
    if before[1] != after[1] or before[3] != after[3] or before[4] != after[4]:
        return False
    return before[2] % PIXEL_SIZE == 0 and after[2] % PIXEL_SIZE == 0 and before[3] % PIXEL_SIZE == 0


def _join_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is exactly the named join becoming a gap, re-derived from the fonts: shape the window under one of the unit's configs, find every pivot–follower pair whose recorded seam dropped from the named height to a break, require both letters to keep their exact shape at their height with their own-frame origin unmoved, and require each span's pixel picture to be identical once displaced by the cumulative gap — the span before the first follower by nothing, the span the first follower leads by the full gap, and one more gap for every further pair. Anything the contract cannot hold — no pair, a shaped run that contradicts the unit's recorded glyphs, a redrawn pivot or follower, an origin shift, an off-grid placement — reads as no match, so the unit queues."""
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
        if not _join_piece_holds(before_pieces.get(index), after_pieces.get(index)):
            return False
        if not _join_piece_holds(before_pieces.get(index + 1), after_pieces.get(index + 1)):
            return False
    intern = comparator.intern
    gap = match["after"]["gap"]
    followers = {index + 1 for index in pairs}
    before_span: list = []
    after_span: list = []
    step = 0
    for index in range(len(before_names)):
        if index in followers:
            if not _span_settled(intern, before_span, after_span, gap * step):
                return False
            before_span = [before_pieces[index]] if index in before_pieces else []
            after_span = [after_pieces[index]] if index in after_pieces else []
            step += 1
            continue
        if index in before_pieces:
            before_span.append(before_pieces[index])
        if index in after_pieces:
            after_span.append(after_pieces[index])
    return _span_settled(intern, before_span, after_span, gap * step)


def _matches_join_dropped(match, unit, excluded, context=None):
    """A named join that is now a break, matched at the rendered-pixel grain: the pivot letter keeps its exact picture and own-frame origin, the follower keeps its exact picture and origin and sits the declared number of columns further, and everything after the follower sits the same extra gap away. Origin unmoved is the shape's point: this is a cursive attachment going away, not a sidebearing change, so a slide of the follower would fail the origin check by design. Any other ink change anywhere in the window fails this match closed — which is where the composed reading picks up, so a window this shape refuses only because a second separately-blessed change moved a pixel it has no vocabulary for may still be explained by both rules together. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the slide and ink-gain shapes do."""
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
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _join_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_join_dropped(rule_id, match) -> None:
    """The join-dropped shape's own coherence, checked once at load: the gap must actually move (zero columns is the identity, which is machine-approved already, so a rule declaring it could only mask a typo), and it must be a widening (a dropped join sits the letters further apart, never closer), and the named seam must be a yK height — a break has nothing to drop."""
    if match["after"]["gap"] == 0:
        _fail(
            f"rule {rule_id!r}: match.after.gap is 0; an unmoved window is ink-identical and "
            "machine-approved already"
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


def _entry_columns(match):
    """The column count named by either entry-shortening shape."""
    after = match["after"]
    return after.get("entry_drop", after.get("entry_contraction"))


def _entry_shift(match, before_name, after_name):
    """The net follower displacement after one entry shortening: the named contraction or drop, plus any independently named change in the pivot's exit extension."""
    shift = -_entry_columns(match)
    if "entry_contraction" in match["after"]:
        shift += _glyph_adjustment(after_name, EXIT_EXTENSION) - _glyph_adjustment(
            before_name, EXIT_EXTENSION
        )
    return shift


def _entry_drop_holds(match, before, after, intern):
    """How far the pivot's own placement moves under the named entry shortening, or None when the piece is not one: same height, both on the grid, and either an old entry extension comes off under a fixed own-frame origin or a named after-side entry contraction pulls the letter in. In the fixed-origin case the after picture is the before picture compacted left by the named columns and the placement stands still. In the contraction case the two pictures are aligned by however far the after form moved its own-frame origin, and the placement makes up the rest of the contraction: a frame that took the whole contraction into its origin leaves the placement standing still, one that took none of it carries the letter the whole contraction closer, and the ink lands in the same place either way. The only left-side loss is inside the contracted columns, and any far-right difference must be exactly the exit-extension count the glyph names say changed. No other cell may disappear or appear."""
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
    """Whether the window's rendered before→after change is exactly the named left-side entry shortening on the named pivot, re-derived from the fonts: shape the window under one of the unit's configs, judge each pivot piece by `_entry_drop_holds` at the placement that contract answers for, and require every span strictly between the pivots to render identically once displaced by the cumulative drop — the span before the first pivot by nothing, the span after the first pivot by the full drop, and one more drop for every further pivot. Each post-pivot span is compared together with that pivot's already-validated after picture, so a suffix glyph may give up cells the pivot still paints without turning an invisible ownership handoff into another visual question. A pair-specific caller passes the positions already scoped by its named left family; the general entry-drop shape leaves them unset and judges every named pivot. Anything the contract cannot hold — no pivot on the before side, pivot counts that disagree, a shaped run that contradicts the unit's recorded glyphs, an off-grid placement, a non-rectilinear outline, a dropped cell outside the named columns, an unnamed visible cell — reads as no match, so the unit queues."""
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
            or not _named_pivot(before_run[index][0], match["before"]["pivots"])
            or not _named_pivot(after_run[index][0], match["after"]["pivots"])
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
    """A letter that gives up a named stretch of left-side entry, matched at the rendered-pixel grain: either the old form's extra entry columns come off under a fixed origin, or a named after-side entry contraction pulls the letter that many columns closer — taken into the after form's own-frame origin, into its placement, or shared between them; everything after the pivot slides closer by that count. The contraction reading admits only the exact far-right tail difference named by the before and after glyphs' exit-extension modifiers. Any other ink change anywhere in the window fails this match closed — which is where the composed reading picks up, so a window this shape refuses only because a second separately-blessed change moved a pixel it has no vocabulary for may still be explained by both rules together. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the slide and ink-gain shapes do."""
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
    """The pivot positions where the rule's named family stands immediately after one of its named left families."""
    glyphs = unit["before"]["glyphs"]
    left_families = set(_families(match["before"]["left"]))
    return [
        index
        for index, name in enumerate(glyphs)
        if index
        and _named_pivot(name, match["before"]["pivots"])
        and _joining_family(glyphs[index - 1]) in left_families
    ]


def _matches_entry_contracted(match, unit, excluded, context=None):
    """One or more named left–pivot pairs whose pivot contracts its entry by the declared columns, using `_entry_geometry` for the same rendered-pixel contract as the contraction arm of the entry-extension-dropped shape. Naming the immediate left families keeps a pair-specific decision pair-specific; the after glyph must carry the declared `en-con-N`, the letter must come that many columns closer however its own frame took the contraction — origin, placement, or the two between them — and the exact exit-extension delta named by the before and after glyphs is the only far-right change admitted. Everything after the pivot must move by the contraction combined with that exit-extension delta, and any other ink change fails closed for the composed reading to consider."""
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
    """Whether the window's rendered before→after change is exactly the named left-side stub coming off the named pivot with the remaining ink sitting still: same own-frame compact as an entry drop, but the after form's placement moves right by the declared column count (the left edge catching up to the remaining ink) and every span strictly between the pivots renders identically with no displacement. Walked position by position the way the composed walk does, because a pivot here is a position, not a name — the same after form can stand at one position as the stub-dropped letter and at another as an untouched same-family letter (a second ·May keeping its old loop beside the one that lost its leftover left pixel), and only the before side's named form says which is which. A pivot position is one whose before name carries a before prefix and whose after name carries an after prefix; it is judged as the compact with its placement moved by the declared count and its origin standing still, and every span between pivots must render identically with no displacement. Anything the contract cannot hold — no pivot position at all, a shaped run that contradicts the unit's recorded glyphs, sides that spell different letters, an off-grid placement, a non-rectilinear outline, a dropped cell outside the named columns — reads as no match, so the unit queues."""
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
            _entry_drop_holds({"after": {"entry_drop": columns}}, before, after, intern) is None
        ):
            return False
        before_span, after_span = [], []
    return _span_settled(intern, before_span, after_span, 0)


def _matches_stub_drop(match, unit, excluded, context=None):
    """A letter that gives up a named left-side stub while its remaining ink stays put, matched at the rendered-pixel grain: the old-font pivot form gives way to a named new form whose own-frame picture is the old one compacted left by the declared column count, origin standing still, placement moving right by that count, everything else in the window unmoved. Placement moving with the compact is what distinguishes this from an entry drop (whose remaining ink slides closer and whose placement stands still). Any other ink change anywhere in the window fails this match closed — which is where the composed reading picks up. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the slide and ink-gain shapes do."""
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
    """The stub-dropped shape's own coherence, checked once at load: the drop must actually move, it must be a shortening, and every pivot form named on either side must belong to one family — a stub-drop rule speaks for one letter's lost left-side pixel, so a second family in the lists could only be a paste error."""
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
    """The entry-shortening shapes' shared coherence, checked once at load: the drop must actually move (zero columns is the identity, which is machine-approved already, so a rule declaring it could only mask a typo), it must be a shortening (the lost stretch sits the letters closer, never further), and every pivot form named on either side must belong to one family — an entry rule speaks for one letter's lost left-side stretch, so a second family in the lists could only be a paste error."""
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
    """The entry-contracted shape's pair-specific coherence, layered on the shared entry-shortening checks."""
    _validate_entry_drop(rule_id, match)
    left = match["before"]["left"]
    families = _families(left)
    if not all(family.startswith("qs") and "." not in family and "/" not in family for family in families):
        _fail(
            f"rule {rule_id!r}: match.before.left must be a bare Quikscript family name or a list "
            f"of them, got {left!r}"
        )


def _redrawn_trade(match):
    """The named cell trade a redrawn rule blesses: the own-frame cells the after form gives up and the ones it takes on, each as a set of (column, row) pairs."""
    return (
        {tuple(point) for point in match["after"]["dropped"]},
        {tuple(point) for point in match["after"]["added"]},
    )


def _redrawn_holds(match, before, after, intern):
    """Whether one pivot piece is the named redraw: same height, same own-frame origin, both on the grid, and the after picture is the before picture with the named dropped cells gone and the named added cells present — both sets read at one common column offset, derived from the dropped cells themselves rather than declared, because an entry extension inserts a column at the pivot's left edge and carries the whole frame right with it, so an entry-extended variant shows the same trade one column over. Deriving the offset from the losses is what lets an added set be empty at all; the offset a pure loss leaves free is bounded instead by the after form the rule names, which is a settled glyph and not a picture. The offset picture equality is exact: a cell lost or gained anywhere outside the named trade fails, whatever the offset."""
    if before[3] != after[3] or before[4] != after[4]:
        return False
    if before[2] % PIXEL_SIZE or after[2] % PIXEL_SIZE or before[3] % PIXEL_SIZE:
        return False
    painted, kept = intern.cells(before[1]), intern.cells(after[1])
    if painted is None or kept is None:
        return False
    dropped, added = _redrawn_trade(match)
    gone, gained = painted - kept, kept - painted
    if len(gone) != len(dropped) or len(gained) != len(added):
        return False
    offset = min(gone)[0] - min(dropped)[0]
    if {(column + offset, row) for column, row in dropped} != gone:
        return False
    return {(column + offset, row) for column, row in added} == gained


def _contraction_room(before, after):
    """How many columns of entry contraction the after form names beyond the before glyph's — the most a redrawn letter may sit closer to what precedes it, since a seam the old font had already drawn that tight has nothing left to close."""
    return max(
        0,
        _glyph_adjustment(after[0], ENTRY_CONTRACTION) - _glyph_adjustment(before[0], ENTRY_CONTRACTION),
    )


def _pull(before, after, expected, room):
    """How far a pivot's own placement sits ahead of where the walk expects it: zero when it stands exactly there, up to `room` columns closer when its new form pulls its entry in that far, and None anywhere else — off the grid, further left than the contraction can account for, or further right at all."""
    offset = after[2] - before[2] - expected * PIXEL_SIZE
    if offset % PIXEL_SIZE or not -room * PIXEL_SIZE <= offset <= 0:
        return None
    return offset // PIXEL_SIZE


def _redrawn_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is exactly the named trade on the named pivot, re-derived from the fonts: shape the window under one of the unit's configs and walk it position by position the way the composed walk does, because a pivot here is a position, not a name — the same before name can stand at one position as the pivot and at another as the untouched letter (a second ·Eight keeping its normal loop beside the one that pulls in), and only the after side's settled form says which is which. A pivot position is one whose before name carries a before prefix and whose after name carries an after prefix; it is judged as the named redraw at the same height and own-frame origin with its placement carrying the displacement accumulated so far, or up to the entry contraction its new form names closer than that, and every span between pivots must render identically under that displacement, which grows by the declared shift and by whatever the pivot took at each pivot. Anything the contract cannot hold — no pivot position at all, a shaped run that contradicts the unit's recorded glyphs, sides that spell different letters, an off-grid placement, a non-rectilinear outline, a cell traded outside the named sets — reads as no match, so the unit queues."""
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
        if not _redrawn_holds(match, before, after, intern):
            return False
        pull = _pull(before, after, displacement, _contraction_room(before, after))
        if pull is None:
            return False
        displacement += shift + pull
        before_span, after_span = [], []
    return _span_settled(intern, before_span, after_span, displacement)


def _matches_redrawn(match, unit, excluded, context=None):
    """A letter redrawn in place to a named new form, matched at the rendered-pixel grain: the old-font pivot form gives way to a named new form whose own-frame picture is the old one with the named cells traded at one common column offset, the own-frame origin standing still and the placement with it unless the new form names an entry contraction, which lets the letter sit up to that many columns closer to what precedes it and carries the same amount on past it; everything after the pivot slides by the declared count — which may be zero when the new form keeps the pivot's advance, since the trade itself is the change. The added set may be empty, which is a form that only gives ink up: ·Key's foot dropping its terminal pixel and its follower coming a column closer is the pure-loss reading, as distinct from the exit stretch the extension-dropped shape names at the name grain, which cannot see what the rest of the window did. Any other ink change anywhere in the window fails this match closed — which is where the composed reading picks up, so a window this shape refuses only because a second separately-blessed change moved a pixel it has no vocabulary for may still be explained by both rules together. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the slide and ink-gain shapes do."""
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
    """The redrawn shape's own coherence, checked once at load: every pivot form named on either side must belong to one family — a redrawn rule speaks for one letter's new form, so a second family in the lists could only be a paste error — the dropped set has to name at least one cell, since a form that gives up nothing and takes on nothing is not redrawn at all, and the dropped and added sets must not share a cell, since a cell traded for itself names no change either. The added set may be empty: a form that only gives ink up is redrawn as much as one that trades, which is what ·Key's foot losing its terminal pixel before ·May, ·No and ·It looks like at this grain."""
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
    """Glyph indices where the named pair changed its join state: any of the pivot prefixes, the follower's family, the named before seam becoming the named after seam, letter for letter, with the pivot and follower settling into cells the rule names in full and the follower's after cell still that same family's."""
    if not _letter_for_letter(unit):
        return []
    glyphs, seams = unit["before"]["glyphs"], unit["before"]["seams"]
    cells, after_seams = unit["after"]["cells"], unit["after"]["seams"]
    followers = _families(match["before"]["follower"])
    pivots = _families(match["before"]["pivot"])
    seam = match["before"]["seam_out"]
    retarget = match["after"].get("retarget", match["after"].get("joined"))
    reach = min(len(glyphs), len(cells), len(seams) + 1, len(after_seams) + 1) - 1
    return [
        i
        for i in range(reach)
        if any(_is_pivot(glyphs[i], pivot) for pivot in pivots)
        and _family(glyphs[i + 1]) in followers
        and seams[i] == seam
        and after_seams[i] == retarget
        and cells[i] in match["after"]["pivot_cells"]
        and cells[i + 1] in match["after"]["receiver_cells"]
        and _cell_rune(cells[i]) == _family(glyphs[i])
        and _cell_rune(cells[i + 1]) == _family(glyphs[i + 1])
    ]


def _retarget_piece_holds(before, after, reach=0):
    """Whether one piece of a newly joined or retargeted pair kept its own-frame origin — or moved it left by exactly the columns the caller declares its new form reaches back — both on the grid. Height and picture may change; that is the shapes' point, with both letters free to redraw. Placement under a running displacement is the walk's job, not this contract's."""
    if before is None or after is None:
        return False
    if after[4] != before[4] - reach * PIXEL_SIZE:
        return False
    return before[2] % PIXEL_SIZE == 0 and after[2] % PIXEL_SIZE == 0 and before[3] % PIXEL_SIZE == 0


def _retarget_geometry(match, unit, comparator, follower_shift, onward, follower_reach=0):
    """Whether the window's rendered before→after change is exactly the named pair gaining a join or changing its join height, re-derived from the fonts: shape the window under one of the unit's configs, find every pivot–follower pair whose recorded seam moved from the named state to the named after seam and whose after cells the rule names, require the pivot to keep its own-frame origin and the follower to keep its own or reach back over its old left edge by the declared columns, require the pivot to keep its column placement and the follower to move by the columns the caller declares — none at all for a retarget whose follower stands still, the declared count for one whose follower comes closer and for every new join — and require each span strictly outside those pairs to render identically once displaced by the cumulative carry — the span before the first pair by nothing, the span after the first pair by what that pair carries onward (the retarget's declared shift, or a new join's shift with its follower's advance delta), and one more of that for every further pair. Anything the contract cannot hold — no pair, a shaped run that contradicts the unit's recorded glyphs, sides that spell different letters, a pivot or follower that moved contrary to the declared shape, an off-grid placement — reads as no match, so the unit queues."""
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
    for index in pairs:
        for piece_index in (index, index + 1):
            before, after = before_pieces.get(piece_index), after_pieces.get(piece_index)
            if before is None or after is None:
                return False
            follows = piece_index == index + 1
            movement = follower_shift if follows else 0
            reach = follower_reach if follows else 0
            if (
                not _retarget_piece_holds(before, after, reach)
                or after[2] != before[2] + movement * PIXEL_SIZE
            ):
                return False
    intern = comparator.intern
    pivots = set(pairs)
    followers = {index + 1 for index in pairs}
    before_span: list = []
    after_span: list = []
    step = 0
    for index in range(len(before_names)):
        if index in pivots:
            if not _span_settled(intern, before_span, after_span, onward * step):
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
    return _span_settled(intern, before_span, after_span, onward * step)


def _matches_join_retarget(match, unit, excluded, context=None):
    """A named join that has changed height, matched at the rendered-pixel grain: the pivot and the follower may both redraw, they keep their own-frame origin, the pivot keeps its column placement, the follower comes the declared number of columns nearer — none at all where the retarget leaves it standing — the named seam becomes the named new height, and everything after the follower sits the declared shift over. An origin unmoved on both letters and a column unmoved on the pivot pin the change to the join's row and the two letters' forms rather than to a slide or a dropped attachment, and any other ink change anywhere in the window fails this match closed — which is where the composed reading picks up, so a window this shape refuses only because a second separately-blessed change moved a pixel it has no vocabulary for may still be explained by both rules together. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the slide and join-dropped shapes do."""
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
    """A named pair — or any of several named pivots into one follower — that has newly joined, matched at the rendered-pixel grain: the pivot and follower may both redraw, the pivot keeps its own-frame origin and the follower either keeps its own or reaches back by the declared columns, the pivot keeps its column placement, the follower moves by the declared shift, everything after it moves by that shift combined with the follower's declared advance delta, and the recorded break becomes the named join height. Those placement constraints distinguish a created join from a retarget, whose follower stands still, and from a general redraw; any other ink change anywhere in the window fails this match closed, which is where the composed reading picks up. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the join-retargeted shape does."""
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
        tuple(_families(match["before"]["follower"])),
        match["after"]["joined"],
        tuple(match["after"]["pivot_cells"]),
        tuple(match["after"]["receiver_cells"]),
        match["after"]["shift"],
        match["after"]["follower_advance"],
        match["after"]["follower_reach"],
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
        )
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_join_retarget(rule_id, match) -> None:
    """The join-retargeted shape's own coherence, checked once at load: both seams must be yK heights and they must differ — a break has nothing to retarget, and a seam that holds its height is the extension shape's subject, not this one's — the cells have to belong to the letters the rule names, and a retarget onto a break is the gap shape's job."""
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
    """The join-created shape's own coherence, checked once at load: the before seam must be a break and the after seam a yK height, because a pair that already joined belongs to the retarget or extension shape, while a new break belongs to the gap shape. The follower's declared reach cannot be negative, since a form that pulls its left edge in is an entry contraction rather than a new join's receiver. The cells have to belong to the letters the rule names, on either side — a rule may name several pivots, which is what lets one letter's new entry be recorded once for every left neighbor that now reaches it."""
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
    named = (
        ("pivot_cells", [_family(name) for name in _families(match["before"]["pivot"])]),
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
    """One position a composable rule was credited at in a composed walk: the rule's id, which shape spoke there (`slide`, `extension`, `gain`, `join`, `entry`, `stub`, `redrawn`, `retarget`, or `joined`), the columns the window's running displacement moves by at that position — the declared slide, minus the extension's column count, the declared join gap, an entry shortening combined with any exit-extension delta on that pivot, the declared created-join shift or the columns a retarget brings its follower nearer, the stub-drop's placement bump (followers unmoved), or the declared ink-gain or redrawn shift — how far the pivot's own placement sits ahead of the span behind it, which only an entry contraction whose after frame did not take it into its origin is ever anything but zero, how much further ahead of that it may sit, which only a redrawn trade whose new form names an entry contraction ever gives room for, whether the event's own contract judged its pivot, which only a created join ever answers no to: one whose pivot moved its origin has judged its follower alone and is an event only behind an entry-contraction event at the same position, and the columns the displacement moves by again once the walk is past the follower, which a created join whose follower redrew to a different advance carries and a retarget carries whatever its declared shift leaves over its follower's own move."""

    rule_id: str
    kind: str
    shift: int
    pivot_judged: bool = True
    lead: int = 0
    advance: int = 0
    room: int = 0


def _shape_of(match):
    """The one delta shape a rule's match declares, read off the `match.after` field that keys it — the reading `load_rules` holds every rule to, so a loaded rule always has exactly one and the flags on its row answer for it. None only for a match that declares no shape at all, which nothing that has been through the loader can be."""
    for shape in SHAPES.values():
        if shape.keyed_by in match["after"]:
            return shape
    return None


def _is_composable(rule):
    """Whether a rule's shape can take part in a composed reading, which its row's `composable` flag answers. What earns a shape that flag is naming a local pixel change the walk can prove — a displacement, a named set of own-frame cells appearing on or traded on the pivot, a named join becoming a gap, a named left-side stretch or stub the pivot gives up, or a named join changing height — so a walk across a window can carry it. A shape that reads a whole window's name-grain structure, or its whole ink change byte for byte, says nothing about any one position and so has nothing to contribute to a walk."""
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
    """A hashable identity for a set of composable rules — each one's id with what it matches on — so a composed reading memoized against one rules file is never served to another: the memo lives on the context, a caller may hold two rule sets against one context, and the memoized value names rule ids, so two sets that match alike under different ids must not share an entry either."""
    return tuple((rule["id"], json.dumps(rule["match"], sort_keys=True)) for rule in rules)


def _candidates(match, unit):
    """The window positions one composable rule could speak for, read off the index record before anything is shaped: a slide, ink-gain, entry-drop, stub-drop, or redrawn rule's are the glyphs whose recorded before name carries one of its pivot prefixes; an entry-contracted rule additionally requires one of the named families immediately on the pivot's left; a join-dropped rule's are the positions where the named pivot's recorded seam into the named follower dropped from the named height to a break; a join-retargeted or join-created rule's are the positions where the named pivot's recorded seam into the named follower moved from the named before state to the named new height and both after cells the rule names; an extension rule's are the positions meeting every per-position precondition the single-rule matcher reads — the named drop (an `ex-ext-N` on the before glyph, or an `ex-con-N` on the after cell whose before glyph never carried an exit extension), the named seam standing still at that position on both sides, the pivot and follower after cells, and the follower's own family answering for its own cell — and none at all unless the named seam is a yK height, since the walk has to know which row a dropped tail sits on. Deliberately name-grain and cheap, because this is the pre-gate that decides whether a window is worth shaping at all: a rule with no candidate here can never be credited, and a window where fewer than two rules have one is never shaped."""
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
    mb, ma = match["before"], match["after"]
    if not SEAM_ROW.fullmatch(mb["seam_out"]):
        return []
    seams, after_seams = unit["before"]["seams"], unit["after"]["seams"]
    cells = unit["after"]["cells"]
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


def _pieces_by_glyph(names, run):
    """Each glyph position of a shaped run mapped to its ink piece, by walking the names and consuming the run's pieces in order: an inkless glyph — a space, a ZWNJ, an empty marker — draws nothing and is simply absent, which is what lets a marker ride through a window without ever being an event. None when the pieces are not all consumed, the one way the two can disagree, which a caller reads as no picture claim being possible."""
    pieces = {}
    index = 0
    for position, name in enumerate(names):
        if index < len(run) and run[index][0] == name:
            pieces[position] = run[index]
            index += 1
    return pieces if index == len(run) else None


def _slide_event(match, rule_id, index, after_names, before_pieces, after_pieces):
    """Whether one slide candidate's own contract holds at the rendered grain, one position at a time and in `_slide_geometry`'s own reading: the after side settles into one of the rule's named after forms, and the pivot keeps its exact shape at its exact height with its own-frame origin displaced by exactly the declared column count, which pins the mechanism to the pivot's sidebearing rather than to drift that happens to land the same pixels. None when it does not hold, which leaves the piece to be judged as ordinary span ink."""
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
    """Whether one ink-gain candidate's own contract holds at the rendered grain, one position at a time: the after side settles into one of the rule's named after forms, the pivot keeps its horizontal own-frame origin, and its placed old picture stands still inside the after frame plus exactly the named cells — no cell lost, no unnamed cell gained, though the frame may extend vertically around it. Horizontal placement under the running displacement is the walk's job, not this contract's, mirroring `_slide_event` leaving the span equality to the walk. None when any of that fails, which leaves the piece to be judged as ordinary span ink."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    if not _gain_holds(match, before, after, intern):
        return None
    return Event(rule_id, "gain", match["after"]["shift"])


def _entry_event(match, rule_id, index, after_names, intern, before_pieces, after_pieces):
    """Whether one entry-shortening candidate's own contract holds at the rendered grain, one position at a time: the after side settles into one of the rule's named after forms, and `_entry_drop_holds` proves either the fixed-origin entry drop or the named entry contraction and says how far the pivot's own placement sits ahead of the span behind it. Placement under the running displacement is the walk's job, not this contract's, mirroring `_gain_event` leaving the span equality to the walk. None when any of that fails, which leaves the piece to be judged as ordinary span ink."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    lead = _entry_drop_holds(match, before, after, intern)
    if lead is None:
        return None
    return Event(rule_id, "entry", _entry_shift(match, before[0], after[0]), lead=lead)


def _stub_event(match, rule_id, index, after_names, intern, before_pieces, after_pieces):
    """Whether one stub-drop candidate's own contract holds at the rendered grain, one position at a time: the after side settles into one of the rule's named after forms, and the pivot keeps its height and own-frame origin while its after picture is its before picture compacted left by the declared column count. Placement under the running displacement is the walk's job, not this contract's. None when any of that fails, which leaves the piece to be judged as ordinary span ink."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    columns = match["after"]["stub_drop"]
    if _entry_drop_holds({"after": {"entry_drop": columns}}, before, after, intern) is None:
        return None
    return Event(rule_id, "stub", columns)


def _redrawn_event(match, rule_id, index, after_names, intern, before_pieces, after_pieces):
    """Whether one redrawn candidate's own contract holds at the rendered grain, one position at a time: the after side settles into one of the rule's named after forms, and the pivot keeps its height and own-frame origin while its after picture is its before picture with the named cells traded at one common column offset. Placement under the running displacement is the walk's job, not this contract's, mirroring `_entry_event` leaving the span equality to the walk, and so is how much of the entry contraction the new form names the pivot takes there — the contract says only how much room there is for it. None when any of that fails, which leaves the piece to be judged as ordinary span ink."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    if not _redrawn_holds(match, before, after, intern):
        return None
    return Event(rule_id, "redrawn", match["after"]["shift"], room=_contraction_room(before, after))


def _extension_event(match, rule_id, index, intern, before_pieces, after_pieces, cell):
    """Whether one extension candidate's own contract holds at the rendered grain: the pivot stands on the grid at the same height on the same own-frame origin and draws the same picture minus a tail, where the tail is every cell the after form has given up, each of them past the after form's rightmost column, on the very row the named seam holds, and exactly as many columns wide as the pivot gave up — the named extension, less the shorter one its after cell keeps when it keeps one, or the named contraction in full. The grid check is the pivot's own because it is the one piece no span ever pictures — `_span_cells` refuses an off-grid placement everywhere else — and the seam row is read by dividing its height by the pixel size, which only names the right row on the grid. The follower has to exist as ink on both sides so the walk can skip it or chain it, but its picture is not this contract's: the rule already named the after cell, and a named redraw (·May losing the stacked entry, ·I's smaller loop) is the rule's own subject. None when any of that fails, which leaves both pieces to be judged as ordinary span ink."""
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
    """Whether one join-dropped candidate's own contract holds at the rendered grain: the pivot keeps its exact shape at its exact height with its own-frame origin unmoved, on the grid, and the follower exists as ink on both sides. Placement under the running displacement is the walk's job, not this contract's, mirroring `_slide_event` leaving the span equality to the walk. The follower's picture is not this contract's either: when the follower is itself an event the next event judges it, and when it is not the walk puts it in the next span, so a redrawn follower that is not an event still fails as unexplained span ink. None when any of that fails, which leaves both pieces to be judged as ordinary span ink."""
    if not _join_piece_holds(before_pieces.get(index), after_pieces.get(index)):
        return None
    if before_pieces.get(index + 1) is None or after_pieces.get(index + 1) is None:
        return None
    return Event(rule_id, "join", match["after"]["gap"])


def _retarget_event(match, rule_id, index, before_pieces, after_pieces):
    """Whether one join-retargeted candidate's own contract holds at the rendered grain: both the pivot and the follower keep their own-frame origin, both on the grid. Height and picture may change. Placement under the running displacement is the walk's job, not this contract's, mirroring `_join_event` leaving the span equality to the walk. The event carries the two counts the walk needs apart — how far the follower itself comes, which is nothing for a retarget that leaves it standing, and how much further the displacement moves once the walk is past it. None when any of that fails, which leaves both pieces to be judged as ordinary span ink."""
    if not _retarget_piece_holds(before_pieces.get(index), after_pieces.get(index)):
        return None
    if not _retarget_piece_holds(before_pieces.get(index + 1), after_pieces.get(index + 1)):
        return None
    follower_shift = match["after"]["follower_shift"]
    return Event(rule_id, "retarget", follower_shift, advance=match["after"]["shift"] - follower_shift)


def _created_join_event(match, rule_id, index, before_pieces, after_pieces):
    """Whether one join-created candidate's own contract holds at the rendered grain: the follower keeps its own-frame origin or reaches back by the columns the rule declares, on the grid, and the pivot keeps its own. Height and picture may change. A pivot that exists as ink on both sides but moved its origin comes back with `pivot_judged` false rather than as no event: the walk honors that only behind an entry-contraction event at the same position — the contraction has judged the pivot's entry and origin, so the created join needs only its follower — and drops it otherwise, so a redrawn pivot never rides through a created join alone. Placement under the running displacement is the walk's job, with the pivot standing under the old displacement, the follower leading the new one, and the follower's advance delta carried on past it. None when the follower fails or the pivot draws nothing, which leaves both pieces to be judged as ordinary span ink."""
    if not _retarget_piece_holds(
        before_pieces.get(index + 1), after_pieces.get(index + 1), match["after"]["follower_reach"]
    ):
        return None
    if before_pieces.get(index) is None or after_pieces.get(index) is None:
        return None
    pivot_judged = _retarget_piece_holds(before_pieces[index], after_pieces[index])
    return Event(
        rule_id, "joined", match["after"]["shift"], pivot_judged, advance=match["after"]["follower_advance"]
    )


def _span_settled(intern, before_span, after_span, displacement, after_anchor=None):
    """Whether one span between events renders as the same picture once displaced: the union of the before pieces' cells at their placements, moved by the displacement the walk has accumulated so far, must equal the after pieces' union exactly. When an already-validated after piece anchors both unions, cells handed invisibly between it and the span do not become a second change. A span or anchor the walk cannot picture — a non-rectilinear outline, an off-grid placement — refuses, so a window no cell reading can be made of never composes."""
    painted = _span_cells(intern, before_span)
    rendered = _span_cells(intern, after_span)
    anchored = set() if after_anchor is None else _span_cells(intern, [after_anchor])
    if painted is None or rendered is None or anchored is None:
        return False
    displaced = {(column + displacement, row) for column, row in painted}
    return displaced | anchored == rendered | anchored


def _span_compacted(intern, before_span, after_span, displacement, columns):
    """Whether one span is the displaced before picture compacted left by `columns`: the leftmost that many columns of ink are gone and the remaining cells have shifted left by the same count. That is the stacked entry coming off the named extension follower, which `_span_settled` cannot see because it is not a translation — the left edge stays and the right edge moves in."""
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
    """Whether one span is accounted for: a pure translation under the standing displacement, optionally unioned on both sides with an already-validated after piece; or that picture compacted left by a dropped entry extension; or — for an extension's named follower — the span without that follower as a translation, which is the named-cell blessing when the follower redrew in some other way."""
    if _span_settled(intern, before_span, after_span, displacement, after_anchor=after_anchor):
        return True
    if after_anchor is not None:
        return False
    if compact and _span_compacted(intern, before_span, after_span, displacement, compact):
        return True
    if skippable and before_span:
        return _span_settled(intern, before_span[1:], after_span[1:], displacement)
    return False


def _composed_walk(rules, unit, context):
    """The composed reading itself, re-derived from the surface's own fonts: shape both sides of the window, hold each shaped run against what the index recorded, evaluate every composable rule's candidates at the rendered grain, and walk the window left to right carrying a running column displacement — each span between events judged as a picture under the displacement standing when it began, each event judged as its own contract plus a placement offset, and each event's pivot (a slide's) or follower (an extension's or a join-dropped's — or that follower itself as the next event, when it is one) or next glyph (an ink-gain's, an entry shortening's, or a redrawn trade's) leading the next span under the new displacement. A retargeted pair keeps its pivot under the standing displacement and brings its follower the columns it declares nearer before the declared shift leads the following span; a newly joined pair keeps its pivot under the standing displacement while its follower leads the new one and hands the follower's advance delta on to whatever comes after it — and when that pivot is an entry-contraction event's, or is a retarget event's follower, the created join chains behind it, the earlier event judging the pivot and the join judging only the follower, which then leads by both shifts combined, while a retarget, an exit-extension drop or a redrawn trade whose pivot is a created join's follower chains the other way round, the join leading that letter and the later event carrying the glyphs after it by both shifts and the advance delta — which a created join chained behind a contraction hands on the same way, so all three can stand in one window. An extension follower's span may also compact left by a dropped entry extension, so the stacked entry coming off ·May is the same seam as ·It's dropped tail, not a third change. A candidate whose own contract fails is simply not an event and its ink is judged as ordinary span ink, so adding a rule to the file can never un-explain a window that was explained without it; two rules claiming one position, or a join-retargeted or join-created event whose follower position is itself claimed, is ambiguous and refuses outright — the chains behind a contraction, behind a retarget and behind a created join being the sanctioned shares, and a created join whose pivot moved its origin being no event at all without a contraction under it. A join-dropped or extension event whose follower is itself an event chains: the follower is the next event under the new displacement rather than consumed. Returns each credited rule's event positions, or None when no such reading of the window exists. It carries no arity threshold of its own — a one-rule reading is a real reading of a window, and it is `_composed` that requires two — which is what lets it be held directly against each single-shape matcher."""
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
                    match, rule["id"], index, after_names, intern, before_pieces, after_pieces
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
    for index, claims in found.items():
        judged = [claim for claim in claims if claim.pivot_judged]
        unjudged = [claim for claim in claims if not claim.pivot_judged]
        if len(judged) > 1:
            return None
        if not judged:
            continue
        events[index] = judged[0]
        if unjudged and judged[0].kind == "entry":
            if len(unjudged) > 1:
                return None
            chained[index] = unjudged[0]
    behind_retarget = {
        index + 1
        for index, event in events.items()
        if event.kind == "retarget" and index + 1 in events and events[index + 1].kind == "joined"
    }
    behind_join = {
        index + 1
        for index, event in list(events.items()) + list(chained.items())
        if event.kind == "joined"
        and index + 1 in events
        and events[index + 1].kind in ("retarget", "extension", "redrawn")
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
                    carried = joined.advance
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
            displacement += event.advance
            before_span, after_span = [], []
            index += 2
            joined = events.get(position + 1) if position + 1 in behind_retarget else None
            if joined is not None:
                displacement += joined.shift
                if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                    return None
                credited.setdefault(joined.rule_id, []).append(position + 1)
                if index in behind_join:
                    carried = joined.advance
                else:
                    displacement += joined.advance
                    index += 1
        elif event.kind == "joined":
            if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                return None
            displacement += event.shift
            if after_pieces[index + 1][2] != before_pieces[index + 1][2] + displacement * PIXEL_SIZE:
                return None
            before_span, after_span = [], []
            if index + 1 in behind_join:
                carried = event.advance
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
                before_span, after_span = [follower_before], [follower_after]
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


def _composed(rules, unit, context):
    """The composed reading a fill may be written from: the name-grain pre-gate first, where two or more rules must have a candidate or the window is never shaped at all; then the walk, memoized per (rules, unit) so a window is shaped once however many times it is asked about; then the two-rule threshold, because a window one rule accounts for on its own belongs to that rule's own line and not to a composition. Returns each credited rule's event positions before any guard is read, since the guards are scoped per credited rule and the caller has to know which positions earned the credit."""
    if not unit.get("before") or not unit.get("after"):
        return None
    if sum(1 for rule in rules if _candidates(rule["match"], unit)) < 2:
        return None
    key = (_composable_digest(rules), unit["id"])
    if key not in context.composed:
        context.composed[key] = _composed_walk(rules, unit, context)
    events = context.composed[key]
    return events if events is not None and len(events) > 1 else None


def _composed_held(rules, unit, events, context):
    """Whether any rule's except_left guard refuses this window, each read in the scope its shape's row declares: a credited window-scope rule's guard reads the whole window, exactly as the single-rule shape does, because such a shape bounds nothing to its left; a credited left-neighbor-scope rule's reads only the left neighbor of each position it was credited at, again exactly as the single-rule shape does; and a rule that took no part in the walk but whose own matcher accepts the window unguarded and refuses it guarded holds it too, since that rule would have held the window in the single-rule pass and a composition must not lift a hold. A refusal holds the whole unit rather than dropping the one rule's credit, which is the file's standing principle that a guarded context never rides along beside an unguarded one."""
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
    """The verdict and note one composed fill carries: the weakest verdict over the credited rules and over every non-composable rule whose own matcher accepts the window as well, since a window some blessed-either rule also speaks for cannot be approved outright on the strength of the others. The note names the credited ids in rules-file order and joins their own notes in the same order, and says which rule outside the credited set weakened the verdict when one did."""
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
    ids = " + ".join(rule["id"] for rule in credited)
    note = f"[standing: {ids}] " + "; ".join(rule["note"] for rule in credited)
    return verdict, note + (f" (either: {weakened})" if weakened else "")


class SlideContext:
    """The font-backed state the slide shape and the composed reading match with: one InkComparator over the surface's shipped font pair, a per-run memo of each rule's geometric verdict per unit so the guarded and unguarded passes over one rule shape a window once, and a second memo of each composed walk per unit, keyed on the composable rules' ids and matches so a window is shaped once however many times the same rules ask about it and a caller holding a second rule set against the same context is never served the first set's reading."""

    def __init__(self, before_font, after_font) -> None:
        self.comparator = InkComparator(before_font, after_font)
        self.memo: dict[tuple, bool] = {}
        self.composed: dict[tuple, dict[str, list[int]] | None] = {}


class Shape(NamedTuple):
    """One expressible delta shape: the match.after field that declares it, the field names match.before and match.after must carry exactly (an empty tuple means the block itself must be absent), which of those fields are lists of cell strings, of delta digests, or of glyph-name prefixes — or integer column counts, or a family name that may also be a list of them — rather than plain scalars, the matcher that reads a unit for it, and its own coherence check. The row also carries the three facts a run has to know about a shape before it reads any unit — whether it can take part in a composed reading, whether it re-shapes windows in the surface's own font pair, and whether it reads the surface's persisted ink deltas, which are independent of one another — and the scope its except_left guard is read in when a composed reading credits it: the whole window, or the left neighbor of each credited position, and None for a shape that never composes."""

    keyed_by: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    cell_lists: tuple[str, ...]
    matcher: Callable[[dict, dict, set[str], "SlideContext | None"], bool]
    validate: Callable[[str, dict], None] | None = None
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
        cell_lists=(),
        matcher=_matches_join_dropped,
        validate=_validate_join_dropped,
        int_fields=("gap",),
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
        name_lists=("pivots",),
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
        int_fields=("shift", "follower_advance", "follower_reach"),
        family_fields=("pivot", "follower"),
        composable=True,
        font_backed=True,
        needs_ink_deltas=True,
        guard_scope="window",
    ),
}


def _shape_names(chosen, conjunction):
    """The delta shapes one of the Shape rows' flags picks out, named in the order SHAPES declares them and joined for prose, so a message about what a run needs of the surface is read off the rows rather than typed out beside them."""
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
    """Whether a rule's except_left guard can refuse anything at all. `guard` reaches `_matches` through exactly one expression — the `excluded` set — so a match naming no except_left families makes the guarded and unguarded passes the same pure function of the same arguments, and a held set defined as matching unguarded while refusing guarded is empty by construction. Every caller that skips a guard pass rests on that identity, which is why the predicate lives here beside `_matches` rather than inline at any one call site: a future shape that lets `guard` reach anything beyond `excluded` has one place to falsify."""
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
    """What one rule reached on a run: the unit ids its own matcher spoke for, split into the blanks it filled and the ones a verdict already covers, the ids its except_left held back, and — as their own numbers rather than folded into the rest — how many units a composed reading credited it at and how many composed lines those spread over. Composed credit has to stand apart because the composed pass claims a window before any single rule is asked about it, so a rule that only ever earns composed credit shows nothing at all on its own line and is not thereby dead. Over a run narrowed by `open_units`, `verdicted` holds exactly the disputed matched units the tripwire names, since no accepted one was offered."""

    filled: list[str]
    verdicted: list[str]
    held: list[str]
    composed_credit: int
    composed_lines: int


class Run(NamedTuple):
    """One pass of the standing approvals over a surface: the fill records to write, the composed pass's counts per credited-id tuple in rules-file order (filled, already verdicted, held), and each rule's Reach by id."""

    fills: list[dict]
    composed_counts: dict[tuple[str, ...], list[int]]
    reaches: dict[str, Reach]


class Composed(NamedTuple):
    """What the composed reading decided about one window: the credited rule ids in rules-file order, whether a guard holds the whole unit, and — for a window no guard holds — the verdict and note a fill of it carries. A held window's verdict is never computed, because nothing can write it."""

    credited: tuple[str, ...]
    held: bool
    verdict: str | None
    note: str | None


class Decision(NamedTuple):
    """Everything a run needs to know about one unit, and nothing the verdict store decides: the composed reading when one claims the window, else which rules' own matchers accept it and which rules' except_left hold it. A claimed window carries no per-rule answers, because the single-rule pass never sees it."""

    composed: Composed | None
    matched: frozenset[str]
    held: frozenset[str]


def unit_key(unit, family_digests) -> str | None:
    """The memo key of one unit, or None for a unit the build never stamped, which is evaluated every pass and never stored. The build's `content_key` pins the window, its configs, both fonts' rendered names, the settled cells and seams, and the judged adjacency; `ink_deltas` rides beside it because the stamp leaves it out as derived presentation while the ink-delta shape reads it directly; and the after font's compiled-glyph digest for every family the after cells name pins the outlines, advances and cursive anchors every font-backed shape re-shapes the window through — the review unit cache's grain, so a drawing change reaches exactly the windows that can feel it and no other. The before font is wholesale in the memo's stamp. Truncated to thirty-two hex characters, since it is written once per human unit and sixty-four bits over the corpus is far from any collision."""
    stamp = unit.get("content_key")
    if not stamp:
        return None
    runes = sorted({_cell_rune(cell) for cell in (unit.get("after") or {}).get("cells") or ()})
    lines = [stamp, json.dumps(unit.get("ink_deltas"), sort_keys=True)]
    lines += [f"{rune}\t{family_digests.get(rune, '-')}" for rune in runes]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:32]


def memo_code_paths(root=ROOT) -> list[pathlib.Path]:
    return [pathlib.Path(root) / relative for relative in MEMO_CODE_MODULES]


def memo_environment(rules_path, surface, root=ROOT) -> tuple[str, dict[str, str]]:
    """The memo's whole-store stamp and the after font's per-family digests the unit keys cite. Any line moving drops the memo entirely: the rules file by raw bytes, because a reworded `note` changes every fill quoting it; the deciding code (`memo_code_paths`); the before font wholesale, the after font's family-blind remainder (its helper glyphs, cmap and GPOS wiring), and `uv.lock`, which pins the HarfBuzz the shaper is. A surface carrying no fonts stamps the sentinel for both, which is the regime where no rule can shape a window at all."""
    before_font = pathlib.Path(surface) / "fonts" / "before.otf"
    after_font = pathlib.Path(surface) / "fonts" / "after.otf"
    family_digests: dict[str, str] = {}
    helpers = "-"
    if after_font.is_file():
        family_digests, helpers = fingerprint.after_font_glyph_digests(after_font)
    lines = [
        f"format\t{MEMO_FORMAT}",
        f"rules\t{fingerprint.file_sha256(pathlib.Path(rules_path))}",
        f"code\t{fingerprint.hash_paths(root, memo_code_paths(root))}",
        f"before_font\t{fingerprint.file_sha256(before_font) if before_font.is_file() else '-'}",
        f"after_helpers\t{helpers}",
        f"lock\t{fingerprint.file_sha256(pathlib.Path(root) / 'uv.lock')}",
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), family_digests


def _decision_record(decision: Decision) -> list:
    composed = decision.composed
    return [
        (
            None
            if composed is None
            else [list(composed.credited), composed.held, composed.verdict, composed.note]
        ),
        sorted(decision.matched),
        sorted(decision.held),
    ]


def _decision_from_record(record: list) -> Decision:
    composed, matched, held = record
    return Decision(
        None if composed is None else Composed(tuple(composed[0]), composed[1], composed[2], composed[3]),
        frozenset(matched),
        frozenset(held),
    )


class Memo:
    """The persisted decisions: one line per unit key under a header carrying the stamp `memo_environment` computes. A memo stamped for any other environment, or unreadable, is an empty one — over-invalidation is the safe direction, and a miss only costs the evaluation the memo would have saved. `write` keeps exactly the entries whose key belongs to a unit on the surface it was asked about, served, fresh, or carried from the file unread, so the file is bounded by the human domain and never sheds an entry a later pass could still serve. Pinned gzip mtime and level 1, like the unit store: written once and read once per pass."""

    def __init__(self, path, environment, family_digests, entries=None) -> None:
        self.path = pathlib.Path(path)
        self.environment = environment
        self.family_digests = family_digests
        self.entries: dict[str, Decision] = entries or {}
        self.fresh: dict[str, Decision] = {}
        self._keys: dict[str, str | None] = {}

    @classmethod
    def open(cls, path, environment, family_digests, *, fresh=False) -> "Memo":
        """The memo on disk, empty when there is nothing usable there or the caller asked to recompute everything (`--fresh-memo`, which still rewrites the file afterward)."""
        entries: dict[str, Decision] = {}
        path = pathlib.Path(path)
        if not fresh and path.is_file():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    header = json.loads(next(stream))
                    if header.get("format") == MEMO_FORMAT and header.get("environment") == environment:
                        for line in stream:
                            key, record = json.loads(line)
                            entries[key] = _decision_from_record(record)
            except OSError, EOFError, ValueError, TypeError, StopIteration:
                entries = {}
        return cls(path, environment, family_digests, entries)

    def key_for(self, unit) -> str | None:
        unit_id = unit["id"]
        if unit_id not in self._keys:
            self._keys[unit_id] = unit_key(unit, self.family_digests)
        return self._keys[unit_id]

    def write(self, units) -> int:
        """Write the memo back, bounded to `units`, and return how many entries it holds."""
        kept: dict[str, Decision] = {}
        for unit in units:
            key = self.key_for(unit)
            if key is None:
                continue
            decision = self.fresh.get(key) or self.entries.get(key)
            if decision is not None:
                kept[key] = decision
        header = {"format": MEMO_FORMAT, "environment": self.environment}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as handle:
            with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0, compresslevel=1) as stream:
                stream.write((json.dumps(header) + "\n").encode())
                for key in sorted(kept):
                    stream.write((json.dumps([key, _decision_record(kept[key])]) + "\n").encode())
        return len(kept)


class Decider:
    """The per-unit pure function behind a run, answered once per unit however many times a run asks — the narrowed pass and the `--require-reach` pass over the whole domain share every answer — and served from the memo when one is open and holds the unit's key. `served`, `computed` and `unkeyed` are the run's own reading of what the memo bought it."""

    def __init__(self, rules, context, memo: Memo | None = None) -> None:
        self.rules = rules
        self.context = context
        self.memo = memo
        self.composable = _composable(rules)
        self._decided: dict[str, Decision] = {}
        self.served = 0
        self.computed = 0
        self.unkeyed = 0

    def evaluate(self, unit) -> Decision:
        """The decision itself, computed: the composed reading first, because it claims a window before any single rule is asked about it, then — for an unclaimed window — each rule's own matcher, and for a guarded rule that refuses, its unguarded form, which is what says the guard held it."""
        composed = None
        if len(self.composable) > 1 and self.context is not None:
            events = _composed(self.composable, unit, self.context)
            if events is not None:
                credited = tuple(rule["id"] for rule in self.rules if rule["id"] in events)
                held = _composed_held(self.rules, unit, events, self.context)
                verdict, note = (
                    (None, None) if held else _composed_verdict(self.rules, unit, events, self.context)
                )
                composed = Composed(credited, held, verdict, note)
        matched: list[str] = []
        held_by: list[str] = []
        if composed is None:
            for rule in self.rules:
                match = rule["match"]
                if _matches(match, unit, context=self.context):
                    matched.append(rule["id"])
                elif not _guard_is_inert(match) and _matches(match, unit, guard=False, context=self.context):
                    held_by.append(rule["id"])
        return Decision(composed, frozenset(matched), frozenset(held_by))

    def decide(self, unit) -> Decision:
        decision = self._decided.get(unit["id"])
        if decision is not None:
            return decision
        key = self.memo.key_for(unit) if self.memo is not None else None
        decision = self.memo.entries.get(key) if self.memo is not None and key is not None else None
        if decision is not None:
            self.served += 1
        else:
            decision = self.evaluate(unit)
            if key is None:
                self.unkeyed += 1
            else:
                self.computed += 1
                assert self.memo is not None
                self.memo.fresh[key] = decision
        self._decided[unit["id"]] = decision
        return decision


def rule_reach(rules, units, records, stamp, context=None, decide=None) -> Run:
    """The whole pass in one place, so the records a run writes and the tally it reports can never disagree about what any rule reached: every unit's decision (`decide`, a `Decider.decide` by default, which is where the memo sits), the composed claims first, because they take a window before any single rule is asked about it, then each rule's own answers over what is left. A caller outside the CLI — the standing probe, or a test holding checked-in rules against a synthetic surface — gets the same numbers the run printed, without re-deriving a single matcher decision."""
    if decide is None:
        decide = Decider(rules, context).decide
    order = {rule["id"]: index for index, rule in enumerate(rules)}
    fills = []
    claimed: set[str] = set()
    credited_units: dict[str, list[str]] = {}
    composed_counts: dict[tuple[str, ...], list[int]] = {}
    matched_by: dict[str, list[dict]] = {rule["id"]: [] for rule in rules}
    held_by: dict[str, list[dict]] = {rule["id"]: [] for rule in rules}
    for unit in units:
        decision = decide(unit)
        composed = decision.composed
        if composed is None:
            for rule_id in decision.matched:
                matched_by[rule_id].append(unit)
            for rule_id in decision.held:
                held_by[rule_id].append(unit)
            continue
        claimed.add(unit["id"])
        for rule_id in composed.credited:
            credited_units.setdefault(rule_id, []).append(unit["id"])
        counts = composed_counts.setdefault(composed.credited, [0, 0, 0])
        if composed.held:
            counts[2] += 1
        elif unit["id"] in records:
            counts[1] += 1
        else:
            counts[0] += 1
            fills.append(
                {"unit": unit["id"], "verdict": composed.verdict, "note": composed.note, "at": stamp}
            )

    reaches: dict[str, Reach] = {}
    for rule in rules:
        matched = matched_by[rule["id"]]
        blanks = [unit for unit in matched if unit["id"] not in records]
        note = f"[standing: {rule['id']}] {rule['note']}"
        for unit in blanks:
            fills.append({"unit": unit["id"], "verdict": rule["verdict"], "note": note, "at": stamp})
        reaches[rule["id"]] = Reach(
            filled=[unit["id"] for unit in blanks],
            verdicted=[unit["id"] for unit in matched if unit["id"] in records],
            held=[unit["id"] for unit in held_by[rule["id"]]],
            composed_credit=len(credited_units.get(rule["id"], ())),
            composed_lines=sum(1 for ids in composed_counts if rule["id"] in ids),
        )

    fills.sort(key=lambda record: record["unit"])
    ordered = sorted(composed_counts, key=lambda ids: [order[rule_id] for rule_id in ids])
    return Run(fills, {credited: composed_counts[credited] for credited in ordered}, reaches)


def open_units(units, records):
    """The units a run can still move, which is the whole of what a report's fill records and its warning are read off: a fill comes only from a blank, and every matcher decision is per-unit pure, so dropping a unit changes no other unit's result — the composed claim included, since a window is claimed on its own contents. The tripwire names only matched units carrying a verdict outside the accepting set, and those come along too. What the full domain buys over this is the already-verdicted column and the reach rollup, both of them readings of the store rather than of the fills."""
    return [
        unit
        for unit in units
        if unit["id"] not in records or records[unit["id"]]["verdict"] not in ACCEPTING_VERDICTS
    ]


def _count(number, noun):
    """`1 composed line`, `3 composed lines` — the report says its numbers in prose, and a plural s is the whole of the grammar it needs."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _own_line_total(reach):
    """Everything one rule's own report line accounts for: what it filled, what a verdict already covered, and what its except_left held."""
    return len(reach.filled) + len(reach.verdicted) + len(reach.held)


def _tally_line(name, filled, verdicted, held, open_only):
    """One report line for a rule or a credited tuple. Under a narrowed run the already-verdicted column is absent rather than zero, because the run was never offered the units it would have counted."""
    column = "" if open_only else f"{verdicted} already verdicted, "
    return f"  {name}: {filled} filled, {column}{held} held for review by except_left"


def _tally_lines(rules, run, open_only):
    """A line per rule in rules-file order, then a line per composed reading, which is what the run is read off at a glance."""
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
    """Every rule's whole reach in one block — what its own line accounts for, what composed lines credited it, the two together, and how many composed lines it appears on — followed by a loud line for each rule that reached nothing at all, which a block of zeros states too quietly to notice."""
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
    """Matched units carrying a verdict outside the accepting set, named on one line when there are any and silent when there are none: a standing rule reaching a window the user judged some other way is what an over-broad rule looks like from the outside, and the run says so where it cannot be missed. The blessed set is wider than the set a rule may write, because `identical` accepts the new rendering just as approve and either do — the reviewer merely found the highlighted portion visually unchanged — and a rule agreeing with such a verdict is no accident at all."""
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
    """The one typo a reached-nothing line cannot see: an except_left family no window on this surface joins from. Such a rule goes on matching everything it always did — it is only the guard that is dead, and silently — so this is informational and never a refusal. It is not a reading of how much a guard held, either: a live guard that legitimately holds nothing on this pass says nothing here, because the family it names is still one the surface's windows carry."""
    joining = {
        _joining_family(name) for unit in units for name in (unit.get("before") or {}).get("glyphs") or ()
    }
    return [
        f"  except_left vocabulary: {rule['id']} guards against {family}, which no window on this surface "
        "joins from — the rule matches exactly what it always did, and its guard simply has nothing here "
        "to hold."
        for rule in rules
        for family in rule["match"].get("except_left", [])
        if family not in joining
    ]


def _explain_lines(rule_id, reach, records):
    """One rule's matched unit ids in the three columns its own report line counts: the blanks it filled, the ones a verdict already covers with that verdict named, and the ones its except_left held. On a caught-up store a rule's whole reach sits in the middle column, whose ids nothing else emits."""
    verdicted = [f"{unit_id} ({records[unit_id]['verdict']})" for unit_id in reach.verdicted]
    return [
        f"  explain {rule_id}:",
        f"    filled ({len(reach.filled)}): {' '.join(reach.filled) or 'none'}",
        f"    already verdicted ({len(verdicted)}): {' '.join(verdicted) or 'none'}",
        f"    held by except_left ({len(reach.held)}): {' '.join(reach.held) or 'none'}",
    ]


def main(argv=None, *, units=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument(
        "verdicts", help="the verdicts file that defines blankness (an export or the autosave)"
    )
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--rules", default=str(RULES))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument(
        "--explain",
        metavar="RULE",
        help="also print this rule's matched unit ids, split into the blanks it filled, the ones a verdict already covers, and the ones its except_left held",
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
        help="persist every unit's decision here, keyed on the unit's content key, its ink deltas and the after font's digests for the families its window names, under a stamp over the rules file's bytes, the deciding code and the fonts; a later run with the same stamp evaluates only the units whose key is new. The fills and the report are byte-identical served or computed. The verdict chain passes this; a dry run against candidate rules leaves it off so it never overwrites the chain's memo with another rules file's decisions.",
    )
    parser.add_argument(
        "--fresh-memo",
        action="store_true",
        help="with --memo: evaluate every unit regardless of what the memo holds, and rewrite it",
    )
    args = parser.parse_args(argv)
    if args.fresh_memo and args.memo is None:
        parser.error("--fresh-memo needs --memo")
    if args.open_only and args.explain is not None:
        parser.error(
            "--explain reads the whole domain's already-verdicted column and cannot be combined with --open-only"
        )

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
    units = [
        unit
        for unit in (load_units(surface) if units is None else units)
        if not unit.get("no_verdict") and unit.get("batch") is not None and unit.get("render_groups") == 1
    ]
    composable = _composable(rules)
    declared = [shape for shape in (_shape_of(rule["match"]) for rule in rules) if shape is not None]
    wants_deltas = any(shape.needs_ink_deltas for shape in declared) or len(composable) > 1
    # The index record always carries the key, and carries None exactly when the shard had no ink_deltas field at all — which is what "predates the emission" means here.
    if wants_deltas and not any(unit.get("ink_deltas") is not None for unit in units):
        raise SystemExit(
            "the surface carries no ink_deltas fields, so it predates the "
            f"{_shape_names(lambda shape: shape.needs_ink_deltas, 'and')} shapes; such a rule cannot "
            "match anything on it — rebuild the surface (make review-cycle) first"
        )
    context = None
    if any(shape.font_backed for shape in declared) or len(composable) > 1:
        before_font, after_font = surface / "fonts" / "before.otf", surface / "fonts" / "after.otf"
        if not (before_font.is_file() and after_font.is_file()):
            raise SystemExit(
                f"a {_shape_names(lambda shape: shape.font_backed, 'or')} rule, and any composed reading "
                "two or more composable rules could earn, re-shape their candidate windows in the "
                "surface's own font pair, and this surface carries no fonts/before.otf + fonts/after.otf "
                "— rebuild the surface (make review-cycle) first"
            )
        context = SlideContext(before_font, after_font)

    memo = None
    if args.memo is not None:
        environment, family_digests = memo_environment(pathlib.Path(args.rules), surface)
        memo = Memo.open(args.memo, environment, family_digests, fresh=args.fresh_memo)
    decider = Decider(rules, context, memo)

    candidates = open_units(units, records) if args.open_only else units
    run = rule_reach(rules, candidates, records, manifest["generated_at"], decide=decider.decide)

    # Reach is a reading of the surface rather than of the queue, so --require-reach judges it over the whole human domain against an empty store: a rule every one of whose windows a human already verdicted has still reached them, and a narrowed run would have been offered none of them.
    reaches = run.reaches
    if args.require_reach:
        reaches = rule_reach(rules, units, {}, manifest["generated_at"], decide=decider.decide).reaches

    lines = []
    if memo is not None:
        held = memo.write(units)
        lines.append(
            f"  memo: served {decider.served}, computed {decider.computed}, unkeyed {decider.unkeyed}; "
            f"{memo.path.name} holds {held} {'entry' if held == 1 else 'entries'}"
        )
    lines += _tally_lines(rules, run, args.open_only)
    if args.require_reach or not args.open_only:
        lines += _rollup_lines(rules, reaches)
    lines += _tripwire_lines(run.reaches, records)
    lines += _vocabulary_lines(rules, units)
    if args.explain is not None:
        lines += _explain_lines(args.explain, run.reaches[args.explain], records)

    payload = {
        "format": "ams-review-verdicts/1",
        "manifest_generated_at": manifest["generated_at"],
        "exported_at": manifest["generated_at"],
        "verdicts": run.fills,
    }
    out = pathlib.Path(args.out)
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
