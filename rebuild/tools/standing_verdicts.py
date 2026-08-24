"""Apply the checked-in standing approvals (rebuild/standing-approvals.yaml) to the live review surface: for every rule, find the blank human units whose before→after delta matches the rule's pattern and emit fill records for them into an importable verdicts file. Five delta shapes are expressible, and a rule declares exactly one of them — which one is keyed by the field its `match.after` carries. The `ligature` shape is a pivot letter whose backward join drops as it ligates with its follower; it holds the seams flanking the delta fixed. The `follower_cells` shape is a pivot letter that gives up a named stretch of exit — the whole of a named `ex-ext-N` the before glyph carried, or the columns down to a shorter one its after cell keeps, or the columns a named `ex-con-N` on the after cell pulls back from a default that never carried an exit-extension token: the two sides must line up letter for letter over an identical seam vector, the follower must be one of the families the rule names, the pivot and the follower must settle into cells the rule names in full — rune, stance, entry, exit and the whole adjustment set, which is what says how much of the stretch went — and the unit's own primary judged adjacency must be exactly that pivot–follower seam with no secondary seam anywhere else in the window. That last requirement is the load-bearing one, because an unchanged seam vector is not unchanged ink: a window can hold every seam still and be asking about a different letter's stroke entirely, and only the surface's own judgment fields say which letter the unit is about. The `ink_deltas` shape works from the opposite end and is ink-exact rather than structural: it names the surface's own per-config localized ink-delta digests (rebuild/review/ink.py's `delta_digest`, persisted on every unit), so a unit matches only when the window's entire before→after ink change, under every config it diverges on, is byte-identical to a blessed delta — every structural difference the unit still carries is then name-grain only, and any extra ink anywhere fails the match closed. The `slide` shape judges the rendered pixels rather than either grain of names: it re-shapes the window in the surface's own font pair and matches when the whole visible change is its named pivot letter and everything after it sliding by a declared column count — which is what lets it survive a union-invisible name-grain re-spelling riding along in the same window, the composition that mints a fresh whole-window digest and orphans an ink-delta rule. The `gained` shape judges the same rendered pixels for a letterform that keeps cells the old font omitted: the old-font pivot form gives way to a named new form that is the same picture plus a named set of own-frame cells, every other pixel in the window standing still — so a window whose only change is ·Roe keeping the baseline bar the old shortened-bottom form dropped matches, and a window that also carries a blessed slide still needs the composed reading. Each shape's own docstring states exactly what it proves, and none claims to bound the window beyond that. Above the five sits a reading no rule declares — the composed one, which runs first and asks whether two or more rules together account for every rendered pixel of one window. The founding example makes it unavoidable: a window where the grounded ·See slides a column closer to what precedes it *and* ·J'ai gives up its exit extension carries two separately-blessed changes at once, and neither rule can speak for it alone — the slide shape fails closed on the extension pixel, the extension shape is structurally blind to ink outside its judged seam. Only the slide, extension-dropped, and ink-gain shapes compose, because they name a local pixel change the walk can prove — a displacement, or a named set of own-frame cells appearing on the pivot. A name-grain pre-gate keeps the pass cheap: each composable rule's candidate positions come straight off the index record, and a window where fewer than two rules have a candidate is never shaped at all. The walk then re-shapes the window in the surface's font pair and carries a running column displacement across it — a slide event moves it by the declared count with the pivot leading the next span, an extension event drops off the named seam row the tail the pivot gave up — the named extension, less any shorter one its after cell keeps, or the named contraction — and moves it again with the follower leading, an ink-gain event adds the named cells on the pivot, judged piece by piece, and leaves the displacement alone with the next glyph leading the next span, and every stretch between events is a span whose before picture, displaced by whatever has accumulated, must equal its after picture exactly. A candidate whose own contract fails is simply not an event and its ink is judged as ordinary span ink, so adding a rule to this file can never un-explain a window; two rules claiming one position, or an extension whose follower position is itself claimed, is ambiguous and refuses. Two refusals are deliberate rather than incidental: an extension's follower must be a pure translation, so a rule whose follower is redrawn (·I's smaller loop after ·Tea) never composes, and because the pivot is judged piece by piece rather than in a union, a pivot whose after form also drops a cell off the seam row (·J'ai's crown contracting under an ·At tuck) never composes either; an ink-gain whose after form loses a cell or gains one the rule did not name never fires as an event, which leaves that ink to be judged as ordinary span ink. Credit needs two or more rules — a window one rule accounts for alone belongs to that rule's own line — and a composed fill's verdict is the weakest over the credited rules and over every non-composable rule that matches the window too, its note naming the credited ids in rules-file order. Any rule's `except_left` family, met anywhere in the window, refuses the whole unit rather than the one position, so a guarded context can never ride along beside an unguarded one; a composed reading reads each credited rule's guard in that rule's own shape's scope, and any refusal holds the whole unit — counted on the composed line, never filled, and never handed back to the single-rule pass. This is the zero-touch sibling of echo_verdicts.py: echo fill extends the user's past verdicts to pixel-identical lookalikes, while a standing rule extends a recorded once-and-for-all decision to instances the user has never seen (new left letters minted by later migrations), so those units never queue. The guard list is the point of authoring a guarded rule at all: a rule's except_left families are held for review, so the one context the user does want to see still reaches the docket. Records are stamped with the manifest's generated_at, so any human verdict beats a standing fill on merge, and a parked unit (a skip verdict) is not blank and is never filled. The artifact cycle runs this after the echo fill, with a merge_verdicts pass to land the file."""

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

