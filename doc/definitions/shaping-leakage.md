# Shaping leakage: a working definition

This doc defines “shaping leakage” precisely enough that an agent can detect leaks, classify each as bad or benign, and fix the bad ones without a human judging each case. The closing section says where each decision is implemented and what is not built.

The investigation behind these decisions (the measurement sweep, the triage of the depth-4 leaks, and the join-contract prevention work) is in `doc/history/2026-06-03--leak-cleanup/`. Those notes hold the evidence for the empirical claims here.

## The one-sentence definition

A **shaping leak** is any difference in a glyph’s shape, across a non-join (pen-lift), between how it is shaped _in context_ and how it is shaped _in isolation_. “In isolation” means each side of the break is re-shaped as its own run, with the real boundary token kept (decision 4).

Leakage is a descriptive notion: any such difference counts. A separate severity, bad or benign, is assigned by an operational proxy. Some benign leakage is wanted, because it adds a little faux-organic variation to the font.

## Settled decisions

### 1. Leakage is descriptive; bad/benign is the severity overlay

“Leak” names a cross-break shape difference, not a defect. Every leak is either **bad** (it looks wrong: a stroke reaching for a neighbor that isn’t there) or **benign** (a legitimate alternate stance, or an invisible swap). A small amount of benign leakage is desirable, because it makes the script look slightly more hand-drawn.

### 2. Two independent leak types over the same break event

Two predicates describe a leak at a break:

- **Identity leak**: a glyph _immediately flanking the break_ changed: `left_chosen != isolated_left` or `right_chosen != isolated_right`. This means a contextual lookup reached across the non-join and picked a different glyph, even when nothing visibly moves.
- **Visual leak**: the rendered run changed. The whole-run pixel signature of the in-context shaping differs from the concatenation of the two separately shaped halves, with `kern` disabled because spacing is not part of leakage.

In this font every visual leak comes from a glyph-name change somewhere in the run. The sweep turns `kern` off, and `kern` holds the font’s only contextual GPOS rule. Cursive attachment cannot fire across a non-join. The join contract drops non-joining, non-cosmetic neighbors from `calt` rules before emission (`_JoinContractRecorder` in `tools/quikscript_fea.py`). The two predicates still catch different cases. The identity signature records only the two glyphs touching the break, so a glyph that changes further inside a run appears only in the visual diff. A flanking-glyph swap can render pixel-identical, which is an identity leak with no visual leak.

The sweep records identity leaks, keyed by the signature `(isolated_left, left_chosen, isolated_right, right_chosen)`, and `_visual_status` in `tools/build_check_html.py` marks each example `same` or `diff`. A signature is visible if any swept example renders a `diff` (`find_visible_leaks`), so the visible set does not depend on enumeration order. Only visible leaks are written to the snapshots.

### 3. The break is any non-join (pen-lift)

Leaks are looked for at every non-join (`_scan_sequence` in `tools/build_check_html.py`). Two letters separated by a `space` or ZWNJ token always have a break between them, because cursive attachment cannot cross the token. Two adjacent letters with no token between them have a break when their chosen glyphs share no join height: `exit_ys(left) & entry_ys(right)` is empty. Two letters fused into one ligature glyph are not a break. Across a pen-lift no stroke connects the two letters, so neither letter’s shape should depend on what is on the other side.

### 4. The isolated reference is boundary-faithful

The break divides the sequence into a left run and a right run. Each run is re-shaped as its own buffer, with its own internal joins intact. The reference is **boundary-faithful**: where real text has a boundary token, that token is present.

- **Word boundaries**: the real `space` or ZWNJ token is in both the full shaping and each isolated half. Space-keyed `calt` (the `not_after` guards in `glyph_data/quikscript.yaml` that list `space` and `uni200C`) then behaves the same in both, and only a difference reaching _across_ the token is flagged. A real boundary token usually breaks the `calt` contextual match anyway, so boundary-faithfulness tends to remove spurious word-boundary leaks while keeping space-keyed stances in the reference.
- **Mid-word non-joins**: real text has no token between the two letters, so nothing is inserted and isolation only splits them.

The sweep therefore enumerates sequences that contain the boundary tokens as well as letters (`_sweep_alphabet`). It skips sequences with a token at either end or two adjacent tokens.

### 5. Boundary tokens: both `space` and `ZWNJ`

`space` and `uni200C` (ZWNJ) are separate boundary tokens (`BOUNDARY_TOKENS` in `test/quikscript_shaping_helpers.py`), and the sweep uses each. Both appear in the glyph data’s `not_after` context lists, so both are boundaries that real text contains.

