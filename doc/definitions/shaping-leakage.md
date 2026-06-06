# Shaping leakage: a working definition

This doc defines “shaping leakage” precisely enough that an autonomous agent can detect leaks, classify each as bad or benign, and iterate on the bad ones without a human adjudicating case by case. The decisions below were settled by walking the full design tree; the closing section lists the build work they imply, none of which is done yet.

The prior investigation that motivated this — the measurement sweep, the triage of 387 depth-4 leaks, and the join-contract prevention work — lives under `doc/history/2026-06-03--leak-cleanup/`. Read those notes for the evidence behind the empirical claims here.

## The one-sentence definition

A **shaping leak** is any difference in a glyph’s shape, across a non-join (pen-lift), between how it is shaped _in context_ and how it is shaped _in isolation_ — where “in isolation” means each side of the break re-shaped as its own run, faithful to the real boundary.

Leakage is a _descriptive_ notion: any such difference counts. On top of it sits a _bad_ vs _benign_ severity, judged by an operational proxy. Some benign leakage is positively welcome — it introduces a little faux-organic variation to the font.

## Settled decisions

### 1. Leakage is descriptive; bad/benign is the severity overlay

“Leak” names the phenomenon (a cross-break shape difference), not a defect. Every leak is then either **bad** (looks wrong/ugly — a stroke reaching for a neighbor that isn’t there) or **benign** (a legitimate alternate form, or an invisible swap). Benign leakage is not merely tolerated: a small amount is desirable, because it makes the script read as slightly more hand-drawn and less mechanical. “Benign leakage” is our term of art for the OK kind.

### 2. Two independent leak types over the same break event

A leak is detected by two independent predicates over the same break. Neither is defined in terms of the other.

- **Identity leak** — the glyph _immediately flanking the break_ changed: `left_chosen != isolated_left` or `right_chosen != isolated_right`. This is the mechanism: a contextual lookup reached across the non-join and picked a different glyph. It is the early-warning signal even when nothing visibly moves.
- **Visual leak** — the _rendered run_ changed: the whole-run pixel signature of the in-context shaping differs from the concatenation of the two independently-shaped halves (with `kern` disabled, since spacing is not what leakage is about).

Why independent and not nested: in this font a _pure_ visual leak (pixels differ with no glyph-name change anywhere) is impossible — `kern` is off, cursive attachment cannot fire across a non-join, there are no contextual GPOS rules, and the join contract strips non-joining neighbors from `calt` before emission. So every visual leak has _some_ identity cause. But neither _signature_ contains the other: the identity signature records only the two glyphs touching the break, so a glyph that changes two letters back from the break is invisible to it yet shows up as a visual diff; conversely a flanking-glyph swap can render pixel-identical. Each predicate catches cases the other misses.

### 3. The break is any non-join (pen-lift)

Leaks are looked for at _every_ non-join, defined exactly as `exit_ys(left) & entry_ys(right) == set()` (`_pair_join_ys` in `test/quikscript_shaping_helpers.py`). This covers both word/segment boundaries (`space`/`ZWNJ`) and mid-word non-joins (adjacent letters whose anchors simply don’t meet). The principle is identical in both cases: across a pen-lift no stroke connects the two letters, so neither letter’s shape may depend on what sits on the other side.

### 4. The isolated reference is boundary-faithful

The break partitions the sequence into a left run and a right run. Each run is re-shaped as its own buffer, with the side’s _own internal joins left intact_. Crucially the reference is **boundary-faithful**: where real text has a boundary token, that token is present.

- **Word boundaries**: the real `space`/`ZWNJ` token is inserted, and it appears in _both_ the full shaping and the isolated halves, so space-keyed `calt` (`after: space` / `not_after: space`) is honored consistently and only a difference reaching _across_ the token is flagged. (A real boundary token usually breaks the `calt` contextual match anyway, so boundary-faithfulness tends to _eliminate_ spurious word-boundary leaks while keeping space-keyed forms in the reference.)
- **Mid-word non-joins**: real text has no token between the two letters, so nothing is inserted; isolation simply splits them.

Consequence: the detection sweep, today letter-only (`itertools.product` over letters), must enumerate sequences that also contain the boundary tokens to surface word-boundary leaks at all.

### 5. Boundary tokens: both `space` and `ZWNJ`

Both `space` and `uni200C` (ZWNJ) are treated as distinct boundary tokens, swept and isolated with each. Both appear in real `calt` `after`/`not_after` rules, so both are real-text boundaries a reader or typist actually hits.

### 6. Bad vs benign: operational proxy + author override

The verdict is mechanical, with an author escape hatch:

