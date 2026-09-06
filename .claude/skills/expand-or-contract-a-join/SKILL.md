---
name: expand-or-contract-a-join
description: Lengthen or shorten one already-joining rebuild pair by N pixels — pick one side, fold the family into an existing same-by extend/contract list when one matches, probe, and gate. Use when the user asks to extend, contract, extend, lengthen, shorten, or add a pixel to a ·X·Y join. It is assumed this is for the rebuild. (or runs /expand-or-contract-a-join).
argument-hint: "[·X·Y] [by N] [baseline|x-height]"
---

A pixel change on one already-joining rebuild pair. The record lives on a rune under `glyph_data/runes/`, not in `glyph_data/quikscript.yaml` (that file's `derive.extend_*` / `contract_*` is the old font; the tweak-an-old-font-join skill is the analogue there). Opening a join that currently breaks is a different task.

`rebuild/schema/rune.schema.json` (`extendRecord`, `contractRecord`) is the record shape. Never author `why:` — AGENTS.md. If the named family has more than one variant on the relevant axis, stop and ask before editing (same letter-name rule).

## 1 — pin the request

- Pair `·X·Y` is left then right. Height is probably `baseline` or `x-height` (the live seam is in a probe of the pair). Amount defaults to 1.
- Codepoints: `doc/glyph-names.md`. Confirm both runes already list each other on that height (`toward:` / `from:`).

## 2 — look for an opposing adjustment first, then pick one side and fold

The two sides of a seam stack; they do not cancel. Before adding a contract, search **both** runes' `policy.extend` for a record that already covers this pair at this height with the same `feature:` / `self:` / `then:` guards the user scoped (pair-wide means no extra guards). If one exists, reduce that extend by N: drop the family from its list (delete the record when the list empties; a remaining single family goes back to flow `{family: qsGay}`), or if its `by` is larger than N, lower `by`. Only author a new contract for leftover amount. Same in reverse when the user asks to extend and a covering contract already exists.

Honor an explicit side ("·Key's foot", "like the other ·Key contractions") when adding. Otherwise:

- **Extend `·X·Y`:** qsX, `exit: <height>`, `when.right` includes qsY.
- **Contract `·X·Y`:** qsY, `entry: <height>`, `when.left` includes qsX.

If that side already has a record at this height with a **different** `by`, put the new `by` on the other side of the seam instead.

Then look at that rune's `policy.extend` / `policy.contract` for a record that is already this side, this height, and this `by`. If one exists, add the family to its `family:` list in code-point order (`postscript_glyph_names.yaml`). Do not add a second record with the same shape — qsEt's two `exit: baseline, by: 1` records (qsGay / qsMay) is the split not to copy; qsJai's `left: [qsPea, qsTea]` is the fold.

A single family stays flow (`{family: qsGay}`); two or more go block. Rune YAML uses the structural style; `uv run python tools/reflow_yaml.py` on the touched rune (expect a no-op).

Do not add `self:` / `then:` / `feature:` guards unless the user scoped the change. Pair-wide means the two letters that bound the space.

## 3 — probe

`PYTHONPATH=. uv run python rebuild/tools/probe.py E6XX:E6XX` — one window per call, every acceptance config. Before the edit, capture the pair; after:

- The pair itself: the adjusted cell gains or drops `en-ext-N` / `ex-ext-N` / `en-con-N` / `ex-con-N` and the seam height is unchanged. Dropping an extend of N is the contraction — the cell loses `ex-ext-N` / `en-ext-N`; it does not pick up a contract token.
- A must-not-move neighbor on each side (a different left into Y, a different right out of X) stays byte-identical to the pre-edit probe.
- If you changed a shared list, probe a sibling still on it — it must not move.

## 4 — gates and land

just-verdicted-now-what's steps 6–7. `make test` self-skips (`glyph_data/runes/` is exempt). Don't rebuild the review surface — the user runs `make review-cycle` after the commit; the validators lane will ERROR on the sitting surface until then, which is not a defect.

UNMATCHED rows are the mid-migration steady state, not a red gate; `run_m1` exits the way its gate judges. Green is defects 0/0, Manual pins clean, `multi_matched` 0. New unmatched exemplars for this pair (`+en-con-1`, `+ex-ext-N`, position-drift) are the designed divergence. A new `E-CONTACT` / dangle in `defect_errors` needs a signature in `rebuild/m1-contact-allow.yaml` in that file's idiom; a cell variant that already has one (the `qsGay.hapax.en-y0.en-con-1` dangle from ·No) is reused, not copied.

Do not author a standing approval unless the user wants the docket to stop asking — that is dont-bug-me-about-this-ever-again.

Commit message names the letters and the look (`Contract ·Key ~b~ ·Gay by a pixel`, `Extend ·Gay ~x~ ·J'ai by another pixel`). After it lands, the user runs `make review-cycle`.