### 6. Bad vs benign: operational proxy + author override

The verdict is mechanical, with author overrides:

- **Bad** ⇔ the leak is **visible** _and_ a changed flanking stance is **additive toward the break**: it gained, in context, a break-facing connector that its isolated form lacks. At a non-join the neighbor cannot complete that connector, so the stroke dangles into empty space. This “dangle” is the main defect.
- **Benign** ⇔ everything else: **subtractive** trims (a contraction, or an entryless, exitless, or trimmed edge) that make the letter more self-contained; swaps to a different **standalone variant** that is a valid stance on its own; and **all invisible** swaps (visual `same`).
- **Author override**: a changed stance whose modifiers include `before-<family>` / `after-<family>` for the across-break neighbor’s family is an author-declared cosmetic interaction, and is benign whatever the proxy says. Only a force-bad entry (decision 11) overrides it.

The additive/subtractive axis is read from a stance’s modifier tokens, with no rendering:

| Class                          | Tokens / derive directives                                                                                                                                                                        |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| additive (reach)               | a break-facing connector the chosen stance **gained** over the isolated form: `ex-yN`/`ex-ext-N` on a left exit, `en-yN`/`en-ext-N` on a right entry, `extended` on either side                   |
| subtractive (trim)             | `ex-con-N`, `en-con-N`, `ex-trim-N`, `en-trim-N`, `ex-dips`, `contract_exit_before`, `contract_entry_after`, `noentry`, `noexit`, `ex-noentry`                                                    |
| standalone variant             | `reaches-way-back`, `nonjoining-left`, `gapped`, `smaller-loop`, `widebase`                                                                                                                       |
| author cosmetic (force-benign) | `before-<family>`, `after-<family>`, checked against the neighbor’s family through the stance’s resolved trigger list, so class tokens such as `after-baseline-letter` don’t match every neighbor |

`tools/leak_classify.py` is the authority on the additive sets (`_LEFT_ADDITIVE_RE`, `_RIGHT_ADDITIVE_RE`) and the removed-edge sets (`_LEFT_EDGE_REMOVED`, `_RIGHT_EDGE_REMOVED`). Of the table’s rows, `classify` uses only these sets and the cosmetic check. The subtractive and standalone-variant rows describe changes it leaves benign. The test is a difference: it compares the chosen stance’s modifiers with the isolated form’s and asks what was gained on the break-facing edge (the left glyph’s exit, the right glyph’s entry). A break-facing edge that is removed (`noexit` or `ex-noentry` on the left, `noentry` on the right) makes the side benign, because nothing is left to dangle. A static `ex-ext`/`en-ext` token alone is the wrong signal: in the human triage verdicts (`doc/history/2026-06-03--leak-cleanup/leak-emergent-verdicts.txt`) it matches none of the rows marked broken, while most of them gain a break-facing `ex-yN` or `en-yN` connector.

Every leak the human triage marked outright broken is an additive dangle or a multi-rule composition of additive reaches, and none is a subtractive trim that made a letter look wrong. With the two per-signature override lists (decision 11), the proxy agrees with every human verdict. `uv run python tools/leak_verdict_reconcile.py` prints the confusion matrix.

### 7. CI gates on bad leaks only, with overrides in both directions

The bad-leak gates fail when a **bad** leak (a visible additive dangle, after overrides) is not already listed in `site/bad-leak-backlog.txt`. The benign census also fails `make test-leaks` when it changes (decision 8). Two author override channels sit on either side of the proxy verdict:

- **Force-benign**: a `before-<family>` / `after-<family>` cosmetic declaration on a stance (decision 6), or a per-signature allowlist entry (decision 11).
- **Force-bad**: a per-signature blocklist entry for a swap the proxy reads as benign but the author finds ugly, so the agent treats it as a defect to fix.

All visible leaks, bad and benign, are recorded for review. `test/test_isolation_leaks.py` has the gates.

### 8. Depth is a coverage knob; bad is a gate, benign is a census

The definition sets no maximum depth: a leak is a leak at any sequence length. Depth only sets how far the sweep enumerates. Depth 3 is cheap enough for the everyday `make test`. Depth 4 is slower and runs in `make test-leaks`. Depth 5 and beyond is impractical.

- The **bad** set is checked at depth 3 and depth 4 against the backlog. A new bad signature fails; a resolved one prints a notice to re-bless.
- The **benign** set is a **census** snapshot (`site/benign-leak-census.txt`) checked only at depth 4. Any change, gained or lost, fails `test_benign_census_unchanged` in `make test-leaks` until `make leak-snapshot` re-blesses it. The default `make test` does not run it.

