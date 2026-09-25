# Note-taking and the rebuild logs

Git holds the history. A checked-in note is justified only when it records one of three things: what should happen next and the open decisions, a durable design fact, or the user’s rationale. It should not narrate work that is already committed. `AGENTS.md` has the short form of these rules, and this document is the full form.

## Where each kind of record goes

| Record                                   | Where it goes                                                             |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| What should happen next, open decisions  | `WHATNEXT.md`                                                             |
| A milestone’s design                     | A `*-PLAN.md` under `rebuild/`, written before the work                   |
| A durable design fact found mid-work     | The milestone’s PLAN, or `doc/rebuild-design.md`                          |
| The user’s rationale for a rune decision | The rune’s `why:` field (never written by an agent)                       |
| What a change did and why                | The commit message; multiline bodies are fine in this repository          |
| An in-flight batch’s parked state        | One bounded progress file under `rebuild/`, deleted when the batch closes |
| Evidence for a still-open decision       | `rebuild/evidence/` under the rules in its `README.md`                    |

There is no `*-REPORT.md`. When a milestone is committed, its record is the commit history plus the runes’ `why:` fields. A report that restates counts, gate tallies, and edits duplicates what git already keeps.

## The progress file

An in-flight batch keeps at most one scratch progress file. It may hold only:

- what is parked and why
- recorded design overrides
- the verification recipe
- the resume commands

It does not list the batch’s commits, because the commit that creates the file marks where the batch starts in `git log`. It does not collect per-change verification detail (row or window counts, per-configuration diffs, gate-pass tallies), because that detail belongs in the commit that made the change. When the batch closes, move any pointer to remaining work into `WHATNEXT.md` and delete the file.

## Counts and drifting state

A count in prose names the artifact that reports it, not the number. The rebuild measures itself on every cycle, so a number written into a note is out of date after the next cycle. When a fact exists both in prose and in a machine-written file, the prose copy is the one that goes stale. That state is recorded in:

- `rebuild/out/cycle_summary.json` for the last cycle’s record, and the per-gate summaries under `rebuild/out/m1/`
- `rebuild/out/review/manifest.json` for surface totals
- `rebuild/review-census-pins.json` for the last accepted census
- `make verdict-ready` for whether a sitting can start

Prefer a qualitative description (“the sweep is exact”, “the unmatched rows wait on verdicts and do not fail the gate”) to a figure. Definitional numbers are not counts and may stay: Tall/Deep/Short as 9/9/6 rows, the depth-4 chain cap, the dev-server ports, a hash that must stay byte-identical, and a fixed fact about the old shipped font. Write a commit-stamped filename with its placeholder, such as `verdicts-carried-<sha>.json`, not with the current cycle’s hash.

## Executable authorities

When a fact is recorded both in prose and in something executable, the executable version is binding and the prose says so. Examples are a field list a validator enforces (`build.check_manifest`), a gate’s list of exempt paths (`make_test_exempt`), a keyboard map the frontend binds (`keyboard.js`), and a class list a ledger holds (`rebuild/m1-divergences.yaml`). Restating one of these in a note creates a second copy that nothing checks and that goes out of date. Name the authority and describe the shape instead.

## Reconnaissance and evidence

Fact-finding done before a PLAN is written is used once. After the PLAN exists, it belongs in git history, not in a checked-in `recon/` file. The same applies to a triage, an audit, or a search for a record to change: once its conclusion is committed to the runes or a ledger, delete the dump. Evidence stays checked in only while it supports a still-open decision or is an input a tool still reads, such as the carried verdict master in `rebuild/evidence/`.

Record a rejected experiment’s findings, evidence references, and reproduction commands in a comment on its GitHub issue before deleting its checked-in PLAN. When the result decides against adoption, close the issue as not planned (WONTFIX).

## Present tense, in notes and in code

When a note has turned into a changelog, rewrite it in place to describe the current state. Do not append another dated correction. Docstrings and code comments follow the same rule: they say what the code does and why, not what it replaced. Their reader never saw the old version, and nothing checks the narration.

Signs that a note has become a changelog:

- dated `Update (…)` or `(2026-…)` stamps inside a note
- “used to”, “no longer”, “retired”, “moved here from”
- “now” paired with a past tense
- an issue number cited as a date (“since issue #78”)

When a design is justified by a measurement or by how an alternative fails, describe the alternative as an alternative (“an `Rc` per entry is four allocations behind every slot”), not as the past. When the reason is that the work is done elsewhere, name where it is done, not where it moved from. The before-and-after belongs in the commit message. A PLAN’s decision record may keep the argument that decided it, but a list of disposed items or a paragraph about what was committed inside a PLAN should be reduced to what exists, with the rest left to git history.