- **Bad** ⇔ the leak is **visible** _and_ the in-context flanking form is **additive toward the break** — it extends or reaches connector ink toward the across-break neighbor (an extension/reach/cosmetic-reach). At a non-join the neighbor cannot complete that reach, so the stroke dangles into empty space. This is the “dangle,” the dominant defect.
- **Benign** ⇔ everything else: **subtractive** trims (a contraction, an entryless/exitless/trimmed edge) that merely make the letter more self-contained; **self-complete standalone variant swaps** (a different but valid citation-quality form); and **all invisible** swaps (visual-`same`).
- **Author override**: a form whose modifiers include `before-<family>` / `after-<family>` is an author-declared cosmetic interaction — forced benign regardless of the proxy.

The additive/subtractive axis is **mechanically readable from a form’s modifier tokens**, no pixel rendering required:

| Class | Tokens / derive directives |
| --- | --- |
| additive (reach) | a break-facing connector the chosen form **gained** vs the isolated form: `ex-yN`/`ex-ext-N` on a left exit, `en-yN`/`en-ext-N` on a right entry, `extended` either side |
| subtractive (trim) | `ex-con-N`, `en-con-N`, `ex-trim-N`, `en-trim-N`, `ex-dips`, `contract_exit_before`, `contract_entry_after`, `noentry`, `noexit`, `ex-noentry` |
| standalone variant | `reaches-way-back`, `nonjoining-left`, `gapped`, `smaller-loop`, `widebase` |
| author cosmetic (force-benign) | `before-<family>`, `after-<family>` (validated against the neighbor’s family via the form’s resolved trigger list, so class tokens like `after-baseline-letter` don’t misfire) |

**Correction folded in during implementation (`tools/leak_classify.py`):** the additive signal is the _gained break-facing anchor_, not a static `ex-ext`/`en-ext` token. Measured against the 99 human-verified “broken” leaks, keying on `ex-ext`/`en-ext` alone fires on **zero** of them, whereas 97 of 100 broken rows involve the in-context form gaining a break-facing `ex-yN`/`en-yN` connector its isolated form lacked. So the proxy compares the chosen form’s modifiers to the isolated form’s and asks what was _gained_ on the break-facing edge (the left glyph’s exit, the right glyph’s entry), with a break-facing subtractive token (`noexit`/`ex-noentry` left, `noentry` right) winning — the edge is gone, nothing to dangle. This is consistent with the prose above (“reaches connector ink toward the across-break neighbor”); only the original token bucketing was wrong (it filed `en-yN`/`ex-yN` under “standalone position”).

Empirical backing: across the 99 leaks human-verified as outright broken, **every** one is an additive dangle (or a multi-rule compose of additive reaches); **zero** are a subtractive trim that made a letter look wrong. So “additive at a non-join = bad, subtractive = benign” is not a guess — it matches the whole measured corpus. With the two per-signature override lists seeded (below), the proxy reconciles the human corpus exactly (precision = recall = 1.000); reproduce with `uv run python tools/leak_verdict_reconcile.py`.

### 7. CI gates on bad leaks only, with overrides in both directions

CI fails if and only if a **bad** leak (a visible additive dangle, after overrides) survives. Benign leaks pass and are welcome — they are the faux-organic variation we want. Two author override channels sit on either side of the proxy verdict:

- **Force-benign**: a `before-<family>` / `after-<family>` cosmetic declaration on a form (already part of decision 6).
- **Force-bad**: a blocklist entry for a proxy-benign swap the author nonetheless finds ugly, so the agent treats it as a defect to fix.

The full leak set (bad and benign) is still recorded for review; only the bad subset is a hard failure.

### 8. Depth is a coverage knob; bad is a gate, benign is a census

The definition bakes in no maximum depth — a leak is a leak at any sequence length. Depth only governs how far the sweep enumerates (depth-3 is cheap enough for the everyday hard gate; depth-4 is slow and runs deeper/periodically; depth-5+ is impractical). At each swept depth:

- the **bad** set is a hard gate that must stay empty;
- the **benign** set is an informational **census** snapshot — changes are surfaced for review (so we notice the organic-variation set shifting) but never fail CI.

### 9. The iteration loop is autonomous detect→fix→verify, commit-gated

Every bad leak is an additive dangle, and its remedy is always **subtractive**: demote the dangling reach back to the isolated/trimmed form _for the offending context only_ — via the existing levers (`revert to isolated`, `predecessor_demote_overrides`, `not_before` to stop the additive form being selected, a `contract_exit_before` / `contract_entry_after`, or an `ex-noentry` trim). The remediation principle is part of this spec: _a bad leak is fixed by making the break-facing edge subtractive (or reverted) for the offending context._

