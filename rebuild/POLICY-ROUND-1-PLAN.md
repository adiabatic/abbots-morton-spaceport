# Applying a verdict round: the laws round 1 taught

Round 1 is closed. Its edits are in the runes, its per-class verdict rationale in `rebuild/m1-divergences.yaml`’s `why:` fields, its gate inventory in `M1-PLAN.md`’s gates section, and its arithmetic in the git log. What survives here is the design law the round taught: the shapes a reject may legitimately take, the two ways an audit will mislead you, and the rationale two live records lost when their `why:` fields were dropped.

## How a reject becomes a record

- **Revert the authoring decision before adding a counter-record.** When the reviewer rejects realized ink _as such_ — not one trigger of it — delete the record that draws it rather than narrowing it or netting it out. Within a migrated alphabet a narrowed record is usually byte-identical to deletion, and narrowing preserves a dead letter for the next reader to puzzle over. The netting shape is worse than inelegant: it is unavailable on the entry side, because **entry-side `extend` and `contract` do not net at name grain**. A `contract:` built to cancel an `extend:` was constructed and disproven — it left oracle rows unmatched and fired inside windows the reviewer had approved.
- **Express a revert as a yielding `prefer`, never a `refuse` and never `mode: absolute`.** A yielding prefer sits at the tier between window join-count and the structural floor, so it flips only structural-floor ties and leaves every strict-gain join standing. A `refuse` in the same position judges the join in front of it unconditionally, and overreaches onto windows the reviewer approved.
- **Scope a same-rune pairing record on `left:`.** A follower-side record is a no-op between same-rune neighbors, so the obvious `right:`-only one-liner cannot do the job — it reaches the follower vote instead and poisons unrelated ·X·May·May-shaped windows, approved ones among them. A `left:` condition that can only match its own family is invisible to the follower path, which is why a pairing record carries one.
- **When the verdict signal is internally contradictory** — the same cell approved in one window and rejected in another — surface the question instead of editing. A record built to honor one side of a contradiction repeals a law the other side depends on.
- **Some rejects are engine-limited, not policy-limited.** “The predecessor’s predecessor is entered” is inexpressible in the closed `when:` vocabulary: `left:` summarizes only the immediate predecessor’s settled state, and there is no `left2`. A window whose only remaining flaw needs that predicate is documented residue until the vocabulary grows — not a record waiting to be written.

## Two ways the audit will mislead you

- **Audit-invisibility is not redundancy.** The acceptance oracle’s window universe tops out at four letters, so a record whose real load is longer chains leaves the divergence audit byte-identical when it is removed. Never delete a record because removal-and-rerun shows no diff; work out what it does past the horizon and pin that length by hand in `rebuild/test_settle.py`, which is the only gate such a record has.
- **Unit ids are positional over the audit** and shift whenever windows re-converge, reclassify, or newly appear. Never carry a `u-NNNN` id as identity, and never cite one as durable provenance unless a rune’s `why:` names it. Carry verdicts on stable window identity instead — the `content_key` shape in `rebuild/tools/carry_verdicts.py` — and account for every verdict as carried, resolved-by-revert, or queued for re-presentation; none may be dropped without landing in one of those.

## Two invariants an apply phase must not break

- **The run never writes the ledger file.** Counts in `rebuild/m1-divergences.yaml` are filled from the conformance run and reviewed as a diff. That ledger’s own header comment is the enforced statement of this, and of the status vocabulary and the partition rule alongside it.
- **A round never fabricates a verdict.** Unverdicted units stay unverdicted, and a skip yields a proposal the user rules on — never a recorded verdict.

## The rationale two qsMay records lost

`qsMay.policy.prefer[1]` (word start) and `[2]` (chain interior) are the greedy ·May·May pairing this round authored, and both are live. Their `why:` fields were removed in `875b44c`, so the block below — reproduced exactly as round 1 wrote it — is the only surviving statement of why those two records exist, including the warning against deleting the chain-interior one as redundant. Restoring the texts to the rune is the user’s call, a `why:` being the user’s own voice; until that happens this block is not a copy that may be trimmed.

```yaml
  prefer:
  - {cell: {exit: baseline}, when: {left: {is: boundary}, right: {family: qsMay}}, why: 'Round-1 verdict on ·May·May·May·May — "the old way seems nicer to write out by hand": at word start, pair up — the grounded baseline join into the next ·May beats declining when the join counts tie.'}
  - {cell: {exit: baseline}, when: {left: {family: qsMay, joined_at: none}, right: {family: qsMay}}, why: 'Chain interior of the same verdict ("the old way seems nicer to write out by hand"): after an unjoined ·May, pair with the next ·May. The acceptance oracle''s window universe tops out at four letters, where the word-start record alone already reproduces every outcome (including the u-0341 quad flip), so this record is invisible to the divergence audit — do not delete it as redundant. Its real load is ·May chains of five or more, which without it regress to the rejected defer-to-the-tail grouping instead of the old greedy y0 | break | y0 | break pairing the shipped font draws at every length; the length-5 and length-6 settle assertions in rebuild/test_settle.py pin it. Like the word-start record, its left: condition can never match a non-·May predecessor seam.'}
```