### 9. The iteration loop is autonomous detect→fix→verify, commit-gated

Every bad leak is an additive dangle, and its fix is always **subtractive**: for the offending context only, make the break-facing edge subtractive or revert it to the isolated or trimmed stance. The existing mechanisms do this: `not_before` to stop the additive stance being selected, `contract_exit_before` / `contract_entry_after`, an `ex-noentry` trim, or a `predecessor_demote_overrides` / `trailing_demote_overrides` row.

The agent runs the loop unattended: sweep, classify, fix each bad leak, rebuild, re-sweep, and confirm that the bad leak is gone, no real join broke, and no new bad leak appeared. It stops before committing, because the project requires explicit approval for every commit.

### 10. One verdict per break: bad iff any changed stance is an additive dangle

A break can change the left flanking glyph, the right one, both, or a glyph further inside a run. A break is **bad** if a stance that changed because of it is an additive reach toward a connection that isn’t made, and benign otherwise. The dangle always sits on a break-facing edge: `calt` matching is contiguous, so cross-break influence cannot skip the flanking glyph, and an inward change always comes with a flanking identity change. `classify` in `tools/leak_classify.py` therefore reads only the two flanking glyphs in the signature.

The verdict depends on the **resulting stance**, not the rule structure, so it does not matter whether one rule or a chain of lookups produced the dangle. The cross-lookup “compose” emergent leaks are bad when their resulting stance is an additive dangle. The leaks the triage accepted (either stance fine, or one merely preferable) classify benign, some of them through the force-benign allowlist. Ligatures need no special case: `qsThey_qsUtter` → `qsThey_qsUtter.noentry.ex-con-1` is read by its modifiers (`noentry` and `ex-con-1`, both subtractive, so benign) like any other stance.

### 11. Overrides: force-benign per-stance, force-bad per-signature

The two override channels are keyed to fit their jobs:

- **Force-benign** has two parts. One is per stance: a `before-<family>` / `after-<family>` modifier saying the cross-break change is intended wherever it appears. The other is per signature: the allowlist `site/leak-force-benign.yaml`. Some human-accepted standalone-variant swaps (for example `qsNo` → `qsNo.alt.en-y0.ex-y0`) gain a break-facing anchor and so trip the proxy, yet carry no cosmetic modifier. Only a per-signature entry can mark that exact swap benign without weakening the proxy elsewhere.
- **Force-bad** is keyed on the leak **signature** `(isolated_left, left_chosen, isolated_right, right_chosen)`, in the blocklist `site/leak-force-bad.yaml`. It marks only that exact swap bad. Its entries are swaps the human triage marked broken that the proxy reads as benign. Most are the cross-lookup-compose case, where the changed side strips to its bare form while an unchanged ligature neighbor absorbs the join, which a per-stance proxy cannot see. Force-bad takes precedence over every force-benign signal.

### 12. The per-fix verify gate

After applying a fix, the agent rebuilds, re-sweeps to the gate depth, and requires: (a) the targeted bad leak is gone; (b) no **new** bad leak appears anywhere in the swept set; and (c) full `make test` passes, so no real cursive join broke. Benign census changes are reported at the commit boundary and never block the loop. The depth-3 re-sweep takes about a second, so checking for new dangles on every iteration is cheap.

## Build work this definition implies

- Detection and the boundary-faithful reference (decisions 2 to 5): `find_leaks`, `find_visible_leaks`, `_scan_sequence`, and `_visual_status` in `tools/build_check_html.py`.
- The bad/benign classifier and both override lists (decisions 6, 10, 11): `tools/leak_classify.py`, `site/leak-force-bad.yaml`, and `site/leak-force-benign.yaml`, checked against the human triage by `tools/leak_verdict_reconcile.py`.
- The gates (decisions 7 and 8): `test/test_isolation_leaks.py`. `test_no_new_bad_isolation_leaks` runs at depth 3 in `make test`. `test_bad_leak_backlog_unchanged` and `test_benign_census_unchanged` run at depth 4 in `make test-leaks`. `tools/leak_snapshot.py` (`make leak-snapshot`) writes `site/bad-leak-backlog.txt` and `site/benign-leak-census.txt`.
- Not built: the autonomous detect→fix→verify loop with the per-fix verify gate (decisions 9 and 12). Its brief is `doc/definitions/shaping-leak-loop.md`.