from rebuild.review.ink import IDENTITY_DIFF, InkComparator, delta_digest, features_for  # noqa: E402
from rebuild.validation.classify import PIXEL_SIZE  # noqa: E402
from rebuild.tools.review_docket import latest_verdicts, load_units  # noqa: E402

SURFACE = ROOT / "rebuild/out/review"
RULES = ROOT / "rebuild/standing-approvals.yaml"
OUT = ROOT / "verdicts-standing-fill.json"
FORMAT = "ams-standing-approvals/1"
ALLOWED_VERDICTS = ("approve", "either")
CELL_FIELDS = 5
EXIT_EXTENSION = re.compile(r"ex-ext-[1-9][0-9]*")
EXIT_CONTRACTION = re.compile(r"ex-con-[1-9][0-9]*")
DELTA_DIGEST = re.compile(r"d-[0-9a-f]{12}")
EMPTY_DELTA_DIGEST = delta_digest(IDENTITY_DIFF)
SEAM_ROW = re.compile(r"y([0-9]+)")
COMPOSABLE_SHAPES = ("slide", "extension-dropped", "ink-gain")


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
        i for i, piece in enumerate(before_run) if _named_pivot(piece[0], match["before"]["pivots"])
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
    """Whether one pivot piece is the named ink-gain: same height, same own-frame origin, both on the grid, and the after picture is the before picture plus exactly the named cells."""
    if before[3] != after[3] or before[4] != after[4]:
        return False
    if before[2] % PIXEL_SIZE or after[2] % PIXEL_SIZE or before[3] % PIXEL_SIZE:
        return False
    painted, kept = intern.cells(before[1]), intern.cells(after[1])
    if painted is None or kept is None:
        return False
    return kept - painted == _gained_cells(match) and not (painted - kept)


