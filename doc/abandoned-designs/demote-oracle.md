# The demote oracle

The old Senior engine compiled one YAML file of letter families and stances into a `calt` of chained contextual substitutions. Commit `d23c0a87`, the last before the rune engine replaced it, holds its code, its data and its documents; the rune files and the pipeline under `engine/` replace all of it.

## The design

The old engine commits a stance early and repairs it later. A letter takes a joining stance as soon as a neighbor offers a join at a matching height. A later lookup can then rewrite that neighbor into a stance without the matching anchor, and the letter is left with a connector that reaches for nothing. The demote layer puts such a letter back on its isolated form, the stance it takes when its pair is shaped alone. It is three hand-written override tables in the YAML:

- `predecessor_demote_overrides`: a predecessor stance, the trigger stance after it (optionally with a backtrack stance before it), and the isolated form, each row compiled to `sub predecessor' trigger by isolated_form;`. A later lookup can strip the trigger's entry again, so the emitter writes the same lookups at three points in the chain (`calt_pred_demote_*`, `calt_final_pred_demote_*`, `calt_final2_pred_demote_*`) and applies each row to the trigger's extension variants as well.
- `trailing_demote_overrides`: the mirror image, a leader stance and a trailing stance that took a backward entry the leader no longer offers.
- `restore_isolated_form_overrides`: a prior, target and follower, for a target that a guard (`guard-tables.md`) kept unchanged although the pair shaped alone gives it another stance.

Each row answers one observed failure. The demote oracle was the plan to stop writing them: shape every letter sequence up to four letters with the authored tables left out, collect each adjacent pair that does not join (no exit height of the left glyph is an entry height of the right one), and derive the demote row and its isolated target from the anchor geometry of the letter's sibling stances.

## Why it was abandoned

A feasibility spike measured the oracle against the authored tables and returned a no-go. Pairwise geometry reproduced only about half of the authored rows, and what it missed was not noise. The right isolated target depends on context the pair does not hold: whether an entry is load-bearing because of the letter two places back, whether a follower still needs an exit, whether the trigger is a ligature that would have to be un-fused, or which of two siblings with the same anchors the author meant. The oracle also derived many times more rows than the authors had written, and the few that matched visible failures included real joins that a demote would have broken. A non-join on adjacent final anchors does not show that a demote is safe.

The deeper fault is the model the tables patch. The emitted chain is the meaning of the YAML, and the engine never computes what the chain does. A repair is one more rule that later lookups can undo, which is why one lookup runs three times, and every new letter or join adds rows while nothing shows when a row stops being needed.

## What replaced it

The rune engine settles before it emits (`doc/engine-design.md` §1). The kernel's settlement function computes the final stance, join and extension of every position, and the FEA transcribes its decision table as final, positive substitutions whose context is already settled when they run (§7). Nothing is committed early, so nothing is demoted. A behavior of the old tables that the runes keep is an explicit `refuse`, `prefer` or `resolve` in a rune file with a `why:` (§13), the conformance sweep checks that the compiled font shapes as the settlement function says, and a `|` in a `data-expect` pin asserts that the text on each side of a break shapes as it does alone.
