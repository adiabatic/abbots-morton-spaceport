# Applying a verdict round: the laws round 1 taught

Round 1 is closed. Its edits are in the runes, its per-class rationale in the `why:` fields of `rebuild/m1-divergences.yaml`, and its gate inventory in the gates section of `M1-PLAN.md`. This file keeps the design rules the round produced: which record shapes a reject can take, two ways the audit misleads, and two invariants a round must not break.

## How a reject becomes a record

- **Revert the authoring decision before adding a counter-record.** When the reviewer rejects the ink itself, not one trigger of it, delete the record that draws it. Don't narrow it or cancel it with another record. Within a migrated alphabet a narrowed record is usually byte-identical to deletion, and it leaves a record that does nothing for the next reader to puzzle over. Cancelling does not work on the entry side at all, because **entry-side `extend` and `contract` do not cancel at name grain**: a `contract:` built to cancel an `extend:` left oracle rows unmatched and fired inside windows the reviewer had approved.
- **Express a revert as a yielding `prefer`, not a `refuse` and not `mode: absolute`.** Yielding prefers rank after window join-count and before the structural floor, so one changes only the result of a floor tie and leaves every join that wins on join-count in place. A `refuse` in the same position removes the join unconditionally and reaches windows the reviewer approved.
- **Scope a same-rune pairing record on `left:`.** A follower-side record does nothing between same-rune neighbors, so the obvious `right:`-only one-liner cannot do the job. It acts through the follower vote instead and changes unrelated ·X·May·May-shaped windows, approved ones among them. A `left:` condition that can only match its own family is not seen by the follower path, so a pairing record carries one.
- **When the verdicts contradict each other**, with the same cell approved in one window and rejected in another, ask the user instead of editing. A record that satisfies one side undoes a rule the other side depends on.
- **Some rejects cannot be expressed in policy.** “The predecessor's predecessor is entered” has no form in the closed `when:` vocabulary: `left:` describes only the immediate predecessor's settled state, and there is no `left2`. A window whose only remaining defect needs that condition stays documented as one of the remaining cases until the vocabulary grows.

## Two ways the audit will mislead you

- **A record the audit cannot see is not redundant.** The acceptance oracle's windows are at most four letters long, so removing a record whose effect is on longer sequences leaves the divergence audit byte-identical. Never delete a record because removing it and rerunning shows no diff. Work out what it does beyond four letters and pin that length by hand in `rebuild/test_settle.py`, which is the only test of that behavior.
- **A unit id changes when the unit's content changes.** The id is derived from the unit's content key (`unit_cache.unit_id_for`), which covers its before and after outcomes, so any change to a window's outcome gives it a new id. Don't cite a `u-` id as durable provenance unless a rune's `why:` names it. The carry (`rebuild/tools/carry_verdicts.py`) moves a verdict to the unit with the same id and reports the rest as stranded on its `carry figures:` line. Account for every stranded verdict as resolved by a revert or queued to be shown again; none may be dropped.

## Two invariants an apply phase must not break

- **The run never writes the ledger file.** Counts in `rebuild/m1-divergences.yaml` are filled from the conformance run and reviewed as a diff. The ledger's header comment states this rule, the status vocabulary, and the partition rule.
- **A round never invents a verdict.** Units without a verdict stay without one, and a skip produces a proposal the user decides on, never a recorded verdict.