def _gain_geometry(match, unit, comparator):
    """Whether the window's rendered before→after change is exactly the named cells appearing on the named pivot, re-derived from the fonts: shape the window under one of the unit's configs, judge each pivot piece as the same picture plus those cells at the same placement, height, and own-frame origin, and require every span strictly between the pivots to render identically with no displacement. Anything the contract cannot hold — no pivot on the before side, pivot counts that disagree, a shaped run that contradicts the unit's recorded glyphs, an off-grid placement, a non-rectilinear outline, a lost cell, an unnamed extra cell — reads as no match, so the unit queues."""
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
    for before_index, after_index in zip(before_pivots, after_pivots):
        before, after = before_run[before_index], after_run[after_index]
        if before[2] != after[2] or not _gain_holds(match, before, after, intern):
            return False
    for before_span, after_span in zip(
        _split_around(before_run, before_pivots), _split_around(after_run, after_pivots)
    ):
        if not _span_settled(intern, before_span, after_span, 0):
            return False
    return True


def _matches_ink_gain(match, unit, excluded, context=None):
    """A letterform that keeps a named set of own-frame cells the old font omitted, matched at the rendered-pixel grain: the old-font pivot form gives way to a named new form that is the same picture plus those cells, every other pixel in the window standing still. Same origin and placement pin the extra ink to the letterform rather than to a slide or a sidebearing change, and any other ink change anywhere in the window fails this match closed — which is where the composed reading picks up, so a window this shape refuses only because a second separately-blessed change moved a pixel it has no vocabulary for may still be explained by both rules together. One shaped config speaks for all of them, on the same digest-agreement precondition the slide shape holds. except_left reads the whole window, as the slide and ink-delta shapes do."""
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
        unit["id"],
    )
    verdict = context.memo.get(key)
    if verdict is None:
        verdict = context.memo[key] = _gain_geometry(match, unit, context.comparator)
    if not verdict:
        return False
    return not any(_joining_family(name) in excluded for name in unit["before"]["glyphs"])


def _validate_ink_gain(rule_id, match) -> None:
    """The ink-gain shape's own coherence, checked once at load: every pivot form named on either side must belong to one family — a gain rule speaks for one letter's extra cells, so a second family in the lists could only be a paste error."""
    families = {_family(name) for name in match["before"]["pivots"] + match["after"]["pivots"]}
    if len(families) != 1:
        _fail(
            f"rule {rule_id!r}: the pivot lists span families {sorted(families)}; an ink-gain rule "
            "speaks for one letter's extra cells"
        )


class Event(NamedTuple):
    """One position a composable rule was credited at in a composed walk: the rule's id, which shape spoke there (`slide`, `extension`, or `gain`), and the columns the window's running displacement moves by at that position — the declared slide, minus the extension's column count, or zero for an ink-gain, which adds cells without moving the rest of the window."""

    rule_id: str
    kind: str
    shift: int


def _is_composable(rule):
    """Whether a rule's shape can take part in a composed reading. Only the slide, extension-dropped, and ink-gain shapes can: they name a local pixel change the walk can prove — a displacement, or a named set of own-frame cells appearing on the pivot — so a walk across a window can carry them. The ligature shape reads a whole window's name-grain structure and the ink-delta shape reads a whole window's ink change byte for byte; neither says anything about one position, so neither has anything to contribute to a walk."""
    return any(SHAPES[name].keyed_by in rule["match"]["after"] for name in COMPOSABLE_SHAPES)


def _composable(rules):
    """The rules a composed reading may credit, in rules-file order."""
    return [rule for rule in rules if _is_composable(rule)]


def _is_slide_match(match):
    return SHAPES["slide"].keyed_by in match["after"]


def _is_gain_match(match):
    return SHAPES["ink-gain"].keyed_by in match["after"]


def _composable_digest(rules):
    """A hashable identity for a set of composable rules — each one's id with what it matches on — so a composed reading memoized against one rules file is never served to another: the memo lives on the context, a caller may hold two rule sets against one context, and the memoized value names rule ids, so two sets that match alike under different ids must not share an entry either."""
    return tuple((rule["id"], json.dumps(rule["match"], sort_keys=True)) for rule in rules)


