# Note-taking and the rebuild logs

Git already holds the history. A checked-in note earns its place only by recording the live frontier, a durable design fact, or the user’s rationale — never a play-by-play of what already landed. `AGENTS.md` carries the short form of these rules; this document is the full form.

## Where each kind of record goes

| Record                                   | Home                                                                                     |
| ---------------------------------------- | ---------------------------------------------------------------------------------------- |
| What ought to happen next, open forks    | `WHATNEXT.md` (its “Keeping this file honest” block is the rule for editing it)          |
| A milestone’s design                     | A `*-PLAN.md` under `rebuild/`, written before the work                                  |
| A durable design fact found mid-work     | The milestone’s PLAN, or `doc/rebuild-design.md`                                         |
| The user’s rationale for a rune decision | The rune’s `why:` field (never written by an agent)                                      |
| What a change did and why                | The commit message; multiline bodies are fine in this repository                         |
| An in-flight batch’s parked state        | One bounded progress file under `rebuild/`, deleted when the batch closes                |
| Evidence for a still-open decision       | `rebuild/evidence/` under the rules in its `README.md`                                   |

There is no `*-REPORT.md`. When a milestone lands, its record is the commit history plus the runes’ `why:` fields; a report that re-narrates counts, gate tallies, and edits duplicates what git preserves.

## The progress file

An in-flight batch keeps at most one scratch progress file. It may hold only:

- what is parked and why
- recorded design overrides
- the verification recipe
- the resume commands

It never lists landed commits — the file’s own creation commit bounds the batch’s commit set in `git log` — and it never accumulates per-change verification detail (row or window counts, per-configuration diffs, gate-pass tallies), because that detail lives in the commit the change landed under. When the batch closes, lift any surviving forward pointer into `WHATNEXT.md` and delete the file.

## Counts and drifting state

A count in prose names the artifact that reports it, never the number. The rebuild measures itself on every cycle, so a tally written into a note is stale by the next one, and when a fact has both a prose copy and a machine copy it is the prose that rots. The homes for that state:

- `rebuild/out/cycle_summary.json` for the last cycle’s record, and the per-gate summaries beside it under `rebuild/out/m1/`
- `rebuild/out/review/manifest.json` for surface totals
- `rebuild/review-census-pins.json` for the last accepted census
- `make verdict-ready` for whether a sitting can start

Prefer the qualitative shape (“the sweep is exact”, “the unmatched rows are verdict-gated, not failing”) to a figure. Definitional numbers are not counts and stay: Tall/Deep/Short as 9/9/6 rows, the depth-4 chain cap, the dev-server ports, a hash that _is_ a byte-identity contract, and a frozen fact about the old shipped font. A commit-stamped filename is written with its placeholder — `verdicts-carried-<sha>.json`, not this cycle’s hash.

## Executable authorities

When a fact has both a prose home and an executable one, the executable one is binding and the prose says so. A field list a validator enforces (`build.check_manifest`), a gate’s exempt-tree list (`make_test_exempt`), a keyboard map the frontend binds (`keyboard.js`), a class list a ledger carries (`rebuild/m1-divergences.yaml`): restating any of these creates a second copy that only drifts, and the note is the copy nothing checks. Name the authority and describe the shape.

## Reconnaissance and evidence

Pre-work fact-finding that grounds a PLAN is consumed once; after the PLAN exists it belongs in git history, not a checked-in `recon/` file. The same holds for a triage, audit, or lever-hunt dump: once its conclusion has landed in the runes or a ledger and committed, delete it. Evidence stays checked in only while it is the proof pile for a still-open fork or a live build input, such as the archived surfaces `rebuild/tools/carry_verdicts.py` reads.

## Present tense, in notes and in code

A note that has drifted into a changelog is rewritten in place to its current state, never extended with another dated correction. Docstrings and code comments follow the same rule: they say what the code does and why, never what it replaced, because the reader they are written for never saw the old shape and nothing checks the narration.

The tells:

- dated `Update (…)` or `(2026-…)` stamps inside a note
- “used to”, “no longer”, “retired”, “moved here from”
- “now” paired with a past tense
- an issue number cited as a date (“since issue #78”)

When the reason for a design is a measurement or a failure mode of an alternative, describe the alternative as an alternative (“an `Rc` per entry is four allocations behind every slot”), not as the past. When the reason is that the work lives elsewhere, name where it lives, not where it moved from. The before-and-after belongs in the commit message; a PLAN’s decision record may keep the argument that settled it, but a disposal ledger or a “landed” paragraph inside one collapses to what exists and what is in git history.
