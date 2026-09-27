---
name: expand-or-contract-a-join
description: Lengthen or shorten one already-joining rebuild pair by N pixels. Pick one side, add the family to an existing extend or contract list with the same `by` when one matches, probe, and run the gates. Use when the user asks to extend, contract, lengthen, shorten, or add a pixel to a ·X·Y join, or runs /expand-or-contract-a-join. Assume the request is for the rebuild.
argument-hint: "[·X·Y] [by N] [baseline|x-height]"
---

This skill makes a pixel change on one rebuild pair that already joins. The record goes on a rune under `glyph_data/runes/`, not in `glyph_data/quikscript.yaml`. That file's `derive.extend_*` / `contract_*` directives belong to the old font, and the tweak-an-old-font-join skill covers them. Opening a join that currently breaks is a different task.

`rebuild/schema/rune.schema.json` (`extendRecord`, `contractRecord`) defines the record shape. Never write `why:` (AGENTS.md). If the named family has more than one variant on the relevant axis, stop and ask before editing (AGENTS.md's letter-name rule).

## 1 — identify the pair, height, and amount

- In the pair `·X·Y`, ·X is the left letter and ·Y the right. The height is probably `baseline` or `x-height`; a probe of the pair shows the current junction. The amount defaults to 1.
- Look up the codepoints in `doc/glyph-names.md`. Confirm that both runes already list each other at that height (`toward:` / `from:`).

## 2 — look for an opposing adjustment first, then pick one side and add to an existing record

An extend on one side of a junction and a contract on the other both apply; they do not cancel out. Before adding a contract, search **both** runes' `policy.extend` for a record that already covers this pair at this height with the same `feature:` / `self:` / `then:` guards the user scoped (pair-wide means no extra guards). If one exists, reduce that extend by N. Either drop the family from its list (delete the record when the list becomes empty, and write a single remaining family back in flow style, `{family: qsGay}`), or, if its `by` is larger than N, lower `by`. Author a new contract only for any amount left over. Do the same in reverse when the user asks to extend and a covering contract already exists.

When adding, use the side the user names ("·Key's foot", "like the other ·Key contractions"). Otherwise:

- **Extend `·X·Y`:** qsX, `exit: <height>`, `when.right` includes qsY.
- **Contract `·X·Y`:** qsY, `entry: <height>`, `when.left` includes qsX.

If that side already has a record at this height with a **different** `by`, put the new `by` on the other side of the junction instead.

Then look in that rune's `policy.extend` / `policy.contract` for a record with this side, this height, and this `by`. If one exists, add the family to its `family:` list in code-point order (`postscript_glyph_names.yaml`). Do not add a second record with the same shape. qsEt's two `exit: baseline, by: 1` contract records (one for qsGay, one for qsMay) are the split not to copy; qsJai's contract record with `left: [qsPea, qsTea]` is the combined form to follow.

A single family stays in flow style (`{family: qsGay}`), and two or more go in block style. Rune YAML follows the structural rule; run `uv run python tools/reflow_yaml.py` on the edited rune and expect a no-op.

Do not add `self:` / `then:` / `feature:` guards unless the user scoped the change. Pair-wide means the two letters that bound the space decide the change, and AGENTS.md makes that the rule: no word-initial, word-final, or isolated treatment unless The Manual mandates it. When a `bind:` cannot coexist with a cell (its drawing would overwrite an onward exit's connector), keep the unbound amount on a separate record so the extension itself stays pair-wide, and guard only the bound record.

## 3 — probe

`uv run python rebuild/tools/probe.py E6XX:E6XX E6XX:E6XX …` takes every window in one call, renders every acceptance config, and prints one block per window in argument order. Before the edit, capture the pair and the neighbors that must not change in one invocation, written to a file. After the edit, run the same invocation and diff:

- The pair itself: the adjusted cell gains or drops `en-ext-N` / `ex-ext-N` / `en-con-N` / `ex-con-N`, and the junction height is unchanged. When the contraction is made by dropping an extend of N, the cell loses `ex-ext-N` / `en-ext-N` and does not gain a contract token.
- A neighbor that must not change, on each side (a different left into Y, a different right out of X), stays byte-identical to the pre-edit probe.
- If you changed a shared list, probe another family still on it. Its output must not change.

## 4 — gates and commit

Follow just-verdicted-now-what's steps 6–7. `make test` skips itself (`glyph_data/runes/` is exempt). Don't rebuild the review corpus; the user runs `make review-cycle` after the commit.

UNMATCHED rows are expected while the migration is in progress and do not fail the gate; `run_m1` exits with its gate's result. Green means defects 0/0, Manual pins clean, and `multi_matched` 0. New unmatched exemplars for this pair (`+en-con-1`, `+ex-ext-N`, position-mismatch) are the designed divergence. A new `E-CONTACT` / dangle in `defect_errors` needs a signature in `rebuild/m1-contact-allow.yaml` in the pattern of that file's existing signatures. When a cell variant already has one (the `qsGay.sole.en-y0.en-con-1` dangle from ·No), reuse that signature instead of copying it.

Do not author a standing approval unless the user wants the review queue to stop asking. That is dont-bug-me-about-this-ever-again's job.

The commit message names the letters and how the join looks (`Contract ·Key ~b~ ·Gay by a pixel`, `Extend ·Gay ~x~ ·J'ai by another pixel`). After it is committed, the user runs `make review-cycle`.