def _candidates(match, unit):
    """The window positions one composable rule could speak for, read off the index record before anything is shaped: a slide or ink-gain rule's are the glyphs whose recorded before name carries one of its pivot prefixes; an extension rule's are the positions meeting every per-position precondition the single-rule matcher reads — the named drop (an `ex-ext-N` on the before glyph, or an `ex-con-N` on the after cell whose before glyph never carried an exit extension), the named seam standing still at that position on both sides, the pivot and follower after cells, and the follower's own family answering for its own cell — and none at all unless the named seam is a yK height, since the walk has to know which row a dropped tail sits on. Deliberately name-grain and cheap, because this is the pre-gate that decides whether a window is worth shaping at all: a rule with no candidate here can never be credited, and a window where fewer than two rules have one is never shaped."""
    glyphs = unit["before"]["glyphs"]
    if _is_slide_match(match) or _is_gain_match(match):
        return [i for i, name in enumerate(glyphs) if _named_pivot(name, match["before"]["pivots"])]
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
    """Whether one ink-gain candidate's own contract holds at the rendered grain, one position at a time: the after side settles into one of the rule's named after forms, the pivot keeps its height and own-frame origin, and its after picture is its before picture plus exactly the named cells — no cell lost, no unnamed cell gained. Placement under the running displacement is the walk's job, not this contract's, mirroring `_slide_event` leaving the span equality to the walk. None when any of that fails, which leaves the piece to be judged as ordinary span ink."""
    before, after = before_pieces.get(index), after_pieces.get(index)
    if before is None or after is None:
        return None
    if not _named_pivot(after_names[index], match["after"]["pivots"]):
        return None
    if not _gain_holds(match, before, after, intern):
        return None
    return Event(rule_id, "gain", 0)


def _extension_event(match, rule_id, index, intern, before_pieces, after_pieces, cell):
    """Whether one extension candidate's own contract holds at the rendered grain: the pivot stands on the grid at the same height on the same own-frame origin and draws the same picture minus a tail, where the tail is every cell the after form has given up, each of them past the after form's rightmost column, on the very row the named seam holds, and exactly as many columns wide as the pivot gave up — the named extension, less the shorter one its after cell keeps when it keeps one, or the named contraction in full. The grid check is the pivot's own because it is the one piece no span ever pictures — `_span_cells` refuses an off-grid placement everywhere else — and the seam row is read by dividing its height by the pixel size, which only names the right row on the grid. The follower must be a pure translation — the same shape at the same height — so a rule whose follower is redrawn rather than moved yields no event at all, which is deliberate: a redrawn follower is a second change in the window and the composed reading has no vocabulary for it. None when any of that fails, which leaves both pieces to be judged as ordinary span ink."""
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
    if follower_before[1] != follower_after[1] or follower_before[3] != follower_after[3]:
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


def _span_settled(intern, before_span, after_span, displacement):
    """Whether one span between events renders as the same picture once displaced: the union of the before pieces' cells at their placements, moved by the displacement the walk has accumulated so far, must equal the after pieces' union exactly. A span the walk cannot picture — a non-rectilinear outline, an off-grid placement — refuses, so a window no cell reading can be made of never composes."""
    painted = _span_cells(intern, before_span)
    rendered = _span_cells(intern, after_span)
    if painted is None or rendered is None:
        return False
    return {(column + displacement, row) for column, row in painted} == rendered