The agent runs the full loop unattended: sweep → classify → fix each bad leak → rebuild → re-sweep → confirm the bad leak is gone, no real join broke, and no new bad leak appeared. It stops at the commit boundary for human approval (per the project’s “never commit without explicit approval” rule).

### 10. One verdict per break: bad iff any changed form is an additive dangle

A break can change the left flanking glyph, the right one, both, or ripple to a glyph further inside a run. The verdict examines _every_ form that changed because context crossed that break and is **bad if any of them is an additive reach toward a connection that isn’t completed**; benign otherwise. In practice the dangle always sits on a break-_facing_ edge — contiguous `calt` matching means cross-break influence can’t skip the flanking glyph, so an inward ripple always rides behind a flanking identity change.

This is a property of the **resulting form**, not the rule structure, so it does not matter whether one rule or a chain of composed lookups produced the dangle: the cross-lookup “compose” emergent leaks are classified bad whenever their resulting form is an additive dangle, and the triage corpus’s “accepted residue” (either form fine, or one merely preferable) lands benign as self-complete variant swaps. Ligatures need no special case — `qsThey_qsUtter` → `qsThey_qsUtter.noentry.ex-con-1` is read by its modifiers (`noentry` + `ex-con-1`, both subtractive → benign) exactly like any other form.

### 11. Overrides: force-benign per-form, force-bad per-signature

The two override channels are keyed to fit their jobs:

- **Force-benign** is two-pronged: the per-_form_ declaration — a `before-<family>` / `after-<family>` modifier saying “this tuck is intended wherever it appears” — _plus_ a per-_signature_ allowlist (`site/leak-force-benign.yaml`). The per-signature half is a symmetric completion folded in during implementation: some human-accepted standalone-variant swaps (e.g. `qsNo` → `qsNo.alt.en-y0.ex-y0`) gain a break-facing anchor and so trip the proxy, yet carry no cosmetic modifier, so only a per-signature allowlist can demote that exact swap without weakening the proxy elsewhere.
- **Force-bad** is keyed on the leak **signature** 4-tuple `(isolated_left, left_chosen, isolated_right, right_chosen)`, in a small blocklist (`site/leak-force-bad.yaml`). It condemns only that exact swap — the cross-lookup-compose case where the changed side strips to bare while an unchanged ligature neighbor absorbs the join, which the per-form proxy is structurally blind to. Force-bad outranks every force-benign signal.

### 12. The per-fix verify gate

After applying a fix, the agent rebuilds, re-sweeps to the gate depth, and requires: (a) the targeted bad leak is gone; (b) zero **new** bad leaks anywhere in the swept set; and (c) full `make test` green, so no real cursive join broke. Benign census changes are reported but never block the loop; they are surfaced at the commit boundary. The depth-3 re-sweep is sub-second, so checking for newly-introduced dangles every iteration is cheap and prevents whack-a-mole.

## Build work this definition implies

These are the implementation consequences of the decisions above. **Deliverable A (detection + classification + gates) is done** (see the files named below); **deliverable B (the autonomous loop) is the remaining work** — its brief is `doc/definitions/shaping-leak-loop.md`.

- [x] Expand the detection sweep to also enumerate the `space` and `ZWNJ` boundary tokens, with a boundary-faithful isolated reference (token present in both the full shaping and the halves). `tools/build_check_html.py` (`find_leaks`, `find_visible_leaks`, `_scan_sequence`, cluster-based spans, `_visual_status`).
- [x] Add the mechanical classifier (`tools/leak_classify.py`) and the bad/benign verdict, with the additive-signal correction folded into decision 6.
- [x] Retarget the depth-3 CI gate to “no new bad leak” (`test/test_isolation_leaks.py::test_no_new_bad_isolation_leaks`).
- [x] Split the depth-4 artifact into a bad-leak backlog gate (`site/bad-leak-backlog.txt`, asymmetric — fails on new, notice on resolved) and a benign census (`site/benign-leak-census.txt`, symmetric, informational). Both generated by `tools/leak_snapshot.py`.
- [x] Add the per-signature force-bad blocklist (`site/leak-force-bad.yaml`) and the per-signature force-benign allowlist (`site/leak-force-benign.yaml`), validated against the human corpus by `tools/leak_verdict_reconcile.py`.
- [ ] Wire the autonomous detect→fix→verify loop, with the per-fix verify gate from decision 12. **Not done — deferred.** See `doc/definitions/shaping-leak-loop.md`.

A pragmatic refinement also landed: visibility is keyed on “any swept example renders a `diff`”, not on whichever example surfaced first, so the visible set is independent of enumeration order (the old first-example heuristic undercounted, and adding the boundary tokens would otherwise mask letters-only leaks). See `find_visible_leaks`.