def _composed_walk(rules, unit, context):
    """The composed reading itself, re-derived from the surface's own fonts: shape both sides of the window, hold each shaped run against what the index recorded, evaluate every composable rule's candidates at the rendered grain, and walk the window left to right carrying a running column displacement — each span between events judged as a picture under the displacement standing when it began, each event judged as its own contract plus a placement offset, and each event's pivot (a slide's) or follower (an extension's) leading the next span under the new displacement. A candidate whose own contract fails is simply not an event and its ink is judged as ordinary span ink, so adding a rule to the file can never un-explain a window that was explained without it; two rules claiming one position, or an extension whose follower position is itself claimed, is ambiguous and refuses outright. Returns each credited rule's event positions, or None when no such reading of the window exists. It carries no arity threshold of its own — a one-rule reading is a real reading of a window, and it is `_composed` that requires two — which is what lets it be held directly against each single-shape matcher."""
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
    if any(len(claims) > 1 for claims in found.values()):
        return None
    events = {index: claims[0] for index, claims in found.items()}
    if any(index + 1 in events for index, event in events.items() if event.kind == "extension"):
        return None
    credited: dict[str, list[int]] = {}
    before_span: list = []
    after_span: list = []
    displacement = 0
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
        if not _span_settled(intern, before_span, after_span, displacement):
            return None
        position = index
        if event.kind == "slide":
            before_span, after_span = [before_pieces[index]], [after_pieces[index]]
            displacement += event.shift
            index += 1
        elif event.kind == "gain":
            if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                return None
            displacement += event.shift
            before_span, after_span = [], []
            index += 1
        else:
            if after_pieces[index][2] != before_pieces[index][2] + displacement * PIXEL_SIZE:
                return None
            displacement += event.shift
            follower_before, follower_after = before_pieces[index + 1], after_pieces[index + 1]
            if follower_after[2] != follower_before[2] + displacement * PIXEL_SIZE:
                return None
            before_span, after_span = [follower_before], [follower_after]
            index += 2
        credited.setdefault(event.rule_id, []).append(position)
    if not _span_settled(intern, before_span, after_span, displacement):
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
    """Whether any rule's except_left guard refuses this window, each read in its own shape's scope: a credited slide or ink-gain rule's reads the whole window, exactly as the single-rule shape does, because neither bounds anything to its left; a credited extension rule's reads only the left neighbor of each position it was credited at, again exactly as the single-rule shape does; and a rule that took no part in the walk but whose own matcher accepts the window unguarded and refuses it guarded holds it too, since that rule would have held the window in the single-rule pass and a composition must not lift a hold. A refusal holds the whole unit rather than dropping the one rule's credit, which is the file's standing principle that a guarded context never rides along beside an unguarded one."""
    glyphs = unit["before"]["glyphs"]
    for rule in rules:
        match = rule["match"]
        excluded = set(match.get("except_left", []))
        if not excluded:
            continue
        indices = events.get(rule["id"])
        if indices:
            if _is_slide_match(match) or _is_gain_match(match):
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
    """One expressible delta shape: the match.after field that declares it, the field names match.before and match.after must carry exactly (an empty tuple means the block itself must be absent), which of those fields are lists of cell strings, of delta digests, or of glyph-name prefixes — or integer column counts, or a family name that may also be a list of them — rather than plain scalars, the matcher that reads a unit for it, and its own coherence check."""

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
    "slide": Shape(
        keyed_by="slide",
        before=("pivots",),
        after=("pivots", "slide"),
        cell_lists=(),
        matcher=_matches_slide,
        validate=_validate_slide,
        name_lists=("pivots",),
        int_fields=("slide",),
    ),
    "ink-gain": Shape(
        keyed_by="gained",
        before=("pivots",),
        after=("pivots", "gained"),
        cell_lists=(),
        matcher=_matches_ink_gain,
        validate=_validate_ink_gain,
        name_lists=("pivots",),
        point_lists=("gained",),
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
                        or not value
                        or not all(
                            isinstance(item, list)
                            and len(item) == 2
                            and all(isinstance(n, int) and not isinstance(n, bool) for n in item)
                            for item in value
                        )
                        or len({tuple(item) for item in value}) != len(value)
                    ):
                        _fail(
                            f"rule {rule_id!r}: match.{block}.{field} must be a nonempty list of "
                            "distinct [column, row] own-frame cells"
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


def _matches(match, unit, *, guard=True, context=None):
    before, after = unit.get("before"), unit.get("after")
    if not before or not after:
        return False
    excluded = set(match.get("except_left", [])) if guard else set()
    for shape in SHAPES.values():
        if shape.keyed_by in match["after"]:
            return shape.matcher(match, unit, excluded, context)
    return False


def main(argv=None, *, units=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split(":")[0] + ".")
    parser.add_argument(
        "verdicts", help="the verdicts file that defines blankness (an export or the autosave)"
    )
    parser.add_argument("--surface", default=str(SURFACE))
    parser.add_argument("--rules", default=str(RULES))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)

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
        for unit in (load_units(surface) if units is None else units)
        if not unit.get("no_verdict") and unit.get("batch") is not None and unit.get("render_groups") == 1
    ]
    composable = _composable(rules)
    wants_deltas = (
        any(
            SHAPES[name].keyed_by in rule["match"]["after"]
            for rule in rules
            for name in ("ink-delta", "slide", "ink-gain")
        )
        or len(composable) > 1
    )
    # The index record always carries the key, and carries None exactly when the shard had no ink_deltas field at all — which is what "predates the emission" means here. The slide and ink-gain shapes share the dependency: their config-agreement precondition reads the same field, so they refuse the stale surface just as loudly instead of quietly matching nothing.
    if wants_deltas and not any(unit.get("ink_deltas") is not None for unit in units):
        raise SystemExit(
            "the surface carries no ink_deltas fields, so it predates the ink-delta, slide, and ink-gain "
            "shapes; such a rule cannot match anything on it — rebuild the surface (make review-cycle) first"
        )
    context = None
    if (
        any(
            SHAPES[name].keyed_by in rule["match"]["after"]
            for rule in rules
            for name in ("slide", "ink-gain")
        )
        or len(composable) > 1
    ):
        before_font, after_font = surface / "fonts" / "before.otf", surface / "fonts" / "after.otf"
        if not (before_font.is_file() and after_font.is_file()):
            raise SystemExit(
                "a slide or ink-gain rule, and any composed reading two or more composable rules could "
                "earn, re-shape their candidate windows in the surface's own font pair, and this surface "
                "carries no fonts/before.otf + fonts/after.otf — rebuild the surface (make review-cycle) first"
            )
        context = SlideContext(before_font, after_font)

    order = {rule["id"]: index for index, rule in enumerate(rules)}
    fills = []
    claimed: set[str] = set()
    composed_counts: dict[tuple[str, ...], list[int]] = {}
    if len(composable) > 1 and context is not None:
        for unit in units:
            events = _composed(composable, unit, context)
            if events is None:
                continue
            credited = tuple(sorted(events, key=lambda rule_id: order[rule_id]))
            claimed.add(unit["id"])
            counts = composed_counts.setdefault(credited, [0, 0, 0])
            if _composed_held(rules, unit, events, context):
                counts[2] += 1
            elif unit["id"] in records:
                counts[1] += 1
            else:
                counts[0] += 1
                verdict, note = _composed_verdict(rules, unit, events, context)
                fills.append(
                    {
                        "unit": unit["id"],
                        "verdict": verdict,
                        "note": note,
                        "at": manifest["generated_at"],
                    }
                )

    open_units = [unit for unit in units if unit["id"] not in claimed]
    lines = []
    for rule in rules:
        matched = [unit for unit in open_units if _matches(rule["match"], unit, context=context)]
        held = [
            unit
            for unit in open_units
            if _matches(rule["match"], unit, guard=False, context=context)
            and not _matches(rule["match"], unit, context=context)
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
    for credited in sorted(composed_counts, key=lambda ids: [order[rule_id] for rule_id in ids]):
        filled, verdicted, guarded = composed_counts[credited]
        lines.append(
            f"  {' + '.join(credited)}: {filled} filled, {verdicted} already verdicted, "
            f"{guarded} held for review by except_left"
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
    return 0


if __name__ == "__main__":
    main()
